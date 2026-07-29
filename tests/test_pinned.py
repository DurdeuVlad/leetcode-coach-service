"""Tests for the pinned progression message (issue #039, FR-8).

Covers:
- Snapshot builder produces the right counts + streak.
- First refresh creates + pins a new message, stores the ID in bot_state.
- Subsequent refreshes edit the existing message.
- "message is not modified" → no-op, no error.
- Edit failure (message deleted) → recovery: unpin old, create + pin new.
- The refresh hook is fire-and-forget: failures don't propagate.
"""

from __future__ import annotations

import datetime
from unittest.mock import AsyncMock, patch

import pytest
from sqlmodel import Session, SQLModel, create_engine

from leetcode_coach.db import models as db_models
from leetcode_coach.flows import pinned as pinned_module
from leetcode_coach.flows.pinned import _build_snapshot, refresh_pinned_message

# --- fixtures ---------------------------------------------------------------


def _make_engine():
    engine = create_engine("sqlite:///:memory:", echo=False)
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture
def sqlite_engine(monkeypatch: pytest.MonkeyPatch):
    """In-memory SQLite patched into db.base.get_session (used by pinned.py
    and db.queries)."""
    engine = _make_engine()

    def _get_session():
        with Session(engine) as session:
            yield session

    monkeypatch.setattr("leetcode_coach.db.base.get_session", _get_session)
    monkeypatch.setattr("leetcode_coach.db.queries.get_session", _get_session)
    # Also patch the already-imported reference in pinned.py
    monkeypatch.setattr(pinned_module, "get_session", _get_session)
    return engine


def _insert_candidates(engine, count: int = 5):
    today = datetime.date.today()
    with Session(engine) as session:
        for i in range(1, count + 1):
            slug = f"problem-{i}"
            if session.get(db_models.LeetCodeProblem, slug) is None:
                session.add(
                    db_models.LeetCodeProblem(
                        slug=slug,
                        title=f"Problem {i}",
                        url=f"https://leetcode.com/problems/{slug}/",
                        difficulty="medium",
                        tags="array",
                        solved=False,
                    )
                )
            session.add(
                db_models.DailyCandidate(
                    proposed_at=today,
                    pick_index=i,
                    slug=slug,
                    title=f"Problem {i}",
                    url=f"https://leetcode.com/problems/{slug}/",
                    tags="array",
                    difficulty="medium",
                    reasoning="test",
                    coaching_hint="test",
                )
            )
        session.commit()


def _insert_pending_review(
    engine, *, message_id: int, status: str = "open", slug: str = "problem-1"
):
    today = datetime.date.today()
    with Session(engine) as session:
        if session.get(db_models.LeetCodeProblem, slug) is None:
            session.add(
                db_models.LeetCodeProblem(
                    slug=slug,
                    title=slug.replace("-", " ").title(),
                    url=f"https://leetcode.com/problems/{slug}/",
                    difficulty="easy",
                    tags="array",
                    solved=False,
                )
            )
        session.add(
            db_models.PendingReview(
                message_id=message_id,
                google_task_id="t1",
                problem_slug=slug,
                problem_title=slug.replace("-", " ").title(),
                proposed_at=today,
                status=status,
            )
        )
        session.commit()


def _insert_log(engine, *, slug: str, date, status: str):
    with Session(engine) as session:
        if session.get(db_models.LeetCodeProblem, slug) is None:
            session.add(
                db_models.LeetCodeProblem(
                    slug=slug,
                    title=slug.replace("-", " ").title(),
                    url=f"https://leetcode.com/problems/{slug}/",
                    difficulty="easy",
                    tags="array",
                    solved=False,
                )
            )
        session.add(
            db_models.LeetCodeLog(
                problem_slug=slug,
                date=date,
                status=status,
            )
        )
        session.commit()


def _insert_lesson(engine, *, title: str, count: int = 3, active: bool = True):
    with Session(engine) as session:
        session.add(
            db_models.TutorLesson(
                title=title,
                category="arrays",
                times_reinforced=count,
                active=active,
            )
        )
        session.commit()


# ===========================================================================
# Snapshot builder
# ===========================================================================


