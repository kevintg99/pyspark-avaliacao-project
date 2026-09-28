"""Estruturas de dados compartilhadas entre as etapas do pipeline."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class MediaType(str, Enum):
    VIDEO = "video"
    IMAGE = "image"


@dataclass(slots=True)
class Word:
    """Palavra transcrita com timestamps (usada só para sincronizar)."""

    text: str
    start: float
    end: float


@dataclass(slots=True)
class Sentence:
    index: int
    text: str
    paragraph: int
    start: float = 0.0
    end: float = 0.0


@dataclass(slots=True)
class SceneAnalysis:
    visual_summary: str
    queries: list[str]
    fallback_query: str
    negative_terms: list[str] = field(default_factory=list)
    tone: str = "neutral"
    # Idioma das consultas: "en" (padrão, via LLM) ou o idioma do roteiro (modo heurístico).
    query_language: str = "en"


@dataclass(slots=True)
class Scene:
    index: int
    text: str
    start: float
    end: float
    analysis: SceneAnalysis

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass
class Candidate:
    """Um resultado de busca (ainda não baixado, exceto na biblioteca local)."""

    provider: str
    media_id: str
    media_type: MediaType
    width: int
    height: int
    duration: float | None
    thumbnail_url: str | None
    download_url: str | None
    page_url: str = ""
    tags: list[str] = field(default_factory=list)
    description: str = ""
    author: str = ""
    query: str = ""
    local_path: str | None = None
    thumbnail_path: str | None = None
    # Preenchidos pelo ranking.
    score: float = 0.0
    semantic: float = 0.0
    text_penalty: float = 0.0
    embedding: Any = field(default=None, repr=False, compare=False)

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.media_type.value}:{self.media_id}"

    @property
    def aspect(self) -> float:
        return self.width / self.height if self.height else 16 / 9

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["media_type"] = self.media_type.value
        data.pop("embedding", None)
        for transient in ("score", "semantic", "text_penalty", "thumbnail_path"):
            data.pop(transient, None)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Candidate":
        payload = dict(data)
        payload["media_type"] = MediaType(payload["media_type"])
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in payload.items() if k in known})


@dataclass(slots=True)
class Transition:
    kind: str  # nome do xfade ou "cut"
    duration: float  # segundos (0 para corte seco)

    @property
    def is_cut(self) -> bool:
        return self.kind == "cut" or self.duration <= 0


@dataclass
class Shot:
    """Um b-roll na linha do tempo.

    ``cut_in``/``cut_out`` são os pontos de troca (meio da transição) na
    narração. ``start_frame``/``end_frame`` incluem a sobreposição das
    transições e já estão quantizados em frames, o que evita deriva de sync.
    """

    index: int
    scene_index: int
    cut_in: float
    cut_out: float
    clip_duration: float
    transition_in: Transition
    start_frame: int = 0
    end_frame: int = 0
    prefer_media: MediaType = MediaType.VIDEO
    candidate: Candidate | None = None
    media_path: str | None = None
    source_offset: float = 0.0
    speed: float = 1.0
    layout: dict[str, Any] = field(default_factory=dict)
    fallback_reason: str | None = None

    @property
    def frames(self) -> int:
        return self.end_frame - self.start_frame
