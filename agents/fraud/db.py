import uuid
from typing import Any

from agents.fraud.config import get_settings
from shared.control_plane import AgentDecision
from shared.db import create_engine, create_session_factory, ping_database

settings = get_settings()
engine = create_engine(settings.database_url)
SessionLocal = create_session_factory(engine)


async def record_decision(
    input_summary: dict[str, Any],
    decision: dict[str, Any],
    executed: bool,
    llm_model: str | None = None,
    llm_prompt_version: str | None = None,
    llm_latency_ms: int | None = None,
    llm_tokens_in: int | None = None,
    llm_tokens_out: int | None = None,
) -> uuid.UUID:
    """Fraud Agent decisions have no incident to attach to (that's the
    Healer's table's other use — see shared/control_plane) so incident_id
    is always null here. Always validated=True: unlike the Healer, there's
    no allowlist to fail — the LLM's output shape makes an invalid
    "decision" (i.e. a freeze) structurally impossible (schemas.FraudNarrative)."""

    async with SessionLocal() as db:
        row = AgentDecision(
            incident_id=None,
            agent="fraud_agent",
            input_summary=input_summary,
            decision=decision,
            validated=True,
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


async def is_ready() -> bool:
    return await ping_database(engine)
