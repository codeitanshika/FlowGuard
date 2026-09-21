import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_fault_injector
from app.db.session import get_db
from app.models.schemas import BalanceAdjustmentRequest, BalanceResponse, FreezeRequest, UserResponse
from app.services import user_service
from shared.fault_injection import FaultInjectionRequest, FaultInjector
from shared.schemas import Envelope

# Everything under /internal is never routed by the Gateway (see
# docs/architecture/03-service-boundaries.md) — reachable only on the
# internal network, by the specific caller that owns each relationship.
router = APIRouter(prefix="/internal", tags=["internal"])


@router.patch("/users/{user_id}/freeze", response_model=Envelope[UserResponse])
async def freeze_user(
    user_id: uuid.UUID, payload: FreezeRequest, db: AsyncSession = Depends(get_db)
) -> Envelope[UserResponse]:
    user = await user_service.freeze(db, user_id, payload.reason, payload.source, payload.risk_assessment_id)
    return Envelope(data=UserResponse.model_validate(user))


@router.patch("/users/{user_id}/unfreeze", response_model=Envelope[UserResponse])
async def unfreeze_user(
    user_id: uuid.UUID, payload: FreezeRequest, db: AsyncSession = Depends(get_db)
) -> Envelope[UserResponse]:
    user = await user_service.unfreeze(db, user_id, payload.reason, payload.source)
    return Envelope(data=UserResponse.model_validate(user))


@router.post("/users/{user_id}/debit", response_model=Envelope[BalanceResponse])
async def debit_user(
    user_id: uuid.UUID, payload: BalanceAdjustmentRequest, db: AsyncSession = Depends(get_db)
) -> Envelope[BalanceResponse]:
    user = await user_service.debit(db, user_id, payload.amount)
    return Envelope(data=BalanceResponse(balance=user.balance, currency=user.currency))


@router.post("/users/{user_id}/credit", response_model=Envelope[BalanceResponse])
async def credit_user(
    user_id: uuid.UUID, payload: BalanceAdjustmentRequest, db: AsyncSession = Depends(get_db)
) -> Envelope[BalanceResponse]:
    user = await user_service.credit(db, user_id, payload.amount)
    return Envelope(data=BalanceResponse(balance=user.balance, currency=user.currency))


@router.post("/fault-injection", response_model=Envelope[dict])
async def configure_fault(
    payload: FaultInjectionRequest, injector: FaultInjector = Depends(get_fault_injector)
) -> Envelope[dict]:
    await injector.enable(payload.mode, payload.error_rate, payload.latency_ms, payload.duration_seconds)
    return Envelope(data={"status": "enabled"})


@router.delete("/fault-injection", response_model=Envelope[dict])
async def clear_fault(injector: FaultInjector = Depends(get_fault_injector)) -> Envelope[dict]:
    await injector.disable()
    return Envelope(data={"status": "cleared"})
