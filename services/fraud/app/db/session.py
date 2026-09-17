from shared.db import build_get_db, create_engine, create_session_factory, ping_database

from app.core.config import get_settings

settings = get_settings()
engine = create_engine(settings.database_url)
SessionLocal = create_session_factory(engine)
get_db = build_get_db(SessionLocal)


async def is_ready() -> bool:
    return await ping_database(engine)
