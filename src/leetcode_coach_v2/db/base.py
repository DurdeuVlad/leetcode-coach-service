from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import MetaData
from sqlmodel import Session, SQLModel, create_engine


class V2SQLModel(SQLModel):
    """Separate metadata prevents V2 Alembic from managing legacy tables."""

    metadata = MetaData()


def create_v2_engine(database_url: str):
    return create_engine(database_url, pool_pre_ping=True, echo=False)


def get_session(engine) -> Iterator[Session]:
    with Session(engine) as session:
        yield session
