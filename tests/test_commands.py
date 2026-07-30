"""Tests for the slash command router (issue #035, FR-6).

Covers the acceptance criteria from plan/issues/035-slash-command-router.md:
- /propose triggers flow_a.propose_5(dry_run=False).
- /pick 1 2 triggers flow_b._pick_parse_path with text "1 2".
- Unknown /foo → "unknown command" reply, zero side effects.
- A non-/ message still goes through FR-2.2 reply correlation
  (regression: existing Flow B tests still pass — verified by running
  test_flow_b.py unchanged alongside this file).
- /coach, /status, /why are stubs in #035; their full behavior is tested
  in #037 / #038. Here we only assert the stub replies.

The router is the only place that knows about commands. Flow internals
stay command-agnostic — we mock them and assert call shape, not behavior.
"""

from __future__ import annotations

import datetime
from unittest.mock import AsyncMock, patch

import pytest
from sqlmodel import Session, SQLModel, create_engine, select
from telegram import Update

from leetcode_coach.flows import commands as commands_module
from leetcode_coach.flows import flow_b
from leetcode_coach.flows.commands import route_command

# --- fixtures ---------------------------------------------------------------


def _make_update(
    *,
    text: str = "hello",
    chat_id: int = 123456,
    message_id: int = 100,
    reply_to_message_id: int | None = None,
) -> Update:
    """Build a telegram.Update the way python-telegram-bot's de_json would.

    Same shape as test_flow_b._make_update — kept local to avoid a cross-
    test-file import (test_flow_b is a sibling, not a library).
    """
    msg: dict = {
        "message_id": message_id,
        "date": 1700000000,
        "chat": {"id": chat_id, "type": "private"},
        "from": {"id": chat_id, "is_bot": False, "first_name": "test"},
        "text": text,
    }
    if reply_to_message_id is not None:
        msg["reply_to_message"] = {
            "message_id": reply_to_message_id,
            "date": 1700000000,
            "chat": {"id": chat_id, "type": "private"},
            "from": {"id": chat_id, "is_bot": False, "first_name": "test"},
            "text": "earlier message",
        }
    data = {"update_id": 1, "message": msg}
    return Update.de_json(data, bot=None)


@pytest.fixture
def sqlite_session_factory(monkeypatch: pytest.MonkeyPatch):
    """In-memory SQLite patched into flow_b (for /pick which reads DB).

    /propose and /pick call flow internals that use get_session; we patch
    flow_b's get_session so /pick's _pick_parse_path can run against the
    in-memory engine. /propose is mocked at the propose_5 boundary so it
    never reaches the DB.
    """
    engine = create_engine("sqlite:///:memory:", echo=False)
    SQLModel.metadata.create_all(engine)

    def _get_session():
        with Session(engine) as session:
            yield session

    monkeypatch.setattr(flow_b, "get_session", _get_session)
    return engine


# ===========================================================================
# /propose
# ===========================================================================


@pytest.mark.asyncio
async def test_propose_command_calls_propose_5_dry_run_false():
    """/propose → flow_a.propose_5 called once with dry_run=False."""
    update = _make_update(text="/propose")

    with patch.object(commands_module, "flow_a") as flow_a_mock:
        flow_a_mock.propose_5 = AsyncMock()
        await route_command(update)

    flow_a_mock.propose_5.assert_awaited_once_with(dry_run=False)


@pytest.mark.asyncio
async def test_propose_command_case_insensitive():
    """/Propose (mixed case) → still dispatches to propose handler."""
    update = _make_update(text="/Propose")

    with patch.object(commands_module, "flow_a") as flow_a_mock:
        flow_a_mock.propose_5 = AsyncMock()
        await route_command(update)

    flow_a_mock.propose_5.assert_awaited_once_with(dry_run=False)


# ===========================================================================
# /pick
# ===========================================================================


@pytest.mark.asyncio
async def test_pick_command_passes_args_to_pick_parse_path(sqlite_session_factory):
    """/pick 1 2 → _pick_parse_path called with chat_id and text "1 2"."""
    update = _make_update(text="/pick 1 2")

    with patch.object(commands_module, "flow_b") as flow_b_mock:
        flow_b_mock._pick_parse_path = AsyncMock()
        await route_command(update)

    flow_b_mock._pick_parse_path.assert_awaited_once()
    call_args = flow_b_mock._pick_parse_path.await_args
    assert call_args.args[0] == 123456  # chat_id
    assert call_args.args[1] == "1 2"  # args string
    assert call_args.kwargs.get("dry_run") is False


