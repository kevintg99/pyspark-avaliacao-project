"""HttpClient: retry, 429, headers de rate limit e erro de conexão (sem rede real)."""

import pytest
import requests

from broll_bot import http as http_mod
from broll_bot.errors import AuthError, RateLimitedError, UnreachableError
from broll_bot.http import HttpClient
from broll_bot.ratelimit import RetryPolicy, TokenBucket


class Resp:
    def __init__(self, status=200, payload=None, headers=None):
        self.status_code = status
        self._payload = payload or {}
        self.headers = requests.structures.CaseInsensitiveDict(headers or {})
        self.text = ""

    def json(self):
        return self._payload


class Session(requests.Session):
    def __init__(self, script):
        super().__init__()
        self.script = list(script)
        self.calls = 0

    def get(self, *a, **k):  # type: ignore[override]
        self.calls += 1
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(http_mod.time, "sleep", lambda s: None)


def client(script, retries=3):
    return HttpClient("t", TokenBucket(1000, 100), RetryPolicy(max_retries=retries), session=Session(script))


def test_retries_5xx_then_succeeds():
    c = client([Resp(503), Resp(500), Resp(200, {"ok": 1})])
    assert c.get_json("https://x") == {"ok": 1}
    assert c.session.calls == 3


def test_429_with_long_reset_gives_up_fast():
    c = client([Resp(429, headers={"X-RateLimit-Reset": "3600"})])
    with pytest.raises(RateLimitedError):
        c.get_json("https://x")
    assert c.session.calls == 1


def test_401_is_not_retried():
    c = client([Resp(401)])
    with pytest.raises(AuthError):
        c.get_json("https://x")
    assert c.session.calls == 1


def test_connection_error_retried_once_then_unreachable():
    c = client([requests.ConnectionError("proxy"), requests.ConnectionError("proxy")])
    with pytest.raises(UnreachableError):
        c.get_json("https://x")
    assert c.session.calls == 2


def test_remaining_zero_blocks_limiter():
    c = client([Resp(200, {"a": 1}, headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "120"})])
    c.get_json("https://x")
    assert c.limiter.wait_time() > 100
    assert not c.limiter.acquire(max_wait=1)