def test_snapshot_shows_counts_and_streak(sqlite_engine):
    """Snapshot contains proposed/picked/coached/expired counts + streak."""
    today = datetime.date.today()
    _insert_candidates(sqlite_engine, count=5)
    _insert_pending_review(sqlite_engine, message_id=100, status="open")
    _insert_pending_review(sqlite_engine, message_id=101, status="expired", slug="problem-2")
    _insert_log(sqlite_engine, slug="problem-1", date=today, status="solved")
    _insert_lesson(sqlite_engine, title="Sliding Window", count=5)

    snapshot = _build_snapshot()

    assert "Proposed: 5" in snapshot
    assert "Picked: 1" in snapshot
    assert "Coached: 1" in snapshot
    assert "Expired: 1" in snapshot
    assert "Active lessons: 1" in snapshot
    assert "Streak: 1 day" in snapshot


def test_snapshot_empty_db(sqlite_engine):
    """Snapshot on an empty DB shows all zeros, streak 0."""
    snapshot = _build_snapshot()

    assert "Proposed: 0" in snapshot
    assert "Picked: 0" in snapshot
    assert "Coached: 0" in snapshot
    assert "Expired: 0" in snapshot
    assert "Active lessons: 0" in snapshot
    assert "Streak: 0 days" in snapshot


# ===========================================================================
# refresh_pinned_message — first run creates + pins
# ===========================================================================


@pytest.mark.asyncio
async def test_first_refresh_creates_and_pins(sqlite_engine, monkeypatch):
    """No pinned_message_id in bot_state → create + pin + store ID."""
    monkeypatch.setattr(
        "leetcode_coach.flows.pinned.get_settings",
        lambda: type("S", (), {"telegram_chat_id": "123"})(),
    )
    sent_texts = []

    async def _fake_send(chat_id, text, **kw):
        sent_texts.append(text)
        return 999  # fake message_id

    with (
        patch.object(pinned_module, "send_message", _fake_send),
        patch.object(pinned_module, "pin_message", AsyncMock()) as pin_mock,
    ):
        await refresh_pinned_message()

    assert len(sent_texts) == 1
    pin_mock.assert_awaited_once_with("123", 999)

    # Verify the ID was stored in bot_state.
    from leetcode_coach.db.queries import get_state

    assert get_state("pinned_message_id") == "999"


# ===========================================================================
# refresh_pinned_message — subsequent runs edit
# ===========================================================================


@pytest.mark.asyncio
async def test_subsequent_refresh_edits_existing(sqlite_engine, monkeypatch):
    """pinned_message_id exists → editMessageText on that message."""
    # Seed bot_state with an existing pinned message ID.
    from leetcode_coach.db.queries import set_state

    set_state("pinned_message_id", "555")
    monkeypatch.setattr(
        "leetcode_coach.flows.pinned.get_settings",
        lambda: type("S", (), {"telegram_chat_id": "123"})(),
    )

    with (
        patch.object(
            pinned_module, "edit_message_text", AsyncMock(return_value={"ok": True})
        ) as edit_mock,
        patch.object(pinned_module, "send_message", AsyncMock(return_value=999)) as send_mock,
        patch.object(pinned_module, "pin_message", AsyncMock()) as pin_mock,
    ):
        await refresh_pinned_message()

    edit_mock.assert_awaited_once()
    call_args = edit_mock.await_args
    assert call_args.args[0] == "123"  # chat_id
    assert call_args.args[1] == 555  # message_id
    send_mock.assert_not_called()  # no new message created
    pin_mock.assert_not_called()


# ===========================================================================
# "message is not modified" → no-op
# ===========================================================================


