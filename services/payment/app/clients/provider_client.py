import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol


@dataclass
class ProviderResult:
    success: bool
    provider_reference: str | None
    failure_reason: str | None


class PaymentProvider(Protocol):
    name: str

    async def capture(self, transaction_id: uuid.UUID, amount: Decimal, currency: str) -> ProviderResult: ...


class MockPaymentProvider:
    """Stand-in for a real payment provider sandbox. Phase 10 replaces this
    with a real Stripe/Razorpay/PayPal client implementing the same
    PaymentProvider interface — nothing in PaymentOrchestrator changes,
    only which provider gets constructed in core/dependencies.py.

    Deterministic failure trigger (amount == 0.13) exists purely so the
    failure/compensation path is exercisable in tests without randomness.
    """

    name = "mock"

    _DECLINE_AMOUNT = Decimal("0.13")

    async def capture(self, transaction_id: uuid.UUID, amount: Decimal, currency: str) -> ProviderResult:
        if amount == self._DECLINE_AMOUNT:
            return ProviderResult(success=False, provider_reference=None, failure_reason="simulated provider decline")
        return ProviderResult(
            success=True, provider_reference=f"mock_{transaction_id.hex[:12]}", failure_reason=None
        )
