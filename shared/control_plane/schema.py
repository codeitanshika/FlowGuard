from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from shared.control_plane.models import Base

# Arbitrary constant; any two processes using the same value serialize.
_SCHEMA_LOCK_KEY = 7801


async def init_control_plane_schema(engine: AsyncEngine) -> None:
    """Monitor, Healer and Ops Controller all start together and all call
    this. create_all is not safe to run concurrently against a fresh
    database (two processes both trying to create the same enum type or
    table fail), so hold a transaction-scoped Postgres advisory lock:
    whoever gets it creates the schema, the others wait, then find
    everything already there."""

    async with engine.begin() as conn:
        await conn.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _SCHEMA_LOCK_KEY})
        await conn.run_sync(Base.metadata.create_all)
