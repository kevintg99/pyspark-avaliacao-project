"""Fontes de b-roll e fábrica configurável."""

from __future__ import annotations

from pathlib import Path

from ..config import AppConfig, ProviderConfig
from ..http import HttpClient
from ..ratelimit import RetryPolicy, TokenBucket
from .base import AuthError, BaseProvider, ProviderError, RateLimitedError
from .local_library import LocalLibraryProvider, SemanticFn
from .pexels import PexelsProvider
from .pixabay import PixabayProvider

__all__ = [
    "AuthError", "BaseProvider", "ProviderError", "RateLimitedError",
    "LocalLibraryProvider", "PexelsProvider", "PixabayProvider", "build_providers",
]


def _http(name: str, pcfg: ProviderConfig) -> HttpClient:
    limiter = TokenBucket(rate_per_second=pcfg.requests_per_minute / 60.0, capacity=pcfg.burst)
    return HttpClient(name, limiter, RetryPolicy(max_retries=pcfg.max_retries),
                      max_wait_seconds=pcfg.max_wait_seconds, timeout=pcfg.timeout_seconds)


def build_providers(cfg: AppConfig, base_dir: Path, semantic: SemanticFn | None = None) -> dict[str, BaseProvider]:
    """Instancia só as fontes habilitadas, na ordem de ``search.provider_order``.

    Para adicionar uma fonte nova: implemente ``BaseProvider`` e registre aqui.
    """
    providers: dict[str, BaseProvider] = {}
    cache_dir = base_dir / cfg.paths.cache_dir
    for name in cfg.search.provider_order:
        pcfg = cfg.providers.get(name, ProviderConfig())
        if not pcfg.enabled:
            continue
        if name == "pexels":
            providers[name] = PexelsProvider(cfg.pexels_api_key, _http(name, pcfg),
                                             orientation=cfg.search.orientation,
                                             target_width=cfg.output.width, locale=pcfg.locale)
        elif name == "pixabay":
            providers[name] = PixabayProvider(cfg.pixabay_api_key, _http(name, pcfg),
                                              min_width=cfg.search.min_width,
                                              target_width=cfg.output.width, lang=pcfg.locale)
        elif name == "mixkit_local":
            providers[name] = LocalLibraryProvider(base_dir / cfg.paths.local_library,
                                                   cache_dir / f"index_{name}.json", name=name, semantic=semantic)
    return providers