@pytest.mark.asyncio
async def test_pick_command_no_args_passes_empty_string(sqlite_session_factory):
    """/pick with no args → _pick_parse_path called with empty string
    (the internal parser handles "no valid picks")."""
    update = _make_update(text="/pick")

    with patch.object(commands_module, "flow_b") as flow_b_mock:
        flow_b_mock._pick_parse_path = AsyncMock()
        await route_command(update)

    flow_b_mock._pick_parse_path.assert_awaited_once()
    call_args = flow_b_mock._pick_parse_path.await_args
    assert call_args.args[1] == ""


# ===========================================================================
# Unknown command (FR-6.6)
# ===========================================================================


@pytest.mark.asyncio
async def test_unknown_command_replies_and_no_side_effects():
    """/foo → "unknown command" reply, no LLM call, no DB write, no flow call."""
    update = _make_update(text="/foo bar baz")

    sent = []
    with (
        patch.object(
            commands_module, "send_message", AsyncMock(side_effect=lambda c, t: sent.append(t))
        ),
        patch.object(commands_module, "flow_a") as flow_a_mock,
        patch.object(commands_module, "flow_b") as flow_b_mock,
    ):
        flow_a_mock.propose_5 = AsyncMock()
        flow_b_mock._pick_parse_path = AsyncMock()
        await route_command(update)

    assert len(sent) == 1
    assert "Unknown command" in sent[0]
    assert "/foo" in sent[0]
    flow_a_mock.propose_5.assert_not_called()
    flow_b_mock._pick_parse_path.assert_not_called()


# ===========================================================================
# Non-/ message falls through (FR-2.2 regression)
# ===========================================================================


@pytest.mark.asyncio
async def test_non_slash_message_returns_false():
    """A message not starting with / → route_command returns False, so
    flow_b.handle_update falls through to FR-2.2 routing."""
    update = _make_update(text="2 5")  # looks like a pick, but no slash

    with (
        patch.object(commands_module, "flow_a") as flow_a_mock,
        patch.object(commands_module, "flow_b") as flow_b_mock,
    ):
        flow_a_mock.propose_5 = AsyncMock()
        flow_b_mock._pick_parse_path = AsyncMock()
        result = await route_command(update)

    assert result is False
    flow_a_mock.propose_5.assert_not_called()
    flow_b_mock._pick_parse_path.assert_not_called()


@pytest.mark.asyncio
async def test_non_text_update_returns_false():
    """An update with no message text (e.g. callback_query) → False."""
    # Build an update with no text field
    data = {
        "update_id": 1,
        "message": {
            "message_id": 100,
            "date": 1700000000,
            "chat": {"id": 123456, "type": "private"},
            "from": {"id": 123456, "is_bot": False, "first_name": "test"},
        },
    }
    update = Update.de_json(data, bot=None)
    result = await route_command(update)
    assert result is False


# ===========================================================================
# /status — deterministic DB dump, no LLM (issue #038, FR-7.1)
# ===========================================================================


def _insert_lesson(
    engine, *, title: str, category: str = "arrays", count: int = 3, active: bool = True
):
    from leetcode_coach.db import models as db_models

    with Session(engine) as session:
        session.add(
            db_models.TutorLesson(
                title=title,
                category=category,
                times_reinforced=count,
                active=active,
            )
        )
        session.commit()


def _insert_log_entry(engine, *, slug: str, date, status: str, lesson_title: str | None = None):
    from leetcode_coach.db import models as db_models

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
                lesson_title=lesson_title,
            )
        )
        session.commit()


@pytest.mark.asyncio
async def test_status_no_llm_call(sqlite_session_factory):
    """/status makes zero LLM calls (FR-7.1)."""
    update = _make_update(text="/status")

    with (
        patch.object(commands_module, "send_message", AsyncMock()),
        patch.object(commands_module, "LLMClient") as llm_mock,
        patch.object(commands_module, "get_session", flow_b.get_session),
    ):
        llm_mock.return_value.complete = AsyncMock()
        await route_command(update)

    llm_mock.return_value.complete.assert_not_called()


