"""Orquestra o fluxo completo: áudio + roteiro -> video_final.mp4 + report.json."""

from __future__ import annotations

import json
import random
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .assembler import Assembler
from .cache import SQLiteCache
from .config import AppConfig
from .log import get_logger
from .media import FFmpegError, audio_duration, probe, require_ffmpeg, supported_xfade_transitions
from .models import MediaType, Scene, Shot
from .providers import LocalLibraryProvider, build_providers
from .ranking import Ranker, ThumbnailFetcher, load_embedder
from .scenes import HeuristicSceneAnalyzer, build_analyzer, merge_short_scenes
from .search import BrollSearcher, SceneSearchResult
from .selection import Selector
from .timeline import plan_timeline
from .transcription import align, parse_script, transcribe

log = get_logger("pipeline")


@dataclass
class RunOptions:
    audio: Path
    script: Path
    output: Path
    base_dir: Path
    reindex_library: bool = False
    report_path: Path | None = None


def run_pipeline(cfg: AppConfig, opts: RunOptions, work_dir: Path) -> dict[str, Any]:
    t0 = time.monotonic()
    require_ffmpeg()
    cache_dir = opts.base_dir / cfg.paths.cache_dir
    media_dir = cache_dir / "media"
    cache = SQLiteCache(cache_dir / "cache.sqlite")
    cache.purge_expired()
    rng = random.Random(cfg.seed)

    # 1) áudio, roteiro e sincronização -------------------------------------
    audio_seconds = audio_duration(opts.audio)
    sentences = parse_script(opts.script.read_text(encoding="utf-8"))
    if not sentences:
        raise ValueError("roteiro vazio ou sem frases reconhecíveis")
    log.info("entrada carregada", extra={"stage": "input", "audio_s": round(audio_seconds, 3), "sentences": len(sentences)})
    words = transcribe(opts.audio, cfg.transcription, cache)
    align(sentences, words, audio_seconds)

    # 2) cenas semânticas ----------------------------------------------------
    analyzer = build_analyzer(cfg.analysis, cache)
    analysis_mode = type(analyzer).__name__
    try:
        scenes = analyzer.analyze(sentences, audio_seconds)
    except Exception as exc:  # noqa: BLE001 - LLM fora do ar não para o vídeo
        log.warning("análise com LLM falhou; usando heurística", extra={"stage": "scenes", "error": str(exc)})
        analysis_mode = "HeuristicSceneAnalyzer (fallback)"
        scenes = HeuristicSceneAnalyzer(cfg.analysis).analyze(sentences, audio_seconds)
    scenes = merge_short_scenes(scenes, min_seconds=1.0)

    # 3) linha do tempo ----------------------------------------------------
    supported = supported_xfade_transitions()
    transitions = [t for t in cfg.timeline.transitions if not supported or t in supported]
    shots = plan_timeline(scenes, words, audio_seconds, cfg.timeline, cfg.output.fps, rng, transitions,
                          image_ratio=cfg.search.image_ratio)
    log.info("linha do tempo planejada", extra={
        "stage": "timeline", "scenes": len(scenes), "shots": len(shots),
        "max_clip_s": max(s.clip_duration for s in shots), "min_clip_s": min(s.clip_duration for s in shots),
    })

    # 4) busca + seleção (cena a cena, para evitar repetição) ----------------
    embedder = load_embedder(cfg, cache_dir)
    ranker = Ranker(cfg, embedder, ThumbnailFetcher(cache_dir))
    providers = build_providers(cfg, opts.base_dir, semantic=ranker.text_image_scores if embedder else None)
    if opts.reindex_library:
        for p in providers.values():
            if isinstance(p, LocalLibraryProvider):
                p.reindex()
    searcher = BrollSearcher(cfg, providers, ranker, cache, rng)
    selector = Selector(cfg, providers, ranker, media_dir, cfg.seed)
    results: dict[int, SceneSearchResult] = {}
    for scene in scenes:
        scene_shots = [s for s in shots if s.scene_index == scene.index]
        n_images = sum(s.prefer_media is MediaType.IMAGE for s in scene_shots)
        results[scene.index] = searcher.search_scene(scene, len(scene_shots), n_images, selector.used_keys)
        selector.assign(scene_shots, results)

    # 5) render -------------------------------------------------------------
    assembler = Assembler(cfg, work_dir / "render")
    clip_paths = _render_shots(assembler, selector, shots, cfg.render.workers)
    opts.output.parent.mkdir(parents=True, exist_ok=True)
    assembler.assemble(shots, clip_paths, opts.audio, opts.output, audio_seconds)

    final = probe(opts.output)
    drift = abs(final.duration - audio_seconds)
    log.info("vídeo final pronto", extra={"stage": "done", "output": str(opts.output),
                                          "duration_s": round(final.duration, 3), "drift_s": round(drift, 3)})

    report = build_report(cfg, opts, scenes, shots, results, providers, analysis_mode, audio_seconds,
                          final.duration, time.monotonic() - t0, selector.downloads)
    report_path = opts.report_path or opts.output.with_name("report.json")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if not cfg.render.keep_intermediates:
        shutil.rmtree(work_dir / "render", ignore_errors=True)
    cache.close()
    return report


