import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from shared.errors import DependencyUnavailableError
from shared.fault_injection import FaultInjected, FaultInjector


@dataclass
class ProviderResult:
    success: bool
    provider_reference: str | None
    failure_reason: str | None


class PaymentProvider(Protocol):
    name: str

    async def capture(self, transaction_id: uuid.UUID, amount: Decimal, currency: str) -> ProviderResult: ...


class MockPaymentProvider:
    """Stand-in for a real payment provider sandbox — still the default
    (PAYMENT_PROVIDER_BACKEND=mock) so the existing stack needs no
    external credentials. Phase 10 adds a real PayPal client
    (app/clients/paypal_provider.py) implementing this same
    PaymentProvider interface as a selectable alternative — nothing in
    PaymentOrchestrator changes, only which provider gets constructed in
    core/dependencies.py, based on settings.provider_backend.

    Deterministic failure trigger (amount == 0.13) exists purely so the
    failure/compensation path is exercisable in tests without randomness
    — a business-level decline, not an infrastructure fault, so it
    doesn't touch the fault injector below and never counts toward the
    provider circuit breaker (see PaymentOrchestrator's is_failure
    predicate).

    fault_injector (Phase 6) is checked separately, ahead of that: the
    provider isn't a network service Fault Injection Middleware can sit
    in front of (it's an in-process call), so it checks its own injector
    directly and translates FaultInjected into DependencyUnavailableError
    itself — the same exception a real network failure would raise, so
    it counts toward the breaker the same way."""

    name = "mock"

    _DECLINE_AMOUNT = Decimal("0.13")

    def __init__(self, fault_injector: FaultInjector) -> None:
        self._fault_injector = fault_injector

    async def capture(self, transaction_id: uuid.UUID, amount: Decimal, currency: str) -> ProviderResult:
        try:
            await self._fault_injector.maybe_apply()
        except FaultInjected as exc:
            raise DependencyUnavailableError(f"payment provider fault injected: {exc}") from exc

        if amount == self._DECLINE_AMOUNT:
            return ProviderResult(success=False, provider_reference=None, failure_reason="simulated provider decline")
        return ProviderResult(
            success=True, provider_reference=f"mock_{transaction_id.hex[:12]}", failure_reason=None
        )
