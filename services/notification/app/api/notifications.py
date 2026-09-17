import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import repository
from app.db.session import get_db
from app.models.schemas import NotificationResponse
from shared.schemas import Envelope

router = APIRouter(tags=["notifications"])


@router.get("/notifications", response_model=Envelope[list[NotificationResponse]])
async def list_notifications(
    user_id: uuid.UUID = Query(...), db: AsyncSession = Depends(get_db)
) -> Envelope[list[NotificationResponse]]:
    notifications = await repository.list_by_user(db, user_id)
    return Envelope(data=[NotificationResponse.model_validate(n) for n in notifications])
