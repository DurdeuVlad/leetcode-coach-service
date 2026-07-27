"""SQLModel engine + session factory.

Single responsibility (per #034): hold the engine and produce sessions.
No business state, no HTTP, no flow decisions. Table models live in
`models.py` (filled in by a later phase) and are imported by Alembic's
`env.py` so migrations can see them.
"""

from __future__ import annotations

from collections.abc import Iterator

from sqlmodel import Session, SQLModel, create_engine

from leetcode_coach.config import get_settings

# Engine is created once at import time from the configured DATABASE_URL.
# `pool_pre_ping` catches stale connections (Coolify restarts, Postgres
# failovers) without raising to the caller.
_settings = get_settings()
engine = create_engine(_settings.database_url, pool_pre_ping=True, echo=False)


def get_session() -> Iterator[Session]:
    """FastAPI dependency yielding a Session; closes on exit."""
    with Session(engine) as session:
        yield session


# Re-export SQLModel so model modules do `from leetcode_coach.db.base import SQLModel`.
__all__ = ["SQLModel", "engine", "get_session"]
