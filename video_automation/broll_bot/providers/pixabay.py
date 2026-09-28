"""Fonte Pixabay (vídeos e imagens). Docs: https://pixabay.com/api/docs/

Regras relevantes da API: respostas devem ficar em cache por 24h e as mídias
não podem ser usadas via hotlink (por isso baixamos o arquivo).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..http import HttpClient
from ..models import Candidate, MediaType
from .base import BaseProvider, ProviderError

VIDEO_URL = "https://pixabay.com/api/videos/"
IMAGE_URL = "https://pixabay.com/api/"

_SIZE_ORDER = ("large", "medium", "small", "tiny")


class PixabayProvider(BaseProvider):
    name = "pixabay"
    supported_media = (MediaType.VIDEO, MediaType.IMAGE)

    def __init__(self, api_key: str | None, http: HttpClient, min_width: int = 1280,
                 target_width: int = 1920, lang: str = "en") -> None:
        self.api_key = api_key
        self.http = http
        self.min_width = min_width
        self.target_width = target_width
        self.lang = lang

    def available(self) -> bool:
        return bool(self.api_key)

    def request_count(self) -> int:
        return self.http.requests_made

    def search(self, query: str, media_type: MediaType, limit: int, language: str = "en") -> list[Candidate]:
        params: dict[str, Any] = {
            "key": self.api_key,
            "q": query[:100],
            "per_page": max(3, min(limit, 200)),
            "safesearch": "true",
            "lang": language or self.lang,
            "min_width": self.min_width,
        }
        if media_type is MediaType.VIDEO:
            params["video_type"] = "film"
            data = self.http.get_json(VIDEO_URL, params)
            return [c for hit in data.get("hits", []) if (c := self._video(hit, query))]
        params.update({"image_type": "photo", "orientation": "horizontal"})
        data = self.http.get_json(IMAGE_URL, params)
        return [c for hit in data.get("hits", []) if (c := self._image(hit, query))]

    def _pick_rendition(self, videos: dict[str, Any]) -> dict[str, Any] | None:
        options = [videos[k] for k in _SIZE_ORDER if videos.get(k, {}).get("url")]
        if not options:
            return None
        good = sorted((v for v in options if (v.get("width") or 0) >= self.target_width), key=lambda v: v["width"])
        return good[0] if good else max(options, key=lambda v: v.get("width") or 0)

    @staticmethod
    def _tags(hit: dict[str, Any]) -> list[str]:
        return [t.strip().lower() for t in (hit.get("tags") or "").split(",") if t.strip()]

    def _video(self, hit: dict[str, Any], query: str) -> Candidate | None:
        videos = hit.get("videos") or {}
        best = self._pick_rendition(videos)
        if best is None:
            return None
        thumb = next((videos[k].get("thumbnail") for k in ("medium", "small", "large") if videos.get(k, {}).get("thumbnail")), None)
        if not thumb and hit.get("picture_id"):
            thumb = f"https://i.vimeocdn.com/video/{hit['picture_id']}_640x360.jpg"
        tags = self._tags(hit)
        return Candidate(
            provider=self.name,
            media_id=str(hit["id"]),
            media_type=MediaType.VIDEO,
            width=int(best.get("width") or 0),
            height=int(best.get("height") or 0),
            duration=float(hit.get("duration") or 0),
            thumbnail_url=thumb,
            download_url=best["url"],
            page_url=hit.get("pageURL", ""),
            tags=tags,
            description=", ".join(tags),
            author=hit.get("user", ""),
            query=query,
        )

    def _image(self, hit: dict[str, Any], query: str) -> Candidate | None:
        url = hit.get("largeImageURL") or hit.get("webformatURL")
        if not url:
            return None
        tags = self._tags(hit)
        return Candidate(
            provider=self.name,
            media_id=str(hit["id"]),
            media_type=MediaType.IMAGE,
            width=int(hit.get("imageWidth") or 0),
            height=int(hit.get("imageHeight") or 0),
            duration=None,
            thumbnail_url=hit.get("webformatURL") or hit.get("previewURL"),
            download_url=url,
            page_url=hit.get("pageURL", ""),
            tags=tags,
            description=", ".join(tags),
            author=hit.get("user", ""),
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
