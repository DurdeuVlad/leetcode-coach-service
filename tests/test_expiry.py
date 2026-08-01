from __future__ import annotations

import datetime
from unittest.mock import AsyncMock

import pytest
from sqlmodel import Session, SQLModel, create_engine

from leetcode_coach.db import models
from leetcode_coach.flows import expiry


@pytest.fixture
def sqlite_session_factory(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)

    def _get_session():
        with Session(engine) as session:
            yield session

    monkeypatch.setattr(expiry, "get_session", _get_session)
    monkeypatch.setattr(expiry, "edit_message_reply_markup", AsyncMock())
    return engine


def _review(factory, *, proposed_at: datetime.date, status: str = "open") -> int:
    with Session(factory) as session:
        session.add(models.LeetCodeProblem(slug=f"p-{proposed_at}-{status}", title="Problem", url="https://leetcode.com/problems/p/", difficulty="medium"))
        row = models.PendingReview(message_id=100 + proposed_at.day, problem_slug=f"p-{proposed_at}-{status}", problem_title="Problem", proposed_at=proposed_at, status=status)
        session.add(row)
        session.commit()
        session.refresh(row)
        assert row.id is not None
        return row.id


@pytest.mark.asyncio
async def test_yesterday_open_review_expires_without_notification(sqlite_session_factory, monkeypatch):
    yesterday = datetime.date.today() - datetime.timedelta(days=1)
    row_id = _review(sqlite_session_factory, proposed_at=yesterday)
    result = await expiry.sweep_expired(chat_id="123456")
    assert result == 1
    with Session(sqlite_session_factory) as session:
        assert session.get(models.PendingReview, row_id).status == models.ReviewStatus.EXPIRED


@pytest.mark.asyncio
async def test_empty_sweep_is_silent(sqlite_session_factory, monkeypatch):
    assert await expiry.sweep_expired(chat_id="123456") == 0


@pytest.mark.asyncio
async def test_current_day_review_expires_at_tonight(sqlite_session_factory):
    row_id = _review(sqlite_session_factory, proposed_at=datetime.date.today())
    assert await expiry.sweep_expired(chat_id="123456") == 1
    with Session(sqlite_session_factory) as session:
        assert session.get(models.PendingReview, row_id).status == models.ReviewStatus.EXPIRED


@pytest.mark.asyncio
async def test_terminal_review_is_not_changed(sqlite_session_factory):
    row_id = _review(sqlite_session_factory, proposed_at=datetime.date.today() - datetime.timedelta(days=1), status="done")
    assert await expiry.sweep_expired(chat_id="123456") == 0
    with Session(sqlite_session_factory) as session:
        assert session.get(models.PendingReview, row_id).status == models.ReviewStatus.DONE


@pytest.mark.asyncio
async def test_extension_defers_expiry_and_expired_thread_buttons_are_removed(
    sqlite_session_factory, monkeypatch
):
    today = datetime.date.today()
    with Session(sqlite_session_factory) as session:
        session.add(models.LeetCodeProblem(slug="extended", title="Extended", url="https://leetcode.com/problems/extended/", difficulty="medium"))
        batch = models.ProposalBatch(
            proposed_at=today,
            status=models.ProposalBatchStatus.ACTIVE,
            expires_at=today,
            extended_until=today + datetime.timedelta(days=1),
        )
        session.add(batch)
        session.flush()
        session.add(models.PendingReview(message_id=321, problem_slug="extended", problem_title="Extended", proposed_at=today, batch_id=batch.id, status=models.ReviewStatus.OPEN))
        session.commit()

    edit = AsyncMock()
    monkeypatch.setattr(expiry, "edit_message_reply_markup", edit)
    assert await expiry.sweep_expired(chat_id="123456") == 0
    edit.assert_not_awaited()
