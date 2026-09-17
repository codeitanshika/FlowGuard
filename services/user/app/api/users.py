import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.schemas import BalanceResponse, UserCreate, UserResponse
from app.services import user_service
from shared.schemas import Envelope

router = APIRouter(tags=["users"])


@router.post("/users", response_model=Envelope[UserResponse], status_code=201)
async def create_user(payload: UserCreate, db: AsyncSession = Depends(get_db)) -> Envelope[UserResponse]:
    user = await user_service.create_user(db, payload.email, payload.full_name, payload.currency)
    return Envelope(data=UserResponse.model_validate(user))


@router.get("/users/{user_id}", response_model=Envelope[UserResponse])
async def get_user(user_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> Envelope[UserResponse]:
    user = await user_service.get_user(db, user_id)
    return Envelope(data=UserResponse.model_validate(user))


@router.get("/users/{user_id}/balance", response_model=Envelope[BalanceResponse])
async def get_balance(user_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> Envelope[BalanceResponse]:
    user = await user_service.get_user(db, user_id)
    return Envelope(data=BalanceResponse(balance=user.balance, currency=user.currency))
