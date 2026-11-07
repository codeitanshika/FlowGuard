"""Read-only control-plane access specific to the eval harness — the
agent_decisions row behind a Healer incident, or behind a Fraud Agent
freeze/review decision, including the prompt_version/source columns the
report groups by. Reuses tests/chaos/db for anomalies/incidents rather
than duplicating that connection logic; see that module's docstring for
why each call opens its own short-lived engine."""

import uuid
from datetime import datetime

from sqlalchemy import select

from shared.control_plane import AgentDecision
from tests.chaos.db import _session  # noqa: F401 - reusing the same connection helper, not re-deriving it


async def decision_for_incident(incident_id: uuid.UUID) -> AgentDecision | None:
    async with _session() as db:
        result = await db.execute(
            select(AgentDecision)
            .where(AgentDecision.incident_id == incident_id)
            .order_by(AgentDecision.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()


async def latest_fraud_decision_after(user_id: uuid.UUID, after: datetime) -> AgentDecision | None:
    async with _session() as db:
        result = await db.execute(
            select(AgentDecision)
            .where(
                AgentDecision.agent == "fraud_agent",
                AgentDecision.created_at >= after,
                AgentDecision.input_summary["user_id"].as_string() == str(user_id),
            )
            .order_by(AgentDecision.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()
