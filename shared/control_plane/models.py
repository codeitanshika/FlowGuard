"""Control-plane tables (flowguard_control), shared by the Monitor, Healer
and Ops Controller — see docs/architecture/05-database-schema.md. One
metadata object on purpose: incidents reference anomalies by foreign key,
so create_all needs to see both."""

import enum
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


class AnomalySeverity(str, enum.Enum):
    warning = "warning"
    critical = "critical"


class Anomaly(Base):
    """p95_latency is stored in milliseconds, throughput in requests/second,
    error_rate as a fraction."""

    __tablename__ = "anomalies"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    service: Mapped[str] = mapped_column(String, nullable=False, index=True)
    metric: Mapped[str] = mapped_column(String, nullable=False)
    observed_value: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    threshold: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    severity: Mapped[AnomalySeverity] = mapped_column(
        SAEnum(AnomalySeverity, name="anomaly_severity", native_enum=True), nullable=False
    )
    trace_id: Mapped[str | None] = mapped_column(String, nullable=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class IncidentStatus(str, enum.Enum):
    diagnosing = "diagnosing"
    remediating = "remediating"
    resolved = "resolved"
    failed = "failed"


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    anomaly_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("anomalies.id"), nullable=False, index=True
    )
    status: Mapped[IncidentStatus] = mapped_column(
        SAEnum(IncidentStatus, name="incident_status", native_enum=True),
        nullable=False,
        default=IncidentStatus.diagnosing,
    )
    root_cause: Mapped[str | None] = mapped_column(Text, nullable=True)
    action_taken: Mapped[str | None] = mapped_column(Text, nullable=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentDecision(Base):
    """Audit record of one agent decision: what it saw, what it decided,
    whether it passed validation, whether it was executed. llm_* columns
    are null when the decision came from the deterministic rules."""

    __tablename__ = "agent_decisions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    incident_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("incidents.id"), nullable=True, index=True
    )
    agent: Mapped[str] = mapped_column(String, nullable=False)
    input_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    llm_model: Mapped[str | None] = mapped_column(String, nullable=True)
    llm_prompt_version: Mapped[str | None] = mapped_column(String, nullable=True)
    llm_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    llm_tokens_in: Mapped[int | None] = mapped_column(Integer, nullable=True)
    llm_tokens_out: Mapped[int | None] = mapped_column(Integer, nullable=True)
    decision: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    validated: Mapped[bool] = mapped_column(Boolean, nullable=False)
    executed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class OpsAction(Base):
    """Ops Controller audit log. A row is written as `received` before
    anything is validated or executed, then updated — so a rejected or
    crashed call still leaves a record (ADR-0005)."""

    __tablename__ = "ops_actions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    action: Mapped[str] = mapped_column(String, nullable=False, index=True)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    caller: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)  # received|rejected|executed|failed
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
