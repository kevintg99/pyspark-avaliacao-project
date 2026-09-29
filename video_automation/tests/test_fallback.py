"""Fallback entre fontes, orçamento de buscas por cena, cache e circuit breaker."""

import random
from pathlib import Path

from broll_bot.cache import SQLiteCache
from broll_bot.config import AppConfig
from broll_bot.errors import AuthError, ProviderError, RateLimitedError
from broll_bot.models import Candidate, MediaType, Scene, SceneAnalysis
from broll_bot.providers.base import BaseProvider
from broll_bot.ranking import Ranker
from broll_bot.search import BrollSearcher


def cand(provider: str, i: int, tags: str, media=MediaType.VIDEO) -> Candidate:
    return Candidate(provider=provider, media_id=str(i), media_type=media, width=1920, height=1080,
                     duration=None if media is MediaType.IMAGE else 12.0, thumbnail_url=None,
                     download_url=f"https://x/{i}.mp4", tags=tags.split(), description=tags)


class FakeProvider(BaseProvider):
    supported_media = (MediaType.VIDEO, MediaType.IMAGE)

    def __init__(self, name: str, results=None, error: Exception | None = None, remote: bool = True):
        self.name = name  # type: ignore[misc]
        self.is_remote = remote  # type: ignore[misc]
        self.results = results or []
        self.error = error
        self.calls: list[tuple[str, MediaType]] = []

    def search(self, query, media_type, limit, language="en"):
        self.calls.append((query, media_type))
        if self.error:
            raise self.error
        return [c for c in self.results if c.media_type is media_type][:limit]

    def download(self, candidate, dest_dir) -> Path:  # pragma: no cover - não usado aqui
        raise NotImplementedError


def scene(queries=("city night traffic", "night street cars"), fallback="city"):
    return Scene(0, "Na cidade, o trânsito à noite...", 0, 10,
                 SceneAnalysis("city street at night with car traffic", list(queries), fallback, ["cartoon"], "calm"))


def make(tmp_path, providers, **search_overrides):
    cfg = AppConfig()
    cfg.search.provider_order = [p.name for p in providers]
    cfg.search.min_score = 0.3
    for k, v in search_overrides.items():
        setattr(cfg.search, k, v)
    ranker = Ranker(cfg, embedder=None, thumbs=None)
    cache = SQLiteCache(tmp_path / "cache.sqlite")
    return BrollSearcher(cfg, {p.name: p for p in providers}, ranker, cache, random.Random(1)), cache


GOOD = [cand("backup", i, "city night traffic cars street") for i in range(6)]


def test_failing_source_falls_back_to_next(tmp_path):
    broken = FakeProvider("broken", error=ProviderError("HTTP 500"))
    backup = FakeProvider("backup", results=GOOD)
    searcher, _ = make(tmp_path, [broken, backup])
    result = searcher.search_scene(scene(), needed=2, needed_images=0, used_keys=set())
    assert result.ranked and all(c.provider == "backup" for c in result.ranked)
    assert any(a.get("error") for a in result.attempts if a["provider"] == "broken")


def test_budget_limits_remote_calls_per_scene(tmp_path):
    useless = [FakeProvider(f"p{i}", results=[cand(f"p{i}", 1, "banana fruit")]) for i in range(3)]
    searcher, _ = make(tmp_path, useless, max_searches_per_scene=3)
    result = searcher.search_scene(scene(queries=("a b", "c d", "e f", "g h")), needed=3, needed_images=0, used_keys=set())
    total_calls = sum(len(p.calls) for p in useless)
    assert result.budget_used == 3
    assert total_calls == 3


def test_stops_searching_when_enough_good_results(tmp_path):
    first = FakeProvider("first", results=GOOD)
    second = FakeProvider("second", results=[cand("second", 9, "city night")])
    searcher, _ = make(tmp_path, [first, second])
    searcher.search_scene(scene(), needed=2, needed_images=0, used_keys=set())
    assert len(first.calls) == 1
    assert second.calls == []


def test_cache_hit_does_not_call_provider_again(tmp_path):
    provider = FakeProvider("backup", results=GOOD)
    searcher, _ = make(tmp_path, [provider])
    searcher.search_scene(scene(), needed=2, needed_images=0, used_keys=set())
    calls = len(provider.calls)
    second = searcher.search_scene(scene(), needed=2, needed_images=0, used_keys=set())
    assert len(provider.calls) == calls
    assert second.budget_used == 0
    assert all(a.get("cached") for a in second.attempts if "results" in a)


def test_auth_error_opens_circuit_immediately(tmp_path):
    bad = FakeProvider("bad", error=AuthError("401"))
    backup = FakeProvider("backup", results=GOOD)
    searcher, _ = make(tmp_path, [bad, backup])
    searcher.search_scene(scene(), needed=1, needed_images=0, used_keys=set())
    assert searcher.breakers["bad"].is_open
    searcher.search_scene(scene(queries=("other query",)), needed=1, needed_images=0, used_keys=set())
    assert len(bad.calls) == 1  # não insiste numa fonte com chave inválida


