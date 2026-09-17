from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class ErrorDetail(BaseModel):
    code: str
    message: str | list | dict


class Envelope(BaseModel, Generic[T]):
    """The one response shape every FlowGuard endpoint returns, per
    docs/api/api-contracts.md. Route handlers only ever set `data`; `error`
    is populated exclusively by shared/exception_handlers.py so success and
    failure paths can't drift out of sync with each other."""

    data: T | None = None
    error: ErrorDetail | None = None
