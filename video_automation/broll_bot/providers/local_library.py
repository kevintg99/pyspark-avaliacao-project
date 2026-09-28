"""Biblioteca local (ex.: clipes do Mixkit baixados manualmente).

O Mixkit não tem API pública e os termos não permitem scraping, então os
clipes são baixados manualmente para uma pasta. Este módulo indexa essa pasta
(nome, tags, duração, resolução e thumbnail) e expõe a mesma interface das
fontes remotas. Embeddings visuais são calculados e cacheados pelo ranking.

Metadados extras opcionais: um arquivo ``<nome>.json`` ao lado do clipe com
``{"tags": [...], "description": "...", "page_url": "..."}``.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Callable

from ..log import get_logger
from ..media import IMAGE_EXTS, VIDEO_EXTS, FFmpegError, extract_frame, probe
from ..models import Candidate, MediaType
from ..text import FILENAME_NOISE, keywords, overlap_score
from .base import BaseProvider, ProviderError

log = get_logger("library")

INDEX_VERSION = 2

SemanticFn = Callable[[str, list[Candidate]], list[float]]


def tags_from_filename(stem: str) -> list[str]:
    words = re.split(r"[^a-zA-Z]+", stem.lower())
    return [w for w in words if len(w) > 1 and w not in FILENAME_NOISE]


class LocalLibraryProvider(BaseProvider):
    name = "mixkit_local"
    is_remote = False
    supported_media = (MediaType.VIDEO, MediaType.IMAGE)

    def __init__(self, root: Path, index_path: Path, name: str = "mixkit_local",
                 semantic: SemanticFn | None = None, min_semantic: float = 0.35) -> None:
        self.root = root
        self.index_path = index_path
        self.name = name  # type: ignore[misc]
        self.semantic = semantic
        self.min_semantic = min_semantic
        self._entries: list[Candidate] | None = None

    def available(self) -> bool:
        return self.root.is_dir() and bool(self.entries)

    # ---------------------------------------------------------------- index
    @property
    def entries(self) -> list[Candidate]:
        if self._entries is None:
            self._entries = self.build_index()
        return self._entries

    def reindex(self) -> None:
        self._entries = self.build_index(force=True)

    def build_index(self, force: bool = False) -> list[Candidate]:
        if not self.root.is_dir():
            return []
        old: dict[str, dict] = {}
        if self.index_path.exists() and not force:
            try:
                data = json.loads(self.index_path.read_text(encoding="utf-8"))
                if data.get("version") == INDEX_VERSION:
                    old = {e["local_path"]: e for e in data.get("entries", [])}
            except (OSError, ValueError):
                old = {}

        entries: list[dict] = []
        thumbs_dir = self.index_path.parent / "thumbs" / self.name
        for path in sorted(self.root.rglob("*")):
            ext = path.suffix.lower()
            if ext not in VIDEO_EXTS | IMAGE_EXTS or not path.is_file():
                continue
            stat = path.stat()
            cached = old.get(str(path))
            if cached and cached.get("_mtime") == stat.st_mtime and cached.get("_size") == stat.st_size:
                entries.append(cached)
                continue
            try:
                info = probe(path)
            except FFmpegError as exc:
                log.warning("arquivo ignorado na biblioteca", extra={"stage": "library", "file": path.name, "error": str(exc)})
                continue
            is_image = ext in IMAGE_EXTS
            tags = tags_from_filename(path.stem)
            description = " ".join(tags)
            page_url = ""
            sidecar = path.with_suffix(".json")
            if sidecar.exists():
                try:
                    meta = json.loads(sidecar.read_text(encoding="utf-8"))
                    tags = list(dict.fromkeys([*meta.get("tags", []), *tags]))
                    description = meta.get("description", description)
                    page_url = meta.get("page_url", "")
                except (OSError, ValueError):
                    pass
            thumb = path if is_image else thumbs_dir / f"{hashlib.sha1(str(path).encode()).hexdigest()[:16]}.jpg"
            if not is_image:
                try:
                    extract_frame(path, thumb, at=max(0.0, info.duration * 0.35))
                except FFmpegError:
                    thumb = None  # type: ignore[assignment]
            cand = Candidate(
                provider=self.name,
                media_id=str(path.relative_to(self.root)),
                media_type=MediaType.IMAGE if is_image else MediaType.VIDEO,
                width=info.width,
                height=info.height,
                duration=None if is_image else info.duration,
                thumbnail_url=None,
                download_url=None,
                page_url=page_url,
                tags=tags,
                description=description,
                local_path=str(path),
                thumbnail_path=str(thumb) if thumb else None,
            )
            record = cand.to_dict()
            record.update({"thumbnail_path": cand.thumbnail_path, "_mtime": stat.st_mtime, "_size": stat.st_size})
            entries.append(record)

        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        self.index_path.write_text(json.dumps({"version": INDEX_VERSION, "entries": entries}, ensure_ascii=False, indent=1),
                                   encoding="utf-8")
        result = []
        for record in entries:
            cand = Candidate.from_dict(record)
            cand.thumbnail_path = record.get("thumbnail_path")
            result.append(cand)
        log.info("biblioteca local indexada", extra={"stage": "library", "source": self.name, "items": len(result)})
        return result

    # --------------------------------------------------------------- search
    def search(self, query: str, media_type: MediaType, limit: int, language: str = "en") -> list[Candidate]:
        pool = [c for c in self.entries if c.media_type is media_type]
        if not pool:
            return []
        terms = keywords(query)
        lexical = [overlap_score(terms, keywords(" ".join(c.tags) + " " + c.description)) for c in pool]
        semantic = [0.0] * len(pool)
        if self.semantic is not None:
            try:
                semantic = self.semantic(query, pool)
            except Exception as exc:  # noqa: BLE001 - busca local nunca derruba o pipeline
                log.warning("busca semântica local falhou", extra={"stage": "library", "error": str(exc)})
        scored = []
        for cand, lex, sem in zip(pool, lexical, semantic):
            if lex <= 0 and sem < self.min_semantic:
                continue
            scored.append((0.5 * lex + sem, cand))
        scored.sort(key=lambda item: item[0], reverse=True)
        out = []
        for _, cand in scored[:limit]:
            copy = Candidate.from_dict(cand.to_dict())
            copy.thumbnail_path = cand.thumbnail_path
            copy.query = query
            out.append(copy)
        return out

    def download(self, candidate: Candidate, dest_dir: Path) -> Path:
        if not candidate.local_path or not Path(candidate.local_path).exists():
            raise ProviderError(f"arquivo local sumiu: {candidate.local_path}")
        return Path(candidate.local_path)