@pytest.mark.asyncio
async def test_status_writes_zero_rows(sqlite_session_factory):
    """/status writes zero rows (FR-7.3). We snapshot row counts before and after."""
    from leetcode_coach.db import models as db_models

    update = _make_update(text="/status")

    with Session(sqlite_session_factory) as session:
        before = {
            "lessons": len(session.exec(select(db_models.TutorLesson)).all()),
            "log": len(session.exec(select(db_models.LeetCodeLog)).all()),
            "reviews": len(session.exec(select(db_models.PendingReview)).all()),
            "problems": len(session.exec(select(db_models.LeetCodeProblem)).all()),
        }

    with (
        patch.object(commands_module, "send_message", AsyncMock()),
        patch.object(commands_module, "get_session", flow_b.get_session),
    ):
        await route_command(update)

    with Session(sqlite_session_factory) as session:
        after = {
            "lessons": len(session.exec(select(db_models.TutorLesson)).all()),
            "log": len(session.exec(select(db_models.LeetCodeLog)).all()),
            "reviews": len(session.exec(select(db_models.PendingReview)).all()),
            "problems": len(session.exec(select(db_models.LeetCodeProblem)).all()),
        }

    assert before == after


@pytest.mark.asyncio
async def test_status_shows_lessons_log_and_streak(sqlite_session_factory):
    """/status message contains active lessons, log rows, and streak count."""
    today = datetime.date.today()
    yesterday = today - datetime.timedelta(days=1)

    _insert_lesson(sqlite_session_factory, title="Sliding Window", count=5)
    _insert_lesson(sqlite_session_factory, title="Two Pointers", count=2)
    _insert_lesson(sqlite_session_factory, title="Old Lesson", count=10, active=False)
    _insert_log_entry(
        sqlite_session_factory,
        slug="two-sum",
        date=today,
        status="solved",
        lesson_title="Two Pointers",
    )
    _insert_log_entry(sqlite_session_factory, slug="three-sum", date=yesterday, status="reviewed")

    update = _make_update(text="/status")
    sent = []

    async def _fake_send(chat_id, text, **kwargs):
        # Slash-command replies are plain text (no parse_mode). Guard against
        # accidentally leaking HTML/MarkdownV2 into command responses.
        assert (
            kwargs.get("parse_mode") is None
        ), f"command reply must be plain text, got parse_mode={kwargs.get('parse_mode')!r}"
        sent.append(text)

    with (
        patch.object(commands_module, "send_message", _fake_send),
        patch.object(commands_module, "get_session", flow_b.get_session),
    ):
        await route_command(update)

    assert len(sent) == 1
    msg = sent[0]
    # Streak = 2 (today + yesterday both have coached attempts)
    assert "Streak: 2 days" in msg
    # Active lessons shown (ordered by count desc)
    assert "Sliding Window" in msg
    assert "Two Pointers" in msg
    assert "5x" in msg
    # Inactive lesson NOT shown
    assert "Old Lesson" not in msg
    # Log entries shown
    assert "two-sum" in msg
    assert "three-sum" in msg


@pytest.mark.asyncio
async def test_status_streak_zero_when_no_coached(sqlite_session_factory):
    """/status with no coached attempts → streak = 0."""
    _insert_log_entry(
        sqlite_session_factory, slug="x", date=datetime.date.today(), status="skipped"
    )

    update = _make_update(text="/status")
    sent = []

    async def _fake_send(chat_id, text, **kwargs):
        # Slash-command replies are plain text (no parse_mode). Guard against
        # accidentally leaking HTML/MarkdownV2 into command responses.
        assert (
            kwargs.get("parse_mode") is None
        ), f"command reply must be plain text, got parse_mode={kwargs.get('parse_mode')!r}"
        sent.append(text)

    with (
        patch.object(commands_module, "send_message", _fake_send),
        patch.object(commands_module, "get_session", flow_b.get_session),
    ):
        await route_command(update)

    assert "Streak: 0 days" in sent[0]


# ===========================================================================
# /why — single bounded LLM call (issue #038, FR-7.2)
# ===========================================================================


