"""Expiry sweep tests (#028) — sweep_expired() per FR-3.

FR-3.1: select today's `pending_review` where `status = open`.
FR-3.2: for each, set `status = expired` + append "Expired without reply
        on <date>" to the Google Task's notes (do NOT delete the task).
FR-3.3: send exactly one Telegram summary message (or "No problems
        expired today" if none).

Test cases:
- 0 open rows → no Google Task update, summary says "No problems expired
  today", return 0.
- 1 open row → 1 Google Task update (notes_append), 1 row flipped to
  expired, summary lists 1 problem, return 1.
- 2 open rows → 2 Google Task updates, 2 rows flipped, summary lists 2,
  return 2.
- Idempotency: a second sweep on the same day finds 0 open rows (already
  expired) and sends the "No problems expired today" message — no double
  Google Task note append.
- A row whose `status != open` (e.g. `done`) is NOT touched.
- A Google Task PATCH failure on one row logs + continues; the DB row is
  still marked expired and the summary still lists it.
"""

from __future__ import annotations

import datetime
from unittest.mock import AsyncMock, patch

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from leetcode_coach.db import models as db_models
from leetcode_coach.flows import expiry as flow_expiry
from leetcode_coach.flows.expiry import sweep_expired

# --- fixtures ---------------------------------------------------------------
#
# In-memory SQLite (same pattern as test_flow_a.py / test_flow_b.py). The
# engine is patched into expiry's module so `next(get_session())` uses our
# test engine.


@pytest.fixture
def sqlite_session_factory(monkeypatch: pytest.MonkeyPatch):
    """Replace expiry's get_session with one backed by in-memory SQLite."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    SQLModel.metadata.create_all(engine)

    def _get_session():
        with Session(engine) as session:
            yield session

    monkeypatch.setattr(flow_expiry, "get_session", _get_session)
    return engine


def _insert_pending_review(
    engine,
    *,
    message_id: int,
    problem_slug: str = "problem-1",
    problem_title: str = "Problem 1",
    status: str = "open",
    google_task_id: str = "task-1",
    proposed_at: datetime.date | None = None,
) -> db_models.PendingReview:
    """Insert a pending_review row (and its leetcode_problems FK if missing)."""
    today = proposed_at or datetime.date.today()
    with Session(engine) as session:
        if session.get(db_models.LeetCodeProblem, problem_slug) is None:
            session.add(
                db_models.LeetCodeProblem(
                    slug=problem_slug,
                    title=problem_title,
                    url=f"https://leetcode.com/problems/{problem_slug}/",
                    difficulty="medium",
                    tags="array",
                    solved=False,
                )
            )
        row = db_models.PendingReview(
            message_id=message_id,
            google_task_id=google_task_id,
            problem_slug=problem_slug,
            problem_title=problem_title,
            proposed_at=today,
            status=status,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return row


# --- tests ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_zero_open_rows_sends_no_problems_message(sqlite_session_factory):
    """0 open rows → no Google Task update, summary says 'No problems
    expired today', return 0."""
    sent: list[str] = []

    with (
        patch.object(flow_expiry, "update_task", AsyncMock()) as update_mock,
        patch.object(
            flow_expiry, "send_message", AsyncMock(side_effect=lambda c, t: sent.append(t))
        ),
    ):
        count = await sweep_expired(chat_id="123")

    assert count == 0
    update_mock.assert_not_called()
    assert len(sent) == 1
    assert "No problems expired today" in sent[0]


@pytest.mark.asyncio
async def test_one_open_row_marked_expired_and_summary_lists_it(sqlite_session_factory):
    """1 open row → 1 Google Task update with notes_append, 1 row flipped to
    expired, summary lists 1 problem, return 1."""
    _insert_pending_review(
        sqlite_session_factory,
        message_id=100,
        problem_slug="two-sum",
        problem_title="Two Sum",
        google_task_id="task-A",
    )
    sent: list[str] = []
    updated_tasks: list[tuple[str, str]] = []

    async def _spy_update(task_id, *, notes_append=None):
        updated_tasks.append((task_id, notes_append or ""))

    with (
        patch.object(flow_expiry, "update_task", AsyncMock(side_effect=_spy_update)),
        patch.object(
            flow_expiry, "send_message", AsyncMock(side_effect=lambda c, t: sent.append(t))
        ),
    ):
        count = await sweep_expired(chat_id="123")

    assert count == 1
    assert len(updated_tasks) == 1
    assert updated_tasks[0][0] == "task-A"
    assert "Expired without reply on" in updated_tasks[0][1]
    assert len(sent) == 1
    assert "Two Sum" in sent[0]
    assert "1 problem" in sent[0]

    # DB row flipped to expired.
    with Session(sqlite_session_factory) as session:
        rows = session.exec(select(db_models.PendingReview)).all()
    assert len(rows) == 1
    assert rows[0].status == "expired"


