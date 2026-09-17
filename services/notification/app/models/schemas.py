import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class NotificationResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    transaction_id: uuid.UUID | None
    channel: Literal["email", "webhook", "log"]
    template: str
    status: Literal["sent", "failed"]
    payload: dict
    created_at: datetime

    model_config = {"from_attributes": True}