@pytest.mark.asyncio
async def test_why_no_args_replies_usage(sqlite_session_factory):
    """/why with no args → "usage" reply, zero LLM calls."""
    update = _make_update(text="/why")
    sent = []

    async def _fake_send(chat_id, text, **kwargs):
        # Slash-command replies are plain text (no parse_mode). Guard against
        # accidentally leaking HTML/MarkdownV2 into command responses.
        assert (
            kwargs.get("parse_mode") is None
        ), f"command reply must be plain text, got parse_mode={kwargs.get('parse_mode')!r}"
        sent.append(text)

    with (
        patch.object(commands_module, "send_message", _fake_send),
        patch.object(commands_module, "LLMClient") as llm_mock,
        patch.object(commands_module, "get_session", flow_b.get_session),
    ):
        llm_mock.return_value.complete = AsyncMock()
        await route_command(update)

    assert len(sent) == 1
    assert "Usage" in sent[0]
    llm_mock.return_value.complete.assert_not_called()


@pytest.mark.asyncio
async def test_why_bad_slug_replies_no_such_problem(sqlite_session_factory):
    """/why nonexistent-slug → "no such problem", zero LLM calls."""
    update = _make_update(text="/why nonexistent-slug")
    sent = []

    async def _fake_send(chat_id, text, **kwargs):
        # Slash-command replies are plain text (no parse_mode). Guard against
        # accidentally leaking HTML/MarkdownV2 into command responses.
        assert (
            kwargs.get("parse_mode") is None
        ), f"command reply must be plain text, got parse_mode={kwargs.get('parse_mode')!r}"
        sent.append(text)

    with (
        patch.object(commands_module, "send_message", _fake_send),
        patch.object(commands_module, "LLMClient") as llm_mock,
        patch.object(commands_module, "get_session", flow_b.get_session),
    ):
        llm_mock.return_value.complete = AsyncMock()
        await route_command(update)

    assert len(sent) == 1
    assert "No such problem" in sent[0]
    llm_mock.return_value.complete.assert_not_called()


@pytest.mark.asyncio
async def test_why_valid_slug_makes_one_llm_call(sqlite_session_factory):
    """/why two-sum → exactly one LLM call with max_tokens=300 (FR-7.2)."""
    _insert_pending_review(sqlite_session_factory, message_id=50, problem_slug="two-sum")
    update = _make_update(text="/why two-sum")
    sent = []

    async def _fake_send(chat_id, text, **kwargs):
        # Slash-command replies are plain text (no parse_mode). Guard against
        # accidentally leaking HTML/MarkdownV2 into command responses.
        assert (
            kwargs.get("parse_mode") is None
        ), f"command reply must be plain text, got parse_mode={kwargs.get('parse_mode')!r}"
        sent.append(text)

    mock_response = AsyncMock()
    mock_response.text = "Two Sum was proposed because it reinforces Two Pointers."
    mock_response.tokens_out = 42

    with (
        patch.object(commands_module, "send_message", _fake_send),
        patch.object(commands_module, "LLMClient") as llm_mock,
        patch.object(commands_module, "get_session", flow_b.get_session),
    ):
        llm_mock.return_value.complete = AsyncMock(return_value=mock_response)
        await route_command(update)

    llm_mock.return_value.complete.assert_awaited_once()
    call_kwargs = llm_mock.return_value.complete.await_args
    assert call_kwargs.kwargs.get("max_tokens") == 300
    assert len(sent) == 1
    assert "Two Sum was proposed" in sent[0]


@pytest.mark.asyncio
async def test_why_writes_zero_rows(sqlite_session_factory):
    """/why writes zero rows (FR-7.3)."""
    from leetcode_coach.db import models as db_models

    _insert_pending_review(sqlite_session_factory, message_id=50, problem_slug="two-sum")
    update = _make_update(text="/why two-sum")

    with Session(sqlite_session_factory) as session:
        before = {
            "lessons": len(session.exec(select(db_models.TutorLesson)).all()),
            "log": len(session.exec(select(db_models.LeetCodeLog)).all()),
            "reviews": len(session.exec(select(db_models.PendingReview)).all()),
            "problems": len(session.exec(select(db_models.LeetCodeProblem)).all()),
        }

    mock_response = AsyncMock()
    mock_response.text = "Because."
    mock_response.tokens_out = 5

    with (
        patch.object(commands_module, "send_message", AsyncMock()),
        patch.object(commands_module, "LLMClient") as llm_mock,
        patch.object(commands_module, "get_session", flow_b.get_session),
    ):
        llm_mock.return_value.complete = AsyncMock(return_value=mock_response)
        await route_command(update)

    with Session(sqlite_session_factory) as session:
        after = {
            "lessons": len(session.exec(select(db_models.TutorLesson)).all()),
            "log": len(session.exec(select(db_models.LeetCodeLog)).all()),
            "reviews": len(session.exec(select(db_models.PendingReview)).all()),
            "problems": len(session.exec(select(db_models.LeetCodeProblem)).all()),
        }

    assert before == after


