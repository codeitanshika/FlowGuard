import json
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from shared.control_plane import OpsAction

_MAX_PARAMS_CHARS = 2000


def _bounded(raw: object) -> dict:
    """Audit what was actually received, but never let a caller stuff an
    unbounded blob into the audit table."""
    try:
        text = json.dumps(raw, default=str)
    except (TypeError, ValueError):
        return {"_unserializable": True}
    if len(text) > _MAX_PARAMS_CHARS:
        return {"_truncated": True, "_preview": text[:_MAX_PARAMS_CHARS]}
    return raw if isinstance(raw, dict) else {"_value": raw}


async def record_received(db: AsyncSession, action: str, raw_params: object, caller: str) -> OpsAction:
    """Written and committed before authentication is acted on, validation,
    or execution — so every call, including rejected and crashed ones,
    leaves a row (ADR-0005)."""
    row = OpsAction(action=action, params=_bounded(raw_params), caller=caller, status="received")
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def finish(db: AsyncSession, row: OpsAction, status: str, detail: str) -> None:
    row.status = status
    row.detail = detail[:1000]
    row.completed_at = datetime.now(UTC)
    await db.commit()
