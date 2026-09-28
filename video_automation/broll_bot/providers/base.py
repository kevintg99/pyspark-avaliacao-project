"""Interface comum das fontes de b-roll."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import ClassVar

from ..errors import AuthError, ProviderError, RateLimitedError
from ..models import Candidate, MediaType

__all__ = ["AuthError", "BaseProvider", "ProviderError", "RateLimitedError"]


class BaseProvider(ABC):
    name: ClassVar[str] = "base"
    is_remote: ClassVar[bool] = True
    supported_media: ClassVar[tuple[MediaType, ...]] = (MediaType.VIDEO,)

    def available(self) -> bool:
        return True

    def supports(self, media_type: MediaType) -> bool:
        return media_type in self.supported_media

    @abstractmethod
    def search(self, query: str, media_type: MediaType, limit: int, language: str = "en") -> list[Candidate]:
        """Busca candidatos. Deve levantar ``ProviderError`` em falhas."""

    @abstractmethod
    def download(self, candidate: Candidate, dest_dir: Path) -> Path:
        """Garante o arquivo local do candidato e devolve o caminho."""

    def request_count(self) -> int:
        return 0

    @staticmethod
    def extension_for(url: str, default: str) -> str:
        path = url.split("?", 1)[0]
        suffix = Path(path).suffix.lower()
        return suffix if suffix in {".mp4", ".mov", ".webm", ".jpg", ".jpeg", ".png", ".webp"} else default