# ===========================================================================
# Integration: route_command wired into flow_b.handle_update
# ===========================================================================


@pytest.mark.asyncio
async def test_handle_update_dispatches_slash_command_before_fr22(sqlite_session_factory):
    """A /propose message reaches route_command and does NOT fall through
    to FR-2.2 fuzzy title matching (which would send a clarification).

    This is the wiring regression test: if route_command is not called,
    "/propose" would be fuzzy-matched against open pending_review rows and
    either coached (if 1 match) or clarified (if 0 or >1). We assert
    propose_5 is called instead.
    """
    from leetcode_coach.flows.flow_b import handle_update

    update = _make_update(text="/propose")

    with patch.object(commands_module, "flow_a") as flow_a_mock:
        flow_a_mock.propose_5 = AsyncMock()
        # Also mock send_message so the unknown-command path (if routing
        # broke) wouldn't try to hit Telegram.
        with patch.object(commands_module, "send_message", AsyncMock()):
            await handle_update(update)

        flow_a_mock.propose_5.assert_awaited_once_with(dry_run=False)


@pytest.mark.asyncio
async def test_handle_update_non_slash_falls_through_to_fr22(sqlite_session_factory):
    """A non-/ message still reaches FR-2.2 routing (regression).

    We seed one open pending_review whose title fuzzy-matches the message
    text, and assert _coach_pass_path is called — proving FR-2.2 still
    runs for non-command messages after the router is wired in.
    """
    from leetcode_coach.db import models as db_models
    from leetcode_coach.flows.flow_b import handle_update

    today = datetime.date.today()
    with Session(sqlite_session_factory) as session:
        session.add(
            db_models.LeetCodeProblem(
                slug="merge-intervals",
                title="Merge Intervals",
                url="https://leetcode.com/problems/merge-intervals/",
                difficulty="medium",
                tags="array",
                solved=False,
            )
        )
        session.add(
            db_models.PendingReview(
                message_id=50,
                google_task_id="t1",
                problem_slug="merge-intervals",
                problem_title="Merge Intervals",
                proposed_at=today,
                status="open",
            )
        )
        session.commit()

    update = _make_update(text="merge intervals code")

    coach_calls = []

    async def _fake_coach(chat_id, inbound_id, review, user_text):
        coach_calls.append(review.problem_slug)

    with patch.object(flow_b, "_coach_pass_path", _fake_coach):
        await handle_update(update)

    assert len(coach_calls) == 1
    assert coach_calls[0] == "merge-intervals"


# ===========================================================================
# /coach — target resolution waterfall (issue #037, FR-6.4)
# ===========================================================================


def _insert_pending_review(
    engine,
    *,
    message_id: int,
    problem_slug: str = "two-sum",
    problem_title: str = "Two Sum",
    status: str = "open",
):
    """Seed a pending_review row + its leetcode_problems parent (FK)."""
    from leetcode_coach.db import models as db_models

    today = datetime.date.today()
    with Session(engine) as session:
        if session.get(db_models.LeetCodeProblem, problem_slug) is None:
            session.add(
                db_models.LeetCodeProblem(
                    slug=problem_slug,
                    title=problem_title,
                    url=f"https://leetcode.com/problems/{problem_slug}/",
                    difficulty="easy",
                    tags="array",
                    solved=False,
                )
            )
        session.add(
            db_models.PendingReview(
                message_id=message_id,
                google_task_id=f"task-{problem_slug}",
                problem_slug=problem_slug,
                problem_title=problem_title,
                proposed_at=today,
                status=status,
            )
        )
        session.commit()


