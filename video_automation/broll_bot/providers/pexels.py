"""Fonte Pexels (vídeos e fotos). Docs: https://www.pexels.com/api/documentation/"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..http import HttpClient
from ..models import Candidate, MediaType
from .base import BaseProvider, ProviderError

VIDEO_URL = "https://api.pexels.com/videos/search"
PHOTO_URL = "https://api.pexels.com/v1/search"

_LOCALES = {"pt": "pt-BR", "en": "en-US", "es": "es-ES"}


def _slug_description(page_url: str) -> str:
    """'https://www.pexels.com/video/man-typing-on-laptop-857195/' -> 'man typing on laptop'."""
    match = re.search(r"/(?:video|photo)/([a-z0-9-]+?)-?\d*/?$", page_url or "")
    return match.group(1).replace("-", " ").strip() if match else ""


class PexelsProvider(BaseProvider):
    name = "pexels"
    supported_media = (MediaType.VIDEO, MediaType.IMAGE)

    def __init__(self, api_key: str | None, http: HttpClient, orientation: str = "landscape",
                 target_width: int = 1920, locale: str = "en-US") -> None:
        self.api_key = api_key
        self.http = http
        self.orientation = orientation
        self.target_width = target_width
        self.locale = locale
        if api_key:
            self.http.session.headers["Authorization"] = api_key

    def available(self) -> bool:
        return bool(self.api_key)

    def request_count(self) -> int:
        return self.http.requests_made

    def search(self, query: str, media_type: MediaType, limit: int, language: str = "en") -> list[Candidate]:
        params: dict[str, Any] = {
            "query": query[:100],
            "per_page": max(1, min(limit, 80)),
            "orientation": self.orientation,
            "locale": _LOCALES.get(language, self.locale),
        }
        if media_type is MediaType.VIDEO:
            data = self.http.get_json(VIDEO_URL, params)
            return [c for item in data.get("videos", []) if (c := self._video(item, query))]
        data = self.http.get_json(PHOTO_URL, params)
        return [c for item in data.get("photos", []) if (c := self._photo(item, query))]

    def _pick_file(self, files: list[dict[str, Any]]) -> dict[str, Any] | None:
        mp4 = [f for f in files if f.get("file_type") == "video/mp4" and f.get("link") and f.get("width")]
        if not mp4:
            return None
        # O menor arquivo que ainda atinge a largura alvo; senão, o maior disponível.
        good = sorted((f for f in mp4 if f["width"] >= self.target_width), key=lambda f: f["width"])
        return good[0] if good else max(mp4, key=lambda f: f["width"])

    def _video(self, item: dict[str, Any], query: str) -> Candidate | None:
        best = self._pick_file(item.get("video_files") or [])
        if best is None:
            return None
        page = item.get("url", "")
        return Candidate(
            provider=self.name,
            media_id=str(item["id"]),
            media_type=MediaType.VIDEO,
            width=int(best.get("width") or item.get("width") or 0),
            height=int(best.get("height") or item.get("height") or 0),
            duration=float(item.get("duration") or 0),
            thumbnail_url=item.get("image"),
            download_url=best["link"],
            page_url=page,
            description=_slug_description(page),
            tags=_slug_description(page).split(),
            author=(item.get("user") or {}).get("name", ""),
            query=query,
        )

    def _photo(self, item: dict[str, Any], query: str) -> Candidate | None:
        src = item.get("src") or {}
        url = src.get("original") or src.get("large2x")
        if not url:
            return None
        page = item.get("url", "")
        description = item.get("alt") or _slug_description(page)
        return Candidate(
            provider=self.name,
            media_id=str(item["id"]),
            media_type=MediaType.IMAGE,
            width=int(item.get("width") or 0),
            height=int(item.get("height") or 0),
            duration=None,
            thumbnail_url=src.get("medium") or src.get("small"),
            download_url=url,
            page_url=page,
            description=description,
            tags=description.lower().split(),
            author=item.get("photographer", ""),
            query=query,
        )

    def download(self, candidate: Candidate, dest_dir: Path) -> Path:
        if not candidate.download_url:
            raise ProviderError("candidato sem URL de download")
        default = ".mp4" if candidate.media_type is MediaType.VIDEO else ".jpg"
        ext = self.extension_for(candidate.download_url, default)
        dest = dest_dir / f"{self.name}_{candidate.media_type.value}_{candidate.media_id}{ext}"
        if dest.exists() and dest.stat().st_size > 0:
            return dest
        return self.http.download(candidate.download_url, dest)
