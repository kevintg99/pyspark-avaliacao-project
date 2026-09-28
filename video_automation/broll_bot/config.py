"""Carregamento de configuração (config.yaml + .env)."""

from __future__ import annotations

import dataclasses
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

import yaml

try:  # python-dotenv é opcional em tempo de teste
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None  # type: ignore[assignment]


@dataclass
class OutputConfig:
    width: int = 1920
    height: int = 1080
    fps: int = 30
    crf: int = 20
    preset: str = "medium"
    audio_bitrate: str = "192k"


@dataclass
class PathsConfig:
    work_dir: str = ".work"
    cache_dir: str = ".cache"
    local_library: str = "library/mixkit"


@dataclass
class TranscriptionConfig:
    enabled: bool = True
    model: str = "small"
    device: str = "auto"
    compute_type: str = "default"
    language: str | None = "pt"


@dataclass
class AnalysisConfig:
    provider: str = "anthropic"  # "anthropic" | "heuristic"
    model: str = "claude-opus-5-5"
    effort: str = "medium"
    max_tokens: int = 16000
    target_scene_seconds: float = 12.0
    min_scene_seconds: float = 3.0
    max_scene_seconds: float = 25.0
    script_language: str = "pt"


@dataclass
class CircuitBreakerConfig:
    failure_threshold: int = 3
    cooldown_seconds: float = 300.0


@dataclass
class SearchConfig:
    provider_order: list[str] = field(default_factory=lambda: ["mixkit_local", "pexels", "pixabay"])
    max_searches_per_scene: int = 3
    local_counts_toward_budget: bool = False
    per_page: int = 15
    min_score: float = 0.45
    reserve_per_scene: int = 1
    image_ratio: float = 0.2
    min_width: int = 1280
    orientation: str = "landscape"
    cache_ttl_hours: float = 24.0
    max_rank_candidates: int = 16
    similarity_threshold: float = 0.93
    circuit_breaker: CircuitBreakerConfig = field(default_factory=CircuitBreakerConfig)


@dataclass
class ProviderConfig:
    enabled: bool = True
    requests_per_minute: float = 60.0
    burst: int = 5
    max_wait_seconds: float = 20.0
    max_retries: int = 3
    timeout_seconds: float = 20.0
    locale: str = "en-US"


@dataclass
class RankingWeights:
    semantic: float = 0.65
    quality: float = 0.12
    duration: float = 0.1
    orientation: float = 0.08
    text_penalty: float = 0.25
    negative_penalty: float = 0.3


@dataclass
class RankingConfig:
    use_clip: bool = True
    clip_model: str = "ViT-B-32"
    clip_pretrained: str = "laion2b_s34b_b79k"
    device: str = "auto"
    weights: RankingWeights = field(default_factory=RankingWeights)


@dataclass
class TimelineConfig:
    min_clip_seconds: float = 2.2
    max_clip_seconds: float = 7.0
    snap_to_words: bool = True
    snap_tolerance: float = 0.6
    hard_cut_probability_in_scene: float = 0.35
    hard_cut_probability_scene_change: float = 0.1
    transition_min: float = 0.2
    transition_max: float = 0.6
    transitions: list[str] = field(
        default_factory=lambda: [
            "fade", "dissolve", "fadeblack", "smoothleft", "smoothright", "smoothup",
            "slideleft", "slideright", "wipeleft", "wiperight", "circleopen",
            "hblur", "coverleft", "revealright", "zoomin",
        ]
    )
    video_layouts: dict[str, float] = field(
        default_factory=lambda: {"full": 0.35, "zoom_crop": 0.3, "push": 0.2, "inset": 0.15}
    )
    image_layouts: dict[str, float] = field(default_factory=lambda: {"kenburns": 0.75, "inset": 0.25})


@dataclass
class RenderConfig:
    chunk_size: int = 12
    workers: int = 2
    intermediate_crf: int = 15
    intermediate_preset: str = "veryfast"
    keep_intermediates: bool = False


@dataclass
class AppConfig:
    seed: int = 42
    output: OutputConfig = field(default_factory=OutputConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)
    transcription: TranscriptionConfig = field(default_factory=TranscriptionConfig)
    analysis: AnalysisConfig = field(default_factory=AnalysisConfig)
    search: SearchConfig = field(default_factory=SearchConfig)
    providers: dict[str, ProviderConfig] = field(
        default_factory=lambda: {
            "pexels": ProviderConfig(requests_per_minute=3.0, burst=5),  # 200/h oficial
            "pixabay": ProviderConfig(requests_per_minute=90.0, burst=10, locale="en"),  # 100/min oficial
            "mixkit_local": ProviderConfig(),
        }
    )
    ranking: RankingConfig = field(default_factory=RankingConfig)
    timeline: TimelineConfig = field(default_factory=TimelineConfig)
    render: RenderConfig = field(default_factory=RenderConfig)

    # Segredos (somente via ambiente).
    pexels_api_key: str | None = None
    pixabay_api_key: str | None = None


T = TypeVar("T")


def _build(cls: type[T], data: dict[str, Any] | None, base: T | None = None) -> T:
    """Mescla recursivamente um dicionário YAML sobre os defaults da dataclass."""
    obj = base if base is not None else cls()
    if not data:
        return obj
    for f in dataclasses.fields(obj):  # type: ignore[arg-type]
        if f.name not in data:
            continue
        value = data[f.name]
        current = getattr(obj, f.name)
        if dataclasses.is_dataclass(current) and isinstance(value, dict):
            setattr(obj, f.name, _build(type(current), value, current))
        else:
            setattr(obj, f.name, value)
    return obj


def load_config(path: str | Path | None = None, env_file: str | Path | None = ".env") -> AppConfig:
    if load_dotenv is not None and env_file and Path(env_file).exists():
        load_dotenv(env_file)

    raw: dict[str, Any] = {}
    if path and Path(path).exists():
        with open(path, encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}

    providers_raw = raw.pop("providers", None) or {}
    cfg = _build(AppConfig, raw)
    for name, pdata in providers_raw.items():
        base = cfg.providers.get(name, ProviderConfig())
        cfg.providers[name] = _build(ProviderConfig, pdata, base)

    cfg.pexels_api_key = os.getenv("PEXELS_API_KEY") or None
    cfg.pixabay_api_key = os.getenv("PIXABAY_API_KEY") or None
    _validate(cfg)
    return cfg


def _validate(cfg: AppConfig) -> None:
    tl = cfg.timeline
    if tl.max_clip_seconds > 7.0:
        raise ValueError("timeline.max_clip_seconds não pode passar de 7 segundos")
    if tl.min_clip_seconds * 2 > tl.max_clip_seconds - tl.transition_max:
        raise ValueError("min_clip_seconds muito alto para max_clip_seconds/transition_max")
    if not 0 <= tl.transition_min <= tl.transition_max:
        raise ValueError("transition_min/transition_max inválidos")
    if cfg.output.width % 2 or cfg.output.height % 2:
        raise ValueError("resolução de saída precisa ter dimensões pares")
