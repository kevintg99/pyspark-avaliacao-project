"""Escolha final de cada shot: baixa só os vencedores (+ reserva), valida e aplica planos B."""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, field
from pathlib import Path

from .config import AppConfig
from .errors import ProviderError
from .framing import choose_layout
from .log import get_logger
from .media import FFmpegError, probe
from .models import Candidate, MediaType, Shot
from .providers.base import BaseProvider
from .ranking import Ranker
from .search import SceneSearchResult

log = get_logger("selection")


def file_fingerprint(path: Path) -> str:
    """Hash rápido (tamanho + primeiros 4 MB) para deduplicar downloads."""
    h = hashlib.sha1(str(path.stat().st_size).encode())
    with open(path, "rb") as fh:
        h.update(fh.read(4 * 1024 * 1024))
    return h.hexdigest()


@dataclass
class Selector:
    cfg: AppConfig
    providers: dict[str, BaseProvider]
    ranker: Ranker
    media_dir: Path
    seed: int
    used_keys: set[str] = field(default_factory=set)
    used_hashes: dict[str, str] = field(default_factory=dict)
    recent: list[Candidate] = field(default_factory=list)
    reserves: dict[int, Candidate] = field(default_factory=dict)
    failed_keys: set[str] = field(default_factory=set)
    path_map: dict[str, Path] = field(default_factory=dict)
    downloads: int = 0
    prev_layout: str | None = None

    # --------------------------------------------------------- download
    def fetch(self, cand: Candidate) -> Path | None:
        """Baixa (ou reaproveita), valida com ffprobe e deduplica por hash."""
        if cand.key in self.failed_keys:
            return None
        provider = self.providers.get(cand.provider)
        if provider is None:
            return None
        try:
            path = provider.download(cand, self.media_dir)
            info = probe(path)
        except (ProviderError, FFmpegError, OSError) as exc:
            self.failed_keys.add(cand.key)
            log.warning("mídia descartada", extra={"stage": "download", "candidate": cand.key, "error": str(exc)})
            return None
        if cand.media_type is MediaType.VIDEO:
            cand.duration = info.duration
        cand.width, cand.height = info.width, info.height
        fp = file_fingerprint(path)
        owner = self.used_hashes.get(fp)
        if owner and owner != cand.key:
            self.failed_keys.add(cand.key)
            log.info("arquivo duplicado ignorado", extra={"stage": "download", "candidate": cand.key, "same_as": owner})
            return None
        self.used_hashes[fp] = cand.key
        self.downloads += provider.is_remote
        return path

    # ---------------------------------------------------------- escolha
    def _acceptable(self, cand: Candidate, min_score: float | None) -> bool:
        if cand.key in self.used_keys or cand.key in self.failed_keys:
            return False
        if min_score is not None and cand.score < min_score:
            return False
        return not any(self.ranker.too_similar(cand, prev) for prev in self.recent[-3:])

    def _pick(self, pools: list[list[Candidate]], prefer: MediaType, min_score: float | None) -> tuple[Candidate, Path] | None:
        for pool in pools:
            ordered = sorted(pool, key=lambda c: (c.media_type is not prefer, -c.score))
            for cand in ordered:
                if not self._acceptable(cand, min_score):
                    continue
                path = self.fetch(cand)
                if path is not None:
                    return cand, path
        return None

    def assign(self, shots: list[Shot], results: dict[int, SceneSearchResult]) -> None:
        min_score = self.cfg.search.min_score
        for shot in shots:
            own = results.get(shot.scene_index, SceneSearchResult(shot.scene_index)).ranked
            neighbors = [results[i].ranked for i in (shot.scene_index - 1, shot.scene_index + 1) if i in results]
            choice = self._pick([own], shot.prefer_media, min_score)
            reason = None
            if choice is None:
                # plano B: candidatos abaixo do score mínimo da cena, depois das cenas vizinhas
                choice = self._pick([own], shot.prefer_media, None) or self._pick(neighbors, shot.prefer_media, min_score)
                reason = "below_min_score_or_neighbor" if choice else None
            if choice is None:
                reuse = self._reuse_candidate(own)
                if reuse is not None:
                    choice = (reuse, self.path_map[reuse.key])
                    reason = "reused_with_new_framing"
            if choice is None:
                shot.fallback_reason = "generated_background"
                shot.layout = {"name": "generated"}
                log.warning("sem mídia para o shot; usando fundo gerado", extra={"stage": "selection", "shot": shot.index})
                continue
            cand, path = choice
            if self.reserves.get(shot.scene_index) is cand:
                del self.reserves[shot.scene_index]
            self.apply(shot, cand, path, reason)

            # reserva: garante 1 candidato extra baixado por cena
            if shot.scene_index not in self.reserves and self.cfg.search.reserve_per_scene > 0:
                self._download_reserve(shot.scene_index, own)

    def apply(self, shot: Shot, cand: Candidate, path: Path, reason: str | None) -> None:
        """Associa a mídia ao shot e sorteia enquadramento e ponto de entrada."""
        rng = random.Random(self.seed * 100003 + shot.index)
        self._register(cand, path)
        shot.candidate, shot.media_path, shot.fallback_reason = cand, str(path), reason
        shot.layout = choose_layout(cand.media_type, cand.aspect, self.cfg.timeline.video_layouts,
                                    self.cfg.timeline.image_layouts, rng, self.prev_layout)
        self.prev_layout = shot.layout["name"]
        self._set_offset(shot, rng)

    def _reuse_candidate(self, own: list[Candidate]) -> Candidate | None:
        """Último plano B com mídia real: o clipe já baixado mais relevante para a
        cena (com outro enquadramento e ponto de entrada), evitando repetir o anterior."""
        last = self.recent[-1].key if self.recent else None
        options = [c for c in sorted(own, key=lambda c: -c.score) if c.key in self.path_map]
        options += [c for c in reversed(self.recent) if c.key in self.path_map]
        return next((c for c in options if c.key != last), options[0] if options else None)

    def _register(self, cand: Candidate, path: Path) -> None:
        self.path_map[cand.key] = path
        self.used_keys.add(cand.key)
        self.recent.append(cand)

    def _download_reserve(self, scene_index: int, pool: list[Candidate]) -> None:
        for cand in sorted(pool, key=lambda c: -c.score):
            if cand.key in self.used_keys or cand.key in self.failed_keys:
                continue
            if cand.score < self.cfg.search.min_score:
                break
            if not any(self.ranker.too_similar(cand, prev) for prev in self.recent[-3:]) and self.fetch(cand):
                self.reserves[scene_index] = cand
                return

    def take_reserve(self, scene_index: int) -> tuple[Candidate, Path] | None:
        """Usado quando a renderização de um shot falha."""
        cand = self.reserves.pop(scene_index, None)
        if cand is None or cand.key in self.used_keys:
            return None
        path = self.fetch(cand)
        return (cand, path) if path else None

    def _set_offset(self, shot: Shot, rng: random.Random) -> None:
        """Ponto de entrada variado no vídeo (não começa sempre no 0s)."""
        cand = shot.candidate
        if cand is None or cand.media_type is MediaType.IMAGE:
            return
        fps = self.cfg.output.fps
        needed = shot.frames / fps
        src = cand.duration or 0.0
        if src >= needed + 0.2:
            slack = src - needed - 0.1
            shot.source_offset = round(rng.uniform(min(0.25, slack), slack), 3)
            shot.speed = 1.0
        elif src * 1.6 >= needed and src > 0:
            # clipe curto: câmera lenta leve em vez de repetir o trecho
            shot.speed = round(min(1.6, needed / src * 1.02), 4)
            shot.source_offset = 0.0
        else:
            shot.speed = 1.0
            shot.source_offset = 0.0  # o assembler faz loop
        if shot.fallback_reason == "reused_with_new_framing" and src > needed:
            shot.source_offset = round(rng.uniform(0, max(0.0, src - needed - 0.05)), 3)
