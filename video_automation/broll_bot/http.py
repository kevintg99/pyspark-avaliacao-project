"""Cliente HTTP com rate limit, retry/backoff e leitura de headers X-RateLimit-*."""

from __future__ import annotations

import os
import random
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

import requests

from .log import get_logger
from .errors import AuthError, ProviderError, RateLimitedError, UnreachableError
from .ratelimit import RetryPolicy, TokenBucket

log = get_logger("http")

USER_AGENT = "broll-bot/1.0 (+automacao de edicao de video)"


class HttpClient:
    def __init__(
        self,
        name: str,
        limiter: TokenBucket,
        retry: RetryPolicy,
        max_wait_seconds: float = 20.0,
        timeout: float = 20.0,
        headers: dict[str, str] | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.name = name
        self.limiter = limiter
        self.retry = retry
        self.max_wait = max_wait_seconds
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, **(headers or {})})
        self.requests_made = 0

    # ------------------------------------------------------------------ API
    def get_json(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(self.retry.max_retries + 1):
            if not self.limiter.acquire(self.max_wait):
                raise RateLimitedError(f"{self.name}: limite local atingido (espera > {self.max_wait}s)")
            try:
                self.requests_made += 1
                resp = self.session.get(url, params=params, timeout=self.timeout)
            except requests.ConnectionError as exc:
                # rede/DNS/proxy fora: uma nova tentativa só; depois a fonte é desativada
                if attempt >= 1:
                    raise UnreachableError(f"{self.name}: sem conexão ({type(exc).__name__})") from exc
                last_error = exc
                self._sleep_backoff(attempt, reason=type(exc).__name__)
                continue
            except requests.RequestException as exc:
                last_error = exc
                self._sleep_backoff(attempt, reason=type(exc).__name__)
                continue

            self._apply_rate_headers(resp)

            if resp.status_code in (401, 403):
                raise AuthError(f"{self.name}: acesso negado ({resp.status_code}) — verifique a chave da API")
            if resp.status_code == 429:
                wait = self._retry_after(resp)
                last_error = RateLimitedError(f"{self.name}: HTTP 429")
                if wait is not None and wait > self.max_wait:
                    raise RateLimitedError(f"{self.name}: cota esgotada, reset em {wait:.0f}s")
                self._sleep_backoff(attempt, reason="429", minimum=wait or 0.0)
                continue
            if resp.status_code >= 500:
                last_error = ProviderError(f"{self.name}: HTTP {resp.status_code}")
                self._sleep_backoff(attempt, reason=str(resp.status_code))
                continue
            if resp.status_code >= 400:
                raise ProviderError(f"{self.name}: HTTP {resp.status_code}: {resp.text[:200]}")
            try:
                return resp.json()
            except ValueError as exc:
                raise ProviderError(f"{self.name}: resposta não é JSON") from exc
        raise ProviderError(f"{self.name}: falhou após {self.retry.max_retries + 1} tentativas: {last_error}")

    def download(self, url: str, dest: Path, max_bytes: int = 600 * 1024 * 1024) -> Path:
        """Baixa em arquivo temporário e renomeia no fim (downloads atômicos)."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        last_error: Exception | None = None
        for attempt in range(self.retry.max_retries + 1):
            fd, tmp_name = tempfile.mkstemp(dir=dest.parent, suffix=".part")
            os.close(fd)
            tmp = Path(tmp_name)
            try:
                with self.session.get(url, stream=True, timeout=(self.timeout, self.timeout * 3)) as resp:
                    if resp.status_code == 429 or resp.status_code >= 500:
                        raise ProviderError(f"HTTP {resp.status_code}")
                    if resp.status_code >= 400:
                        raise AuthError(f"download negado: HTTP {resp.status_code}")
                    written = 0
                    with open(tmp, "wb") as fh:
                        for chunk in resp.iter_content(chunk_size=1024 * 256):
                            written += len(chunk)
                            if written > max_bytes:
                                raise ProviderError("arquivo excede o tamanho máximo permitido")
                            fh.write(chunk)
                if written == 0:
                    raise ProviderError("download vazio")
                shutil.move(str(tmp), dest)
                return dest
            except AuthError:
                tmp.unlink(missing_ok=True)
                raise
            except (requests.RequestException, ProviderError) as exc:
                tmp.unlink(missing_ok=True)
                last_error = exc
                self._sleep_backoff(attempt, reason=f"download: {exc}")
        raise ProviderError(f"{self.name}: download falhou: {last_error}")

    # ------------------------------------------------------------- helpers
    def _sleep_backoff(self, attempt: int, reason: str, minimum: float = 0.0) -> None:
        if attempt >= self.retry.max_retries:
            return
        delay = max(minimum, self.retry.delay(attempt, random))
        log.info("retry com backoff", extra={"stage": "http", "source": self.name, "reason": reason, "delay": round(delay, 2)})
        time.sleep(delay)

    @staticmethod
    def _header(resp: requests.Response, name: str) -> float | None:
        value = resp.headers.get(name)
        try:
            return float(value) if value is not None else None
        except ValueError:
            return None

    def _reset_in(self, resp: requests.Response) -> float | None:
        reset = self._header(resp, "X-RateLimit-Reset")
        if reset is None:
            return None
        # Pexels manda timestamp UNIX; Pixabay manda segundos até o reset.
        return max(0.0, reset - time.time()) if reset > 1e9 else reset

    def _retry_after(self, resp: requests.Response) -> float | None:
        retry_after = self._header(resp, "Retry-After")
        return retry_after if retry_after is not None else self._reset_in(resp)

    def _apply_rate_headers(self, resp: requests.Response) -> None:
        remaining = self._header(resp, "X-RateLimit-Remaining")
        if remaining is not None and remaining <= 0:
            reset_in = self._reset_in(resp) or 60.0
            self.limiter.block_until(time.monotonic() + reset_in)
            log.warning(
                "cota da fonte zerada; pausando",
                extra={"stage": "http", "source": self.name, "reset_in_s": round(reset_in, 1)},
            )
