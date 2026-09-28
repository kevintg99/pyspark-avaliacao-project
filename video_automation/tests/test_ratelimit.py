from broll_bot.ratelimit import CircuitBreaker, RetryPolicy, TokenBucket


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += s


def test_token_bucket_burst_then_waits():
    c = Clock()
    bucket = TokenBucket(rate_per_second=1.0, capacity=2, clock=c, sleep=c.sleep)
    assert bucket.acquire(max_wait=0) and bucket.acquire(max_wait=0)
    assert not bucket.acquire(max_wait=0.5)  # precisaria esperar ~1s
    assert bucket.acquire(max_wait=2)
    assert 0.9 <= c.t <= 1.1


def test_token_bucket_respects_block_until():
    c = Clock()
    bucket = TokenBucket(rate_per_second=100, capacity=5, clock=c, sleep=c.sleep)
    bucket.block_until(30.0)
    assert not bucket.acquire(max_wait=10)  # não trava o pipeline: devolve False
    assert bucket.acquire(max_wait=60)
    assert c.t >= 30.0


def test_retry_delay_grows_and_is_capped():
    policy = RetryPolicy(max_retries=5, base_delay=1.0, max_delay=8.0)
    import random

    rng = random.Random(0)
    delays = [policy.delay(i, rng) for i in range(6)]
    assert 0.5 <= delays[0] <= 1.0
    assert all(d <= 8.0 for d in delays)
    assert delays[3] >= 4.0


def test_circuit_breaker_opens_and_half_opens():
    c = Clock()
    cb = CircuitBreaker(failure_threshold=2, cooldown_seconds=60, clock=c)
    cb.record_failure()
    assert not cb.is_open
    cb.record_failure()
    assert cb.is_open
    c.t += 61
    assert not cb.is_open  # meia-abertura
    cb.record_failure()  # falhou de novo -> reabre
    assert cb.is_open
    c.t += 61
    cb.record_success()
    assert not cb.is_open