def test_rate_limited_source_is_skipped(tmp_path):
    limited = FakeProvider("limited", error=RateLimitedError("429"))
    backup = FakeProvider("backup", results=GOOD)
    searcher, _ = make(tmp_path, [limited, backup])
    result = searcher.search_scene(scene(), needed=1, needed_images=0, used_keys=set())
    assert result.ranked
    assert searcher.breakers["limited"].is_open


def test_unexpected_exception_never_stops_pipeline(tmp_path):
    buggy = FakeProvider("buggy", error=KeyError("campo novo na API"))
    backup = FakeProvider("backup", results=GOOD)
    searcher, _ = make(tmp_path, [buggy, backup])
    result = searcher.search_scene(scene(), needed=1, needed_images=0, used_keys=set())
    assert result.ranked


def test_local_source_does_not_consume_budget(tmp_path):
    local = FakeProvider("local", results=[cand("local", 1, "banana")], remote=False)
    remote = FakeProvider("remote", results=GOOD)
    searcher, _ = make(tmp_path, [local, remote], max_searches_per_scene=1)
    result = searcher.search_scene(scene(), needed=2, needed_images=0, used_keys=set())
    assert result.budget_used == 1
    assert len(remote.calls) == 1


def test_used_candidates_are_not_counted_as_available(tmp_path):
    provider = FakeProvider("backup", results=GOOD[:2])
    other = FakeProvider("other", results=[cand("other", 7, "city night traffic street")])
    searcher, _ = make(tmp_path, [provider, other])
    used = {c.key for c in GOOD[:2]}
    searcher.search_scene(scene(), needed=1, needed_images=0, used_keys=used)
    assert other.calls  # precisou buscar em outra fonte


def test_ranking_prefers_relevant_metadata(tmp_path):
    provider = FakeProvider("p", results=[cand("p", 1, "banana fruit kitchen"), cand("p", 2, "city night traffic")])
    searcher, _ = make(tmp_path, [provider])
    result = searcher.search_scene(scene(), needed=1, needed_images=0, used_keys=set())
    assert result.ranked[0].media_id == "2"


def test_unreachable_source_is_disabled_after_first_failure(tmp_path):
    from broll_bot.errors import UnreachableError

    offline = FakeProvider("offline", error=UnreachableError("proxy"))
    backup = FakeProvider("backup", results=GOOD)
    searcher, _ = make(tmp_path, [offline, backup])
    for q in ("q1", "q2", "q3"):
        searcher.search_scene(scene(queries=(q,)), needed=1, needed_images=0, used_keys=set())
    assert len(offline.calls) == 1


# ------------------------------------------------------------ seleção/planos B
def _selector(tmp_path, library_files):
    from broll_bot.models import Shot, Transition
    from broll_bot.providers.local_library import LocalLibraryProvider
    from broll_bot.selection import Selector

    cfg = AppConfig()
    lib = LocalLibraryProvider(tmp_path / "lib", tmp_path / "index.json")
    lib._entries = [
        Candidate("mixkit_local", name, MediaType.VIDEO, 1920, 1080, 10.0, None, None,
                  tags=name.split("-"), local_path=str(tmp_path / name))
        for name in library_files
    ]
    sel = Selector(cfg, {"mixkit_local": lib}, Ranker(cfg, None, None), tmp_path / "media", seed=1)
    sel.fetch = lambda c: Path(c.local_path)  # sem ffprobe no teste
    shots = [Shot(i, 0, i * 4.0, i * 4.0 + 4, 4.123, Transition("cut", 0), i * 120, i * 120 + 120) for i in range(6)]
    return sel, shots


def test_unmatched_scene_uses_unused_library_clips_before_repeating(tmp_path):
    sel, shots = _selector(tmp_path, ["office-laptop", "city-night", "coffee-cup"])
    sel.assign(shots[:3], {})
    assert {s.candidate.media_id for s in shots[:3]} == {"office-laptop", "city-night", "coffee-cup"}
    assert all(s.fallback_reason == "library_last_resort" for s in shots[:3])


def test_reuse_rotates_instead_of_ping_pong(tmp_path):
    sel, shots = _selector(tmp_path, ["a-clip", "b-clip", "c-clip"])
    sel.assign(shots, {})
    ids = [s.candidate.media_id for s in shots]
    assert all(x != y for x, y in zip(ids, ids[1:]))  # nunca repete em sequência
    assert sorted(ids[3:]) == ["a-clip", "b-clip", "c-clip"]  # repete todos antes de voltar a um
    assert all(s.fallback_reason == "reused_with_new_framing" for s in shots[3:])


def test_generated_background_only_when_nothing_exists(tmp_path):
    sel, shots = _selector(tmp_path, [])
    sel.assign(shots[:1], {})
    assert shots[0].fallback_reason == "generated_background"
