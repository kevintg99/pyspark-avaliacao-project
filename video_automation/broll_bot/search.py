"""Busca de b-rolls por cena: poucas consultas, cache, fallback entre fontes."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from .cache import SQLiteCache, make_key
from .config import AppConfig
from .errors import AuthError, ProviderError, RateLimitedError, UnreachableError
from .log import get_logger
from .models import Candidate, MediaType, Scene
from .providers.base import BaseProvider
from .ranking import Ranker
from .ratelimit import CircuitBreaker

log = get_logger("search")


@dataclass
class SceneSearchResult:
    scene_index: int
    ranked: list[Candidate] = field(default_factory=list)
    attempts: list[dict[str, Any]] = field(default_factory=list)
    budget_used: int = 0

    def best_query(self) -> str | None:
        ok = [a for a in self.attempts if a.get("results")]
        return ok[0]["query"] if ok else None


class BrollSearcher:
    def __init__(self, cfg: AppConfig, providers: dict[str, BaseProvider], ranker: Ranker,
                 cache: SQLiteCache, rng: random.Random) -> None:
        self.cfg = cfg
        self.providers = providers
        self.ranker = ranker
        self.cache = cache
        self.rng = rng
        cb = cfg.search.circuit_breaker
        self.breakers = {name: CircuitBreaker(cb.failure_threshold, cb.cooldown_seconds) for name in providers}
        self._unavailable_logged: set[str] = set()

    # -------------------------------------------------------------- infra
    def _ordered(self, media_type: MediaType) -> list[BaseProvider]:
        out = []
        for name, provider in self.providers.items():
            if not provider.supports(media_type):
                continue
            if not provider.available():
                if name not in self._unavailable_logged:
                    self._unavailable_logged.add(name)
                    log.warning("fonte indisponível (sem chave ou vazia); pulando", extra={"stage": "search", "source": name})
                continue
            out.append(provider)
        return out

    def _cached_search(self, provider: BaseProvider, query: str, media_type: MediaType,
                       language: str) -> tuple[list[Candidate], bool]:
        s = self.cfg.search
        if not provider.is_remote:  # busca local é barata; não precisa de cache
            return provider.search(query, media_type, s.per_page, language), True
        key = make_key(provider.name, media_type.value, query.lower().strip(), s.per_page, s.orientation, language)
        hit = self.cache.get("search", key)
        if hit is not None:
            return [Candidate.from_dict(d) for d in hit], True
        results = provider.search(query, media_type, s.per_page, language)
        self.cache.set("search", key, [c.to_dict() for c in results], ttl_seconds=s.cache_ttl_hours * 3600)
        return results, False

    def _attempt(self, provider: BaseProvider, query: str, media_type: MediaType, language: str,
                 result: SceneSearchResult) -> list[Candidate]:
        breaker = self.breakers[provider.name]
        record: dict[str, Any] = {"provider": provider.name, "query": query, "media": media_type.value}
        if breaker.is_open:
            record["skipped"] = "circuit_open"
            result.attempts.append(record)
            return []
        try:
            found, cached = self._cached_search(provider, query, media_type, language)
            breaker.record_success()
            record.update(results=len(found), cached=cached)
            if provider.is_remote and not cached:
                result.budget_used += 1
            return found
        except AuthError as exc:
            breaker.trip()
            result.budget_used += 1
            record["error"] = str(exc)
        except (RateLimitedError, UnreachableError) as exc:
            breaker.trip()
            result.budget_used += 1
            record["error"] = str(exc)
        except ProviderError as exc:
            breaker.record_failure()
            result.budget_used += 1
            record["error"] = str(exc)
        except Exception as exc:  # noqa: BLE001 - fonte com bug não derruba o pipeline
            breaker.record_failure()
            record["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            result.attempts.append(record)
        log.warning("fonte falhou; tentando a próxima", extra={"stage": "search", **record})
        return []

    # ------------------------------------------------------------- search
    def search_scene(self, scene: Scene, needed: int, needed_images: int, used_keys: set[str]) -> SceneSearchResult:
        s = self.cfg.search
        result = SceneSearchResult(scene.index)
        pool: dict[str, Candidate] = {}
        lang = scene.analysis.query_language
        queries = list(dict.fromkeys([*scene.analysis.queries, scene.analysis.fallback_query]))

        def counts() -> tuple[int, int]:
            good = [c for c in result.ranked if c.score >= s.min_score and c.key not in used_keys]
            return (sum(c.media_type is MediaType.VIDEO for c in good), sum(c.media_type is MediaType.IMAGE for c in good))

        def budget_left(provider: BaseProvider) -> bool:
            counts_budget = provider.is_remote or s.local_counts_toward_budget
            return not counts_budget or result.budget_used < s.max_searches_per_scene

        def run_pass(media: MediaType, target: int, query_list: list[str]) -> None:
            for query in query_list:
                for provider in self._ordered(media):
                    if counts()[0 if media is MediaType.VIDEO else 1] >= target:
                        return
                    if not budget_left(provider):
                        continue
                    found = self._attempt(provider, query, media, lang, result)
                    new = [c for c in found if c.key not in pool]
                    if new:
                        for c in new:
                            pool[c.key] = c
                        result.ranked = self.ranker.rank(scene, list(pool.values()))

        target_videos = max(0, needed - needed_images) + s.reserve_per_scene
        run_pass(MediaType.VIDEO, target_videos, queries)
        if needed_images:
            run_pass(MediaType.IMAGE, needed_images, queries[:1] + queries[-1:])
        videos, images = counts()
        if videos + images < needed + s.reserve_per_scene:
            # completa com o tipo que faltar, ainda dentro do orçamento
            run_pass(MediaType.IMAGE if needed_images == 0 else MediaType.VIDEO, needed + s.reserve_per_scene, queries)

        videos, images = counts()
        log.info(
            "cena pesquisada",
            extra={"stage": "search", "scene": scene.index, "needed": needed, "good_videos": videos,
                   "good_images": images, "api_calls": result.budget_used,
                   "top_score": result.ranked[0].score if result.ranked else None},
        )
        return result
