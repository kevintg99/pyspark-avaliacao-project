"""Controle de taxa, retry com backoff e circuit breaker por fonte."""

from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass
from typing import Callable


class TokenBucket:
    """Token bucket thread-safe.

    ``acquire`` nunca espera mais que ``max_wait``: se a espera necessária
    for maior, devolve ``False`` e o chamador pode partir para outra fonte
    em vez de travar o pipeline.
    """

    def __init__(
        self,
        rate_per_second: float,
        capacity: int,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if rate_per_second <= 0:
            raise ValueError("rate_per_second precisa ser > 0")
        self.rate = rate_per_second
        self.capacity = max(1, capacity)
        self._tokens = float(self.capacity)
        self._clock = clock
        self._sleep = sleep
        self._last = clock()
        self._blocked_until = 0.0
        self._lock = threading.Lock()

    def _refill(self, now: float) -> None:
        self._tokens = min(self.capacity, self._tokens + (now - self._last) * self.rate)
        self._last = now

    def block_until(self, monotonic_deadline: float) -> None:
        """Bloqueia a fonte até um instante (ex.: header X-RateLimit-Reset)."""
        with self._lock:
            self._blocked_until = max(self._blocked_until, monotonic_deadline)
            self._tokens = 0.0

    def wait_time(self) -> float:
        with self._lock:
            now = self._clock()
            self._refill(now)
            blocked = max(0.0, self._blocked_until - now)
            deficit = 0.0 if self._tokens >= 1 else (1 - self._tokens) / self.rate
            return max(blocked, deficit)

    def acquire(self, max_wait: float) -> bool:
        while True:
            with self._lock:
                now = self._clock()
                self._refill(now)
                if now >= self._blocked_until and self._tokens >= 1:
                    self._tokens -= 1
                    return True
                blocked = max(0.0, self._blocked_until - now)
                wait = max(blocked, (1 - self._tokens) / self.rate)
            if wait > max_wait:
                return False
            self._sleep(wait)
            max_wait -= wait


@dataclass
class RetryPolicy:
    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 20.0

    def delay(self, attempt: int, rng: random.Random | None = None) -> float:
        """Backoff exponencial com *full jitter* (attempt começa em 0)."""
        rng = rng or random
        cap = min(self.max_delay, self.base_delay * (2**attempt))
        return rng.uniform(cap / 2, cap)


class CircuitBreaker:
    """Desativa uma fonte temporariamente depois de N falhas seguidas."""

    def __init__(
        self, failure_threshold: int = 3, cooldown_seconds: float = 300.0, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.failure_threshold = max(1, failure_threshold)
        self.cooldown = cooldown_seconds
        self._clock = clock
        self._failures = 0
        self._opened_at: float | None = None
        self._lock = threading.Lock()

    @property
    def is_open(self) -> bool:
        with self._lock:
            if self._opened_at is None:
                return False
            if self._clock() - self._opened_at >= self.cooldown:
                # meia-abertura: deixa uma tentativa passar
                self._opened_at = None
                self._failures = self.failure_threshold - 1
                return False
            return True

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_at = None

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._failures >= self.failure_threshold:
                self._opened_at = self._clock()

    def trip(self) -> None:
        """Abre imediatamente (ex.: chave inválida, cota esgotada)."""
        with self._lock:
            self._failures = self.failure_threshold
            self._opened_at = self._clock()