def _render_shots(assembler: Assembler, selector: Selector, shots: list[Shot], workers: int) -> list[Path]:
    render_dir = assembler.work_dir
    lock = threading.Lock()

    def one(shot: Shot) -> Path:
        dest = render_dir / f"shot_{shot.index:04d}.mp4"
        try:
            return assembler.render_shot(shot, dest)
        except FFmpegError as exc:
            log.warning("falha ao renderizar shot; tentando reserva", extra={"stage": "render", "shot": shot.index, "error": str(exc)[-300:]})
        with lock:
            reserve = selector.take_reserve(shot.scene_index)
            if reserve is not None:
                selector.apply(shot, reserve[0], reserve[1], "render_failed_used_reserve")
        if reserve is not None:
            try:
                return assembler.render_shot(shot, dest)
            except FFmpegError:
                pass
        shot.fallback_reason = "render_failed_generated_background"
        shot.candidate, shot.media_path = None, None
        return assembler.render_generated(shot, dest)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        paths = list(pool.map(one, shots))
    log.info("shots renderizados", extra={"stage": "render", "count": len(paths)})
    return paths


def build_report(cfg: AppConfig, opts: RunOptions, scenes: list[Scene], shots: list[Shot],
                 results: dict[int, SceneSearchResult], providers: dict, analysis_mode: str,
                 audio_seconds: float, video_seconds: float, elapsed: float, downloads: int) -> dict[str, Any]:
    scene_items = []
    for scene in scenes:
        res = results.get(scene.index, SceneSearchResult(scene.index))
        scene_items.append({
            "scene": scene.index,
            "start": round(scene.start, 3),
            "end": round(scene.end, 3),
            "narrated_text": scene.text,
            "visual_summary": scene.analysis.visual_summary,
            "queries": scene.analysis.queries,
            "fallback_query": scene.analysis.fallback_query,
            "tone": scene.analysis.tone,
            "api_calls": res.budget_used,
            "search_attempts": res.attempts,
            "shots": [_shot_item(s) for s in shots if s.scene_index == scene.index],
        })
    credits = sorted({(s.candidate.provider, s.candidate.author, s.candidate.page_url)
                      for s in shots if s.candidate and s.candidate.page_url})
    return {
        "input": {"audio": str(opts.audio), "script": str(opts.script)},
        "output": str(opts.output),
        "seed": cfg.seed,
        "analysis_mode": analysis_mode,
        "audio_duration": round(audio_seconds, 3),
        "video_duration": round(video_seconds, 3),
        "elapsed_seconds": round(elapsed, 1),
        "totals": {
            "scenes": len(scenes),
            "shots": len(shots),
            "remote_downloads": downloads,
            "api_requests": {name: p.request_count() for name, p in providers.items()},
            "fallbacks": sum(1 for s in shots if s.fallback_reason),
            "max_clip_seconds": max(s.clip_duration for s in shots),
        },
        "scenes": scene_items,
        "credits": [{"source": p, "author": a, "url": u} for p, a, u in credits],
    }


def _shot_item(s: Shot) -> dict[str, Any]:
    c = s.candidate
    return {
        "shot": s.index,
        "cut_in": s.cut_in,
        "cut_out": s.cut_out,
        "applied_duration": s.clip_duration,
        "transition_in": {"type": s.transition_in.kind, "duration": s.transition_in.duration},
        "source": c.provider if c else None,
        "media_type": c.media_type.value if c else None,
        "media_id": c.media_id if c else None,
        "page_url": c.page_url if c else None,
        "query": c.query if c else None,
        "score": c.score if c else None,
        "semantic": c.semantic if c else None,
        "layout": s.layout,
        "source_offset": s.source_offset,
        "speed": s.speed,
        "fallback": s.fallback_reason,
    }
