"""Unit tests for shared/circuit_breaker.py. Run from the repo root:
    python -m pytest tests/unit/test_circuit_breaker.py -v

Uses FakeRedis (below) instead of a real Redis connection — the breaker
only ever calls get/set/incr/delete, so a small in-memory stub keeps
these tests fast and hermetic rather than depending on infra being up.
"""

import asyncio

import pytest

from shared.circuit_breaker import (
    BreakerOpenError,
    BreakerState,
    CircuitBreaker,
    CircuitBreakerConfig,
)


class FakeRedis:
    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self._store.get(key)

    async def set(self, key: str, value: str, nx: bool = False, ex: int | None = None) -> bool | None:
        if nx and key in self._store:
            return None
        self._store[key] = str(value)
        return True

    async def incr(self, key: str) -> int:
        value = int(self._store.get(key, 0)) + 1
        self._store[key] = str(value)
        return value

    async def delete(self, *keys: str) -> None:
        for key in keys:
            self._store.pop(key, None)


class DependencyError(Exception):
    """Stand-in for shared.errors.DependencyUnavailableError — a real
    infra failure the breaker should count."""


class BusinessRejection(Exception):
    """Stand-in for a business-rule rejection (e.g. ConflictError,
    insufficient balance) — the breaker should never retry or count
    this, it's the dependency working correctly and saying no."""


def make_breaker(**config_overrides) -> CircuitBreaker:
    config = CircuitBreakerConfig(
        failure_threshold=3,
        recovery_timeout=0.05,
        max_retries=1,
        backoff_base=0.001,
        **config_overrides,
    )
    return CircuitBreaker("test-dep", FakeRedis(), config)


def is_dependency_error(exc: Exception) -> bool:
    return isinstance(exc, DependencyError)


async def test_starts_closed():
    breaker = make_breaker()
    assert await breaker.state() == BreakerState.closed


async def test_successful_call_passes_through_and_stays_closed():
    breaker = make_breaker()

    async def ok():
        return "result"

    result = await breaker.call(ok)
    assert result == "result"
    assert await breaker.state() == BreakerState.closed


async def test_opens_after_failure_threshold():
    breaker = make_breaker()

    async def always_fails():
        raise DependencyError("down")

    for _ in range(3):  # failure_threshold=3
        with pytest.raises(DependencyError):
            await breaker.call(always_fails, is_failure=is_dependency_error)

    assert await breaker.state() == BreakerState.open


async def test_open_breaker_rejects_without_calling_fn():
    breaker = make_breaker()
    calls = 0

    async def always_fails():
        nonlocal calls
        calls += 1
        raise DependencyError("down")

    for _ in range(3):  # failure_threshold=3 calls, each retried once (max_retries=1) -> 6 fn invocations
        with pytest.raises(DependencyError):
            await breaker.call(always_fails, is_failure=is_dependency_error)
    assert calls == 6

    # Breaker is open now — next call must be rejected immediately,
    # fn must not run again.
    with pytest.raises(BreakerOpenError):
        await breaker.call(always_fails, is_failure=is_dependency_error)
    assert calls == 6


async def test_half_open_after_recovery_timeout_then_closes_on_success():
    breaker = make_breaker()  # recovery_timeout=0.05

    async def always_fails():
        raise DependencyError("down")

    for _ in range(3):
        with pytest.raises(DependencyError):
            await breaker.call(always_fails, is_failure=is_dependency_error)
    assert await breaker.state() == BreakerState.open

    await asyncio.sleep(0.07)  # let the cooldown elapse

    async def now_succeeds():
        return "recovered"

    result = await breaker.call(now_succeeds, is_failure=is_dependency_error)
    assert result == "recovered"
    assert await breaker.state() == BreakerState.closed


async def test_half_open_probe_failure_reopens_immediately():
    breaker = make_breaker()

    async def always_fails():
        raise DependencyError("down")

    for _ in range(3):
        with pytest.raises(DependencyError):
            await breaker.call(always_fails, is_failure=is_dependency_error)
    await asyncio.sleep(0.07)

    # The probe itself fails too.
    with pytest.raises(DependencyError):
        await breaker.call(always_fails, is_failure=is_dependency_error)
    assert await breaker.state() == BreakerState.open


async def test_half_open_probe_is_not_retried():
    breaker = make_breaker()
    calls = 0

    async def always_fails():
        nonlocal calls
        calls += 1
        raise DependencyError("down")

    for _ in range(3):  # failure_threshold=3 calls, each retried once (max_retries=1) -> 6 fn invocations
        with pytest.raises(DependencyError):
            await breaker.call(always_fails, is_failure=is_dependency_error)
    assert calls == 6
    await asyncio.sleep(0.07)

    with pytest.raises(DependencyError):
        await breaker.call(always_fails, is_failure=is_dependency_error)
    # max_retries=1 would mean 2 more attempts if this were retried like a
    # CLOSED-state call — a half-open probe must be exactly one attempt.
    assert calls == 7


async def test_business_rejection_is_not_retried_or_counted():
    breaker = make_breaker()
    calls = 0

    async def rejects():
        nonlocal calls
        calls += 1
        raise BusinessRejection("insufficient balance")

    for _ in range(5):  # well past failure_threshold=3
        with pytest.raises(BusinessRejection):
            await breaker.call(rejects, is_failure=is_dependency_error)

    assert calls == 5  # never retried — one call in, one call out, each time
    assert await breaker.state() == BreakerState.closed  # never counted as a failure


async def test_transient_failure_recovers_via_retry_without_tripping():
    breaker = make_breaker()
    attempts = 0

    async def fails_once_then_succeeds():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise DependencyError("blip")
        return "ok"

    result = await breaker.call(fails_once_then_succeeds, is_failure=is_dependency_error)
    assert result == "ok"
    assert attempts == 2  # first attempt + one retry (max_retries=1)
    assert await breaker.state() == BreakerState.closed


async def test_force_open_and_reset():
    breaker = make_breaker()
    assert await breaker.state() == BreakerState.closed

    await breaker.force_open()
    assert await breaker.state() == BreakerState.open

    with pytest.raises(BreakerOpenError):
        await breaker.call(lambda: None)

    await breaker.reset()
    assert await breaker.state() == BreakerState.closed
