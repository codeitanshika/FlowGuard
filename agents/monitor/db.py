import enum
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import DateTime, Numeric, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from agents.monitor.config import get_settings
from shared.db import create_engine, create_session_factory, ping_database

settings = get_settings()
engine = create_engine(settings.database_url)
SessionLocal = create_session_factory(engine)


class Base(DeclarativeBase):
    pass


class AnomalySeverity(str, enum.Enum):
    warning = "warning"
    critical = "critical"


class Anomaly(Base):
    """docs/architecture/05-database-schema.md, control-plane `anomalies`.
    p95_latency is stored in milliseconds, throughput in requests/second,
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
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


async def save_anomaly(
    db: AsyncSession,
    service: str,
    metric: str,
    observed_value: float,
    threshold: float,
    severity: str,
    trace_id: str | None,
) -> Anomaly:
    anomaly = Anomaly(
        service=service,
        metric=metric,
        observed_value=Decimal(str(round(observed_value, 6))),
        threshold=Decimal(str(threshold)),
        severity=AnomalySeverity(severity),
        trace_id=trace_id,
    )
    db.add(anomaly)
    await db.commit()
    await db.refresh(anomaly)
    return anomaly


async def is_ready() -> bool:
    return await ping_database(engine)