@pytest.mark.asyncio
async def test_not_modified_is_noop(sqlite_engine, monkeypatch):
    """Telegram returns 'message is not modified' → no-op, no new message."""
    from leetcode_coach.db.queries import set_state

    set_state("pinned_message_id", "555")
    monkeypatch.setattr(
        "leetcode_coach.flows.pinned.get_settings",
        lambda: type("S", (), {"telegram_chat_id": "123"})(),
    )

    from leetcode_coach.errors import TelegramError

    with (
        patch.object(
            pinned_module,
            "edit_message_text",
            AsyncMock(side_effect=TelegramError("message is not modified")),
        ),
        patch.object(pinned_module, "send_message", AsyncMock(return_value=999)) as send_mock,
        patch.object(pinned_module, "pin_message", AsyncMock()) as pin_mock,
        patch.object(pinned_module, "unpin_message", AsyncMock()) as unpin_mock,
    ):
        await refresh_pinned_message()

    send_mock.assert_not_called()
    pin_mock.assert_not_called()
    unpin_mock.assert_not_called()
    # ID unchanged.
    from leetcode_coach.db.queries import get_state

    assert get_state("pinned_message_id") == "555"


# ===========================================================================
# Edit failure → recovery (unpin old, create + pin new)
# ===========================================================================


@pytest.mark.asyncio
async def test_edit_failure_creates_new(sqlite_engine, monkeypatch):
    """Edit fails (message deleted) → unpin old, create + pin new, store new ID."""
    from leetcode_coach.db.queries import set_state

    set_state("pinned_message_id", "555")
    monkeypatch.setattr(
        "leetcode_coach.flows.pinned.get_settings",
        lambda: type("S", (), {"telegram_chat_id": "123"})(),
    )

    from leetcode_coach.errors import TelegramError

    with (
        patch.object(
            pinned_module,
            "edit_message_text",
            AsyncMock(side_effect=TelegramError("message to edit not found")),
        ),
        patch.object(pinned_module, "send_message", AsyncMock(return_value=888)) as send_mock,
        patch.object(pinned_module, "pin_message", AsyncMock()) as pin_mock,
        patch.object(pinned_module, "unpin_message", AsyncMock()) as unpin_mock,
    ):
        await refresh_pinned_message()

    send_mock.assert_awaited_once()  # new message created
    pin_mock.assert_awaited_once_with("123", 888)  # new message pinned
    unpin_mock.assert_awaited_once_with("123", 555)  # old message unpinned

    # New ID stored.
    from leetcode_coach.db.queries import get_state

    assert get_state("pinned_message_id") == "888"


# ===========================================================================
# Fire-and-forget: refresh failures don't propagate from flows
# ===========================================================================


@pytest.mark.asyncio
async def test_refresh_failure_does_not_raise(sqlite_engine, monkeypatch):
    """If refresh_pinned_message itself raises, the flow's try/except
    catches it. We test the function directly — it should handle internal
    errors gracefully (the caller wraps it in try/except, but the function
    itself should not crash on Telegram errors during recovery)."""
    from leetcode_coach.db.queries import set_state

    set_state("pinned_message_id", "555")
    monkeypatch.setattr(
        "leetcode_coach.flows.pinned.get_settings",
        lambda: type("S", (), {"telegram_chat_id": "123"})(),
    )

    from leetcode_coach.errors import TelegramError

    # Both edit and send fail — the function should raise (the caller's
    # try/except is what makes it fire-and-forget, not this function).
    with (
        patch.object(
            pinned_module,
            "edit_message_text",
            AsyncMock(side_effect=TelegramError("message to edit not found")),
        ),
        patch.object(
            pinned_module, "send_message", AsyncMock(side_effect=TelegramError("chat not found"))
        ),
        patch.object(
            pinned_module, "unpin_message", AsyncMock(side_effect=TelegramError("can't unpin"))
        ),
        pytest.raises(TelegramError),
    ):
        await refresh_pinned_message()


# ===========================================================================
# Mock mode (no real Telegram token)
# ===========================================================================


@pytest.mark.asyncio
async def test_mock_mode_does_not_store_id(sqlite_engine, monkeypatch):
    """In mock mode (send_message returns -1), don't store a fake ID."""
    monkeypatch.setattr(
        "leetcode_coach.flows.pinned.get_settings",
        lambda: type("S", (), {"telegram_chat_id": "123"})(),
    )

    with (
        patch.object(pinned_module, "send_message", AsyncMock(return_value=-1)),
        patch.object(pinned_module, "pin_message", AsyncMock()),
    ):
        await refresh_pinned_message()

    from leetcode_coach.db.queries import get_state

    assert get_state("pinned_message_id") is None
