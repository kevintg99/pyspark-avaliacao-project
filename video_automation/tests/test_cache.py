from broll_bot.cache import SQLiteCache, make_key


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_set_get_roundtrip(tmp_path):
    cache = SQLiteCache(tmp_path / "c.sqlite")
    cache.set("search", "k", [{"id": 1, "tags": ["a"]}], ttl_seconds=60)
    assert cache.get("search", "k") == [{"id": 1, "tags": ["a"]}]
    assert cache.get("search", "missing") is None
    assert cache.get("other-namespace", "k") is None


def test_ttl_expires(tmp_path):
    clock = FakeClock()
    cache = SQLiteCache(tmp_path / "c.sqlite", clock=clock)
    cache.set("search", "k", {"v": 1}, ttl_seconds=24 * 3600)
    clock.now += 24 * 3600 - 1
    assert cache.get("search", "k") == {"v": 1}
    clock.now += 2
    assert cache.get("search", "k") is None


def test_persists_between_instances(tmp_path):
    path = tmp_path / "c.sqlite"
    SQLiteCache(path).set("ns", "k", 42, ttl_seconds=60)
    assert SQLiteCache(path).get("ns", "k") == 42


def test_purge_expired(tmp_path):
    clock = FakeClock()
    cache = SQLiteCache(tmp_path / "c.sqlite", clock=clock)
    cache.set("ns", "a", 1, ttl_seconds=10)
    cache.set("ns", "b", 2, ttl_seconds=1000)
    clock.now += 100
    assert cache.purge_expired() == 1
    assert cache.get("ns", "b") == 2


def test_make_key_is_stable_and_order_sensitive():
    assert make_key("pexels", "video", "city night") == make_key("pexels", "video", "city night")
    assert make_key("pexels", "video", "a") != make_key("pixabay", "video", "a")


def test_corrupted_file_falls_back_to_memory(tmp_path):
    path = tmp_path / "c.sqlite"
    path.write_bytes(b"isto nao e um banco sqlite" * 100)
    cache = SQLiteCache(path)
    cache.set("ns", "k", 1, ttl_seconds=60)
    assert cache.get("ns", "k") == 1
