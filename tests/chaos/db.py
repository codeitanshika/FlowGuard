"""Direct, read-only access to flowguard_control for chaos tests — the
only place in this test suite that needs the docker-compose.test.yml
port-publishing override, since it's the only place that needs
authoritative timestamps (anomalies.detected_at, incidents.resolved_at)
rather than inferring recovery from black-box HTTP behavior.

Opens a fresh engine per call rather than one shared, pooled, module-level
engine: pytest-asyncio gives each test function its own event loop by
default, and a connection pool created under one loop breaks (loudly, in
teardown) if reused from another — this module is called only a handful
of times per test run, so the extra connection setup per call is cheap
and sidesteps that entirely."""

import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from sqlalchemy import select

from shared.control_plane import Anomaly, Incident
from shared.db import create_engine, create_session_factory

CONTROL_DB_URL = os.environ.get(
    "FLOWGUARD_CONTROL_DATABASE_URL",
    "postgresql+asyncpg://flowguard:flowguard@localhost:5432/flowguard_control",
)


@asynccontextmanager
async def _session():
    engine = create_engine(CONTROL_DB_URL)
    try:
        async with create_session_factory(engine)() as db:
            yield db
    finally:
        await engine.dispose()


async def check_control_db_is_reachable() -> None:
    async with _session() as db:
        await db.execute(select(1))


async def latest_anomaly_after(service: str, metric: str, after: datetime) -> Anomaly | None:
    async with _session() as db:
        result = await db.execute(
            select(Anomaly)
            .where(Anomaly.service == service, Anomaly.metric == metric, Anomaly.detected_at >= after)
            .order_by(Anomaly.detected_at.asc())
            .limit(1)
        )
        return result.scalar_one_or_none()


async def incident_for_anomaly(anomaly_id) -> Incident | None:
    async with _session() as db:
        result = await db.execute(
            select(Incident).where(Incident.anomaly_id == anomaly_id).order_by(Incident.opened_at.desc()).limit(1)
        )
        return result.scalar_one_or_none()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