@pytest.mark.asyncio
async def test_two_open_rows_all_marked_expired(sqlite_session_factory):
    """2 open rows → 2 Google Task updates, 2 rows flipped, summary lists 2,
    return 2."""
    _insert_pending_review(
        sqlite_session_factory,
        message_id=100,
        problem_slug="two-sum",
        problem_title="Two Sum",
        google_task_id="task-A",
    )
    _insert_pending_review(
        sqlite_session_factory,
        message_id=200,
        problem_slug="merge-intervals",
        problem_title="Merge Intervals",
        google_task_id="task-B",
    )
    sent: list[str] = []
    updated_tasks: list[str] = []

    async def _spy_update(task_id, *, notes_append=None):
        updated_tasks.append(task_id)

    with (
        patch.object(flow_expiry, "update_task", AsyncMock(side_effect=_spy_update)),
        patch.object(
            flow_expiry, "send_message", AsyncMock(side_effect=lambda c, t: sent.append(t))
        ),
    ):
        count = await sweep_expired(chat_id="123")

    assert count == 2
    assert sorted(updated_tasks) == ["task-A", "task-B"]
    assert len(sent) == 1
    assert "Two Sum" in sent[0]
    assert "Merge Intervals" in sent[0]
    assert "2 problem" in sent[0]

    with Session(sqlite_session_factory) as session:
        rows = session.exec(select(db_models.PendingReview)).all()
    assert all(r.status == "expired" for r in rows)


@pytest.mark.asyncio
async def test_idempotent_second_sweep_no_double_append(sqlite_session_factory):
    """A second sweep on the same day finds 0 open rows (already expired) →
    no Google Task update, 'No problems expired today' summary. This guards
    against a missed-cron catch-up double-appending the expiry note."""
    _insert_pending_review(
        sqlite_session_factory,
        message_id=100,
        problem_title="Two Sum",
        google_task_id="task-A",
    )

    with (
        patch.object(flow_expiry, "update_task", AsyncMock()),
        patch.object(flow_expiry, "send_message", AsyncMock()),
    ):
        first = await sweep_expired(chat_id="123")
        second = await sweep_expired(chat_id="123")

    assert first == 1
    assert second == 0


@pytest.mark.asyncio
async def test_done_row_not_touched(sqlite_session_factory):
    """A row with `status = done` is NOT expired by the sweep."""
    _insert_pending_review(
        sqlite_session_factory,
        message_id=100,
        problem_title="Two Sum",
        status="done",
        google_task_id="task-A",
    )
    sent: list[str] = []

    with (
        patch.object(flow_expiry, "update_task", AsyncMock()) as update_mock,
        patch.object(
            flow_expiry, "send_message", AsyncMock(side_effect=lambda c, t: sent.append(t))
        ),
    ):
        count = await sweep_expired(chat_id="123")

    assert count == 0
    update_mock.assert_not_called()
    assert "No problems expired today" in sent[0]

    with Session(sqlite_session_factory) as session:
        row = session.exec(select(db_models.PendingReview)).one()
    assert row.status == "done"


@pytest.mark.asyncio
async def test_google_task_failure_continues_sweep(sqlite_session_factory):
    """A Google Task PATCH failure on one row logs + continues: the DB row
    is still marked expired and the summary still lists it. One stale note
    is not worth aborting the whole sweep (NFR-1 layer 2)."""
    _insert_pending_review(
        sqlite_session_factory,
        message_id=100,
        problem_slug="two-sum",
        problem_title="Two Sum",
        google_task_id="task-A",
    )
    _insert_pending_review(
        sqlite_session_factory,
        message_id=200,
        problem_slug="merge-intervals",
        problem_title="Merge Intervals",
        google_task_id="task-B",
    )
    sent: list[str] = []
    call_count = 0

    async def _flaky_update(task_id, *, notes_append=None):
        nonlocal call_count
        call_count += 1
        if task_id == "task-A":
            raise RuntimeError("google tasks 500")

    with (
        patch.object(flow_expiry, "update_task", AsyncMock(side_effect=_flaky_update)),
        patch.object(
            flow_expiry, "send_message", AsyncMock(side_effect=lambda c, t: sent.append(t))
        ),
    ):
        count = await sweep_expired(chat_id="123")

    # Both rows attempted (failure on A did not abort the loop).
    assert call_count == 2
    # Both rows still flipped to expired in the DB.
    assert count == 2
    with Session(sqlite_session_factory) as session:
        rows = session.exec(select(db_models.PendingReview)).all()
    assert all(r.status == "expired" for r in rows)
    # Summary still lists both.
    assert "Two Sum" in sent[0]
    assert "Merge Intervals" in sent[0]


@pytest.mark.asyncio
async def test_yesterday_rows_not_touched(sqlite_session_factory):
    """FR-3.1: only TODAY's open rows are swept. Yesterday's open row is
    left alone (it's stale data, not today's sweep target)."""
    yesterday = datetime.date.today() - datetime.timedelta(days=1)
    _insert_pending_review(
        sqlite_session_factory,
        message_id=100,
        problem_title="Old Problem",
        google_task_id="task-A",
        proposed_at=yesterday,
    )
    sent: list[str] = []

    with (
        patch.object(flow_expiry, "update_task", AsyncMock()) as update_mock,
        patch.object(
            flow_expiry, "send_message", AsyncMock(side_effect=lambda c, t: sent.append(t))
        ),
    ):
        count = await sweep_expired(chat_id="123")

    assert count == 0
    update_mock.assert_not_called()
    assert "No problems expired today" in sent[0]

    with Session(sqlite_session_factory) as session:
        row = session.exec(select(db_models.PendingReview)).one()
    assert row.status == "open"  # unchanged