@pytest.mark.asyncio
async def test_coach_single_open_review_no_slug_coaches_it(sqlite_session_factory):
    """1 open review, /coach <code> (no slug) → coaches that review."""
    _insert_pending_review(sqlite_session_factory, message_id=50, problem_slug="two-sum")
    update = _make_update(text="/coach def foo(): pass")

    coach_calls = []

    async def _fake_coach(chat_id, inbound_id, review, user_text, *, dry_run=False):
        coach_calls.append((review.problem_slug, user_text, dry_run))

    with (
        patch.object(commands_module, "flow_b") as flow_b_mock,
        patch.object(commands_module, "get_session", flow_b.get_session),
    ):
        flow_b_mock._coach_pass_path = _fake_coach
        await route_command(update)

    assert len(coach_calls) == 1
    assert coach_calls[0][0] == "two-sum"
    assert coach_calls[0][1] == "def foo(): pass"
    assert coach_calls[0][2] is False


@pytest.mark.asyncio
async def test_coach_slug_targets_correct_review(sqlite_session_factory):
    """2 open reviews, /coach two-sum <code> → coaches the two-sum row."""
    _insert_pending_review(sqlite_session_factory, message_id=50, problem_slug="two-sum")
    _insert_pending_review(
        sqlite_session_factory,
        message_id=51,
        problem_slug="three-sum",
        problem_title="Three Sum",
    )
    update = _make_update(text="/coach two-sum def foo(): pass")

    coach_calls = []

    async def _fake_coach(chat_id, inbound_id, review, user_text, *, dry_run=False):
        coach_calls.append((review.problem_slug, user_text))

    with (
        patch.object(commands_module, "flow_b") as flow_b_mock,
        patch.object(commands_module, "get_session", flow_b.get_session),
    ):
        flow_b_mock._coach_pass_path = _fake_coach
        await route_command(update)

    assert len(coach_calls) == 1
    assert coach_calls[0][0] == "two-sum"
    assert coach_calls[0][1] == "def foo(): pass"


@pytest.mark.asyncio
async def test_coach_two_open_reviews_no_slug_replies_with_list(sqlite_session_factory):
    """2 open reviews, /coach <code> (no slug) → reply lists both, no coach call."""
    _insert_pending_review(sqlite_session_factory, message_id=50, problem_slug="two-sum")
    _insert_pending_review(
        sqlite_session_factory,
        message_id=51,
        problem_slug="three-sum",
        problem_title="Three Sum",
    )
    update = _make_update(text="/coach def foo(): pass")

    sent = []

    async def _fake_send(chat_id, text, **kwargs):
        # Slash-command replies are plain text (no parse_mode). Guard against
        # accidentally leaking HTML/MarkdownV2 into command responses.
        assert (
            kwargs.get("parse_mode") is None
        ), f"command reply must be plain text, got parse_mode={kwargs.get('parse_mode')!r}"
        sent.append(text)

    with (
        patch.object(commands_module, "send_message", _fake_send),
        patch.object(commands_module, "flow_b") as flow_b_mock,
        patch.object(commands_module, "get_session", flow_b.get_session),
    ):
        flow_b_mock._coach_pass_path = AsyncMock()
        await route_command(update)

    assert len(sent) == 1
    assert "two-sum" in sent[0]
    assert "three-sum" in sent[0]
    flow_b_mock._coach_pass_path.assert_not_called()


@pytest.mark.asyncio
async def test_coach_zero_open_reviews_replies_no_open_problems(sqlite_session_factory):
    """0 open reviews, /coach anything → "No open problems today", no coach call."""
    update = _make_update(text="/coach anything")

    sent = []

    async def _fake_send(chat_id, text, **kwargs):
        # Slash-command replies are plain text (no parse_mode). Guard against
        # accidentally leaking HTML/MarkdownV2 into command responses.
        assert (
            kwargs.get("parse_mode") is None
        ), f"command reply must be plain text, got parse_mode={kwargs.get('parse_mode')!r}"
        sent.append(text)

    with (
        patch.object(commands_module, "send_message", _fake_send),
        patch.object(commands_module, "flow_b") as flow_b_mock,
        patch.object(commands_module, "get_session", flow_b.get_session),
    ):
        flow_b_mock._coach_pass_path = AsyncMock()
        await route_command(update)

    assert len(sent) == 1
    assert "No open problems" in sent[0]
    flow_b_mock._coach_pass_path.assert_not_called()


