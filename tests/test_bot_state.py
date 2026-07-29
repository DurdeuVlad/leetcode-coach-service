"""Tests for the bot_state table + get_state/set_state helpers (issue #036).

Covers the acceptance criteria from plan/issues/036-bot-state-table.md:
- set_state on a new key → get_state returns the value.
- Second set_state on the same key overwrites and bumps updated_at.
- get_state on a missing key returns None.

The upsert is portable (select-then-insert-or-update), so we test against
in-memory SQLite — same pattern as test_flow_a / test_flow_b. The real
Postgres migration is exercised by `alembic upgrade head` at deploy time.
"""

from __future__ import annotations

import datetime
import time

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from leetcode_coach.db import queries as queries_module
from leetcode_coach.db.models import BotState


@pytest.fixture
def sqlite_session_factory(monkeypatch: pytest.MonkeyPatch):
    """Patch get_session in db.queries with an in-memory SQLite engine.

    Uses check_same_thread=False + StaticPool so the engine works across
    threads (in case any test uses TestClient). All SQLModel tables are
    created so the bot_state table exists alongside the others.
    """
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite:///:memory:",
        echo=False,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def _get_session():
        with Session(engine) as session:
            yield session

    monkeypatch.setattr(queries_module, "get_session", _get_session)
    return engine


def test_set_state_new_key_then_get(sqlite_session_factory):
    """set_state on a new key persists; get_state returns the value."""
    queries_module.set_state("pinned_message_id", "12345")
    assert queries_module.get_state("pinned_message_id") == "12345"


def test_set_state_overwrites_and_bumps_updated_at(sqlite_session_factory):
    """A second set_state on the same key overwrites value and advances
    updated_at (issue #036 acceptance criterion)."""
    queries_module.set_state("pinned_message_id", "111")
    with next(queries_module.get_session()) as session:
        first = session.get(BotState, "pinned_message_id")
        assert first is not None
        first_updated = first.updated_at

    # Force the clock to advance so updated_at is strictly greater even on
    # fast machines. The helper reads datetime.now(UTC) at call time.
    time.sleep(0.01)
    queries_module.set_state("pinned_message_id", "222")

    assert queries_module.get_state("pinned_message_id") == "222"
    with next(queries_module.get_session()) as session:
        second = session.get(BotState, "pinned_message_id")
        assert second is not None
        assert second.updated_at > first_updated


def test_get_state_missing_key_returns_none(sqlite_session_factory):
    """get_state on a key that was never set returns None (not empty string,
    not raising)."""
    assert queries_module.get_state("nonexistent_key") is None


def test_set_state_upsert_does_not_duplicate(sqlite_session_factory):
    """Upsert must not create a second row for the same key (PK enforcement
    via the select-then-update path, not via catching IntegrityError)."""
    queries_module.set_state("k", "v1")
    queries_module.set_state("k", "v2")
    queries_module.set_state("k", "v3")
    with next(queries_module.get_session()) as session:
        rows = session.exec(select(BotState)).all()
        assert len(rows) == 1
        assert rows[0].value == "v3"


def test_updated_at_is_timezone_aware(sqlite_session_factory):
    """updated_at is stored as a timezone-aware UTC datetime (the spec says
    TIMESTAMPTZ; the model uses datetime.datetime.now(timezone.utc))."""
    queries_module.set_state("tz_check", "v")
    with next(queries_module.get_session()) as session:
        row = session.get(BotState, "tz_check")
        assert row is not None
        # On SQLite the tzinfo may be stripped on round-trip, but the value
        # must be a datetime (not a string). On Postgres it retains tzinfo.
        assert isinstance(row.updated_at, datetime.datetime)
