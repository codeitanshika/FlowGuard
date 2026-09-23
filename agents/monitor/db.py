from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from agents.monitor.config import get_settings
from shared.control_plane import Anomaly, AnomalySeverity
from shared.db import create_engine, create_session_factory, ping_database

settings = get_settings()
engine = create_engine(settings.database_url)
SessionLocal = create_session_factory(engine)


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