@pytest.mark.asyncio
async def test_coach_reply_to_problem_message_uses_message_id(sqlite_session_factory):
    """Reply-to a problem message with /coach <code> → coaches the review
    whose message_id matches the replied-to message."""
    _insert_pending_review(sqlite_session_factory, message_id=50, problem_slug="two-sum")
    update = _make_update(text="/coach def foo(): pass", reply_to_message_id=50)

    coach_calls = []

    async def _fake_coach(chat_id, inbound_id, review, user_text, *, dry_run=False):
        coach_calls.append((review.problem_slug, user_text))

    with (
        patch.object(commands_module, "flow_b") as flow_b_mock,
        patch.object(commands_module, "get_session", flow_b.get_session),
    ):
        flow_b_mock._coach_pass_path = _fake_coach
        await route_command(update)

    assert len(coach_calls) == 1
    assert coach_calls[0][0] == "two-sum"
    assert coach_calls[0][1] == "def foo(): pass"


@pytest.mark.asyncio
async def test_coach_reply_to_non_problem_message_replies_error(sqlite_session_factory):
    """Reply-to a message_id not in pending_review → error reply, no coach call."""
    _insert_pending_review(sqlite_session_factory, message_id=50, problem_slug="two-sum")
    # Reply to message 999 which is not a problem thread.
    update = _make_update(text="/coach code", reply_to_message_id=999)

    sent = []

    async def _fake_send(chat_id, text, **kwargs):
        # Slash-command replies are plain text (no parse_mode). Guard against
        # accidentally leaking HTML/MarkdownV2 into command responses.
        assert (
            kwargs.get("parse_mode") is None
        ), f"command reply must be plain text, got parse_mode={kwargs.get('parse_mode')!r}"
        sent.append(text)

    with (
        patch.object(commands_module, "send_message", _fake_send),
        patch.object(commands_module, "flow_b") as flow_b_mock,
        patch.object(commands_module, "get_session", flow_b.get_session),
    ):
        flow_b_mock._coach_pass_path = AsyncMock()
        await route_command(update)

    assert len(sent) == 1
    assert "isn't an open problem thread" in sent[0]
    flow_b_mock._coach_pass_path.assert_not_called()


# ===========================================================================
# /help — static command list, no LLM, no DB write
# ===========================================================================


@pytest.mark.asyncio
async def test_help_lists_all_commands():
    """/help → one message listing every known command."""
    update = _make_update(text="/help")
    sent = []

    async def _fake_send(chat_id, text, **kwargs):
        assert kwargs.get("parse_mode") is None
        sent.append(text)

    with patch.object(commands_module, "send_message", _fake_send):
        await route_command(update)

    assert len(sent) == 1
    msg = sent[0]
    for cmd in ("/propose", "/pick", "/coach", "/status", "/why", "/help"):
        assert cmd in msg, f"{cmd} missing from /help output"


@pytest.mark.asyncio
async def test_help_no_llm_no_db():
    """/help makes zero LLM calls and zero DB accesses."""
    update = _make_update(text="/help")

    with (
        patch.object(commands_module, "send_message", AsyncMock()),
        patch.object(commands_module, "LLMClient") as llm_mock,
        patch.object(commands_module, "get_session") as session_mock,
    ):
        llm_mock.return_value.complete = AsyncMock()
        await route_command(update)

    llm_mock.return_value.complete.assert_not_called()
    session_mock.assert_not_called()


@pytest.mark.asyncio
async def test_help_case_insensitive():
    """/Help and /HELP → dispatches to help handler."""

    for text in ("/Help", "/HELP", "/HeLp"):
        await _assert_help_replies(text)


async def _assert_help_replies(text: str) -> None:
    """Helper: send `text` as a command, assert the help reply is returned."""
    update = _make_update(text=text)
    captured: list[str] = []

    async def _fake_send(chat_id, t, **kwargs):
        captured.append(t)

    with patch.object(commands_module, "send_message", _fake_send):
        await route_command(update)

    assert len(captured) == 1
    assert "/propose" in captured[0]


@pytest.mark.asyncio
async def test_help_ignores_args():
    """/help foo bar → still shows help (args ignored)."""
    update = _make_update(text="/help foo bar")
    sent = []

    async def _fake_send(chat_id, text, **kwargs):
        assert kwargs.get("parse_mode") is None
        sent.append(text)

    with patch.object(commands_module, "send_message", _fake_send):
        await route_command(update)

    assert len(sent) == 1
    assert "/propose" in sent[0]
