from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import MetaData
from sqlmodel import Session, SQLModel, create_engine


class BaseSQLModel(SQLModel):
    """Separate metadata for Alembic-managed tables."""

    metadata = MetaData()


def create_db_engine(database_url: str):
    return create_engine(database_url, pool_pre_ping=True, echo=False)


def get_session(engine) -> Iterator[Session]:
    with Session(engine) as session:
        yield session
