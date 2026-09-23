import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from agents.healer.config import get_settings
from shared.control_plane import AgentDecision, Incident, IncidentStatus
from shared.db import create_engine, create_session_factory, ping_database

settings = get_settings()
engine = create_engine(settings.database_url)
SessionLocal = create_session_factory(engine)


async def create_incident(anomaly_id: uuid.UUID) -> Incident:
    async with SessionLocal() as db:
        incident = Incident(anomaly_id=anomaly_id, status=IncidentStatus.diagnosing)
        db.add(incident)
        await db.commit()
        await db.refresh(incident)
        return incident


async def update_incident(
    incident_id: uuid.UUID,
    status: IncidentStatus,
    root_cause: str | None = None,
    action_taken: str | None = None,
) -> datetime | None:
    values: dict[str, Any] = {"status": status}
    if root_cause is not None:
        values["root_cause"] = root_cause
    if action_taken is not None:
        values["action_taken"] = action_taken
    resolved_at = None
    if status in (IncidentStatus.resolved, IncidentStatus.failed):
        resolved_at = datetime.now(timezone.utc)
        values["resolved_at"] = resolved_at
    async with SessionLocal() as db:
        await db.execute(update(Incident).where(Incident.id == incident_id).values(**values))
        await db.commit()
    return resolved_at


async def record_decision(
    incident_id: uuid.UUID,
    input_summary: dict[str, Any],
    decision: dict[str, Any],
    validated: bool,
    executed: bool,
    llm_model: str | None = None,
    llm_prompt_version: str | None = None,
    llm_latency_ms: int | None = None,
    llm_tokens_in: int | None = None,
    llm_tokens_out: int | None = None,
) -> uuid.UUID:
    async with SessionLocal() as db:
        row = AgentDecision(
            incident_id=incident_id,
            agent="healer",
            input_summary=input_summary,
            decision=decision,
            validated=validated,
            executed=executed,
            llm_model=llm_model,
            llm_prompt_version=llm_prompt_version,
            llm_latency_ms=llm_latency_ms,
            llm_tokens_in=llm_tokens_in,
            llm_tokens_out=llm_tokens_out,
        )
        db.add(row)
        await db.commit()
        return row.id


async def mark_decision_executed(decision_id: uuid.UUID) -> None:
    async with SessionLocal() as db:
        await db.execute(update(AgentDecision).where(AgentDecision.id == decision_id).values(executed=True))
        await db.commit()


async def fail_stale_incidents() -> int:
    """Incidents are worked by in-process tasks, so any still open when the
    Healer starts belong to a previous process that is gone. Close them
    explicitly rather than leaving them 'diagnosing' forever."""
    async with SessionLocal() as db:
        result = await db.execute(
            update(Incident)
            .where(Incident.status.in_([IncidentStatus.diagnosing, IncidentStatus.remediating]))
            .values(
                status=IncidentStatus.failed,
                action_taken="abandoned: Healer restarted before the incident finished",
                resolved_at=datetime.now(timezone.utc),
            )
        )
        await db.commit()
        return result.rowcount or 0


async def is_ready() -> bool:
    return await ping_database(engine)
