import uuid

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_orchestrator
from app.db import repository
from app.db.session import get_db
from app.models.schemas import PaymentRequest, PaymentResponse
from app.services.payment_orchestrator import PaymentOrchestrator
from shared.errors import NotFoundError
from shared.schemas import Envelope

router = APIRouter(tags=["payments"])


@router.post("/payments", response_model=Envelope[PaymentResponse], status_code=201)
async def create_payment(
    request: Request,
    payload: PaymentRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    db: AsyncSession = Depends(get_db),
    orchestrator: PaymentOrchestrator = Depends(get_orchestrator),
) -> Envelope[PaymentResponse]:
    trace_id = request.headers.get("X-Trace-Id", "unknown")
    result = await orchestrator.create_payment(db, payload, idempotency_key, trace_id)
    return Envelope(data=result)


@router.get("/payments/{transaction_id}", response_model=Envelope[PaymentResponse])
async def get_payment(
    transaction_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> Envelope[PaymentResponse]:
    txn = await repository.get_transaction(db, transaction_id)
    if txn is None:
        raise NotFoundError(f"transaction {transaction_id} not found")
    return Envelope(data=PaymentResponse.model_validate(txn))


@router.get("/payments", response_model=Envelope[list[PaymentResponse]])
async def list_payments(
    user_id: uuid.UUID | None = None, db: AsyncSession = Depends(get_db)
) -> Envelope[list[PaymentResponse]]:
    txns = await repository.list_transactions(db, user_id)
    return Envelope(data=[PaymentResponse.model_validate(t) for t in txns])
