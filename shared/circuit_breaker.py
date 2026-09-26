import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

import redis.asyncio as redis

from shared.logging import get_logger

logger = get_logger(__name__)


class BreakerState(str, Enum):
    closed = "closed"
    open = "open"
    half_open = "half_open"


class BreakerOpenError(Exception):
    """Raised when a call is rejected outright — breaker open and cooldown
    not elapsed, or elapsed but another caller already claimed the
    single half-open probe slot. Deliberately not a shared.errors type:
    the breaker has no business knowing about any one service's error
    vocabulary. Callers translate this to DependencyUnavailableError at
    the call site — see payment_orchestrator.py."""


@dataclass
class CircuitBreakerConfig:
    failure_threshold: int = 5
    recovery_timeout: float = 30.0
    # max_retries=1 (2 attempts total while CLOSED), not 2 (3 attempts) —
    # tuned down from the original default after a real bug found via
    # testing: with a 3s per-call client timeout, 3 attempts + backoff
    # could take ~10.2s, longer than the Gateway's own 10s proxy timeout
    # to Payment Service. The Gateway would occasionally give up waiting
    # even though Payment's retry sequence was about to succeed (or
    # gracefully degrade) on its own. See ADR-0012: a caller's timeout
    # must have real headroom over a callee's worst-case retry duration,
    # not just its single-attempt timeout.
    max_retries: int = 1
    backoff_base: float = 0.2  # seconds; doubles each retry (0.2, 0.4, ...)


def _always_a_failure(_: Exception) -> bool:
    return True


class CircuitBreaker:
    """CLOSED -> OPEN -> HALF_OPEN -> CLOSED, state in Redis (see ADR-0003
    for why a breaker exists at all instead of relying on retries alone).

    Composition, not two separate layers bolted together: retry-with-
    backoff only happens while CLOSED, and only for exceptions the caller
    marks as `is_failure` — a business-rule rejection (e.g. "insufficient
    balance") is neither retried nor counted toward the failure
    threshold, only genuine dependency failures are. A HALF_OPEN probe is
    never retried — it's meant to be one fast, cheap answer to "has this
    recovered?", not another chance to hammer a maybe-still-struggling
    dependency."""

    def __init__(
        self,
        name: str,
        redis_client: redis.Redis,
        config: CircuitBreakerConfig | None = None,
    ) -> None:
        self._name = name
        self._redis = redis_client
        self._config = config or CircuitBreakerConfig()

    @property
    def name(self) -> str:
        return self._name

    async def state(self) -> BreakerState:
        raw = await self._redis.get(self._key("state"))
        return BreakerState(raw) if raw else BreakerState.closed

    async def call(
        self,
        fn: Callable[..., Awaitable[Any]],
        *args: Any,
        is_failure: Callable[[Exception], bool] = _always_a_failure,
        **kwargs: Any,
    ) -> Any:
        current = await self._pre_call_check()
        retryable = current == BreakerState.closed
        attempts = self._config.max_retries + 1 if retryable else 1

        for attempt in range(attempts):
            try:
                result = await fn(*args, **kwargs)
            except Exception as exc:
                if not is_failure(exc):
                    # A legitimate rejection from a healthy dependency
                    # (e.g. 409 insufficient balance) — not a breaker
                    # concern. Don't retry it, don't count it, don't
                    # touch breaker state at all.
                    raise
                if attempt < attempts - 1:
                    delay = self._config.backoff_base * (2**attempt)
                    logger.warning(
                        "breaker.retry",
                        breaker=self._name,
                        attempt=attempt + 1,
                        delay_seconds=delay,
                        error=str(exc),
                    )
                    await asyncio.sleep(delay)
                    continue
                await self._record_failure(current)
                raise
            else:
                await self._record_success(current)
                return result

    async def force_open(self) -> None:
        """Manual override — used by the /internal/circuit-breakers
        endpoints (Ops Controller calls this via the Healer Agent,
        Phase 8)."""
        await self._trip()

    async def reset(self) -> None:
        await self._redis.delete(self._key("failures"), self._key("opened_at"), self._key("probe"))
        await self._redis.set(self._key("state"), BreakerState.closed.value)
        logger.info("breaker.reset", breaker=self._name)

    async def _pre_call_check(self) -> BreakerState:
        current = await self.state()
        if current != BreakerState.open:
            return current

        opened_at_raw = await self._redis.get(self._key("opened_at"))
        opened_at = float(opened_at_raw) if opened_at_raw else 0.0
        if time.time() - opened_at < self._config.recovery_timeout:
            raise BreakerOpenError(f"circuit '{self._name}' is open")

        # Cooldown elapsed. Let exactly one caller through as a probe —
        # SET NX is the same atomic-claim primitive shared/idempotency.py
        # uses for the same reason: avoid a thundering herd of "is it
        # back yet?" attempts the moment the timeout lapses.
        acquired = await self._redis.set(
            self._key("probe"), "1", nx=True, ex=int(self._config.recovery_timeout) or 1
        )
        if not acquired:
            raise BreakerOpenError(f"circuit '{self._name}' is open (probe already in flight)")

        await self._redis.set(self._key("state"), BreakerState.half_open.value)
        logger.info("breaker.half_open", breaker=self._name)
        return BreakerState.half_open

    async def _record_success(self, state_before_call: BreakerState) -> None:
        await self._redis.delete(self._key("failures"))
        if state_before_call != BreakerState.closed:
            await self._redis.set(self._key("state"), BreakerState.closed.value)
            await self._redis.delete(self._key("opened_at"), self._key("probe"))
            logger.info("breaker.closed", breaker=self._name)

    async def _record_failure(self, state_before_call: BreakerState) -> None:
        if state_before_call == BreakerState.half_open:
            # The probe failed — back to OPEN immediately, no need to
            # accumulate a separate failure count for this.
            await self._trip()
            return
        count = await self._redis.incr(self._key("failures"))
        if count >= self._config.failure_threshold:
            await self._trip()

    async def _trip(self) -> None:
        await self._redis.set(self._key("state"), BreakerState.open.value)
        await self._redis.set(self._key("opened_at"), str(time.time()))
        await self._redis.delete(self._key("probe"))
        logger.warning("breaker.open", breaker=self._name)

    def _key(self, suffix: str) -> str:
        return f"breaker:{self._name}:{suffix}"
