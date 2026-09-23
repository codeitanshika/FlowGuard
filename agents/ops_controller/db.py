from agents.ops_controller.config import get_settings
from shared.db import create_engine, create_session_factory, ping_database

settings = get_settings()
engine = create_engine(settings.database_url)
SessionLocal = create_session_factory(engine)


async def is_ready() -> bool:
    return await ping_database(engine)
