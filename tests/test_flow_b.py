"""Flow B tests (#027) — routing, pick-parse, coach pass, golden double-gate.

Covers every FR-2 sub-requirement:
- FR-2.2 routing: reply-to per-problem id → coach; reply-to 5-list id →
  pick-parse; no-reply single fuzzy → coach; zero/multiple → clarify + no
  state change.
- FR-2.3/2.4 pick-parse: "2 5" → 2 msgs/2 rows; cap at 2; empty →
  "no valid picks" + zero rows.
- FR-2.5/2.7 coach path: all four post-coach updates happen in order.
- FR-2.6 golden double-gate: coach says graduate but DB count = 4 → bump,
  not graduate; count ≥ 5 → retire. The DB count is the source of truth,
  never the coach (AGENTS.md gotcha #4).
- Status notes: "skipped" logs status only.

The golden double-gate test MUST FAIL if the DB-count gate is removed.
Tests that can't fail on the bug they guard are theatre (#034).
"""

from __future__ import annotations

import datetime
import json
from unittest.mock import AsyncMock, patch

import pytest
from sqlmodel import Session, SQLModel, create_engine, select
from telegram import Update

from leetcode_coach.db import models as db_models
from leetcode_coach.flows import flow_b
from leetcode_coach.flows.flow_b import (
    CoachParseError,
    CoachResult,
    _fuzzy_title_match,
    _lesson_decision,
    _parse_coach_result,
    handle_update,
)
from leetcode_coach.integrations.llm import LLMClient, LLMResponse

# --- fixtures ---------------------------------------------------------------
#
# In-memory SQLite (same pattern as test_flow_a.py). The SQL we use is
# standard and works on both SQLite and Postgres; testcontainers would be
# heavier and slower for the same coverage. The engine is patched into
# flow_b's module so `next(get_session())` uses our test engine.


@pytest.fixture
def sqlite_session_factory(monkeypatch: pytest.MonkeyPatch):
    """Replace flow_b's get_session with one backed by in-memory SQLite."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    SQLModel.metadata.create_all(engine)

    def _get_session():
        with Session(engine) as session:
            yield session

    monkeypatch.setattr(flow_b, "get_session", _get_session)
    # #039: the pinned refresh hook (called from _pick_parse_path and
    # _post_coach_updates when not dry_run) uses pinned_module.get_session
    # + db.queries.get_session — patch them too.
    from leetcode_coach.db import queries as db_queries
    from leetcode_coach.flows import pinned as pinned_module

    monkeypatch.setattr(pinned_module, "get_session", _get_session)
    monkeypatch.setattr(db_queries, "get_session", _get_session)
    return engine


def _make_update(
    *,
    text: str = "hello",
    chat_id: int = 123456,
    message_id: int = 100,
    reply_to_message_id: int | None = None,
) -> Update:
    """Build a telegram.Update the way python-telegram-bot's de_json would.

    We construct the raw dict and call Update.de_json so the typed fields
    (message.chat.id, reply_to_message.message_id, etc.) populate the
    same way the webhook route does it.
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


def _insert_daily_candidates(engine, count: int = 5) -> list[db_models.DailyCandidate]:
    """Insert `count` daily_candidates rows for today (pick_index 1..count)."""
    today = datetime.date.today()
    cands = []
    with Session(engine) as session:
        for i in range(1, count + 1):
            slug = f"problem-{i}"
            c = db_models.DailyCandidate(
                proposed_at=today,
                pick_index=i,
                slug=slug,
                title=f"Problem {i}",
                url=f"https://leetcode.com/problems/{slug}/",
                tags="array",
                difficulty="medium" if i % 2 else "hard",
                reasoning="r",
                coaching_hint="h",
            )
            session.add(c)
            cands.append(c)
        # Also need the leetcode_problems row (FK).
        for c in cands:
            session.add(
                db_models.LeetCodeProblem(
                    slug=c.slug,
                    title=c.title,
                    url=c.url,
                    difficulty=c.difficulty,
                    tags=c.tags,
                    solved=False,
                )
            )
        session.commit()
    return cands


def _attach_created_batch(engine) -> int:
    """Attach today's candidates to a batch in its initial lifecycle state."""
    with Session(engine) as session:
        batch = db_models.ProposalBatch(status=db_models.ProposalBatchStatus.CREATED)
        session.add(batch)
        session.flush()
        assert batch.id is not None
        for candidate in session.exec(select(db_models.DailyCandidate)).all():
            candidate.batch_id = batch.id
            session.add(candidate)
        session.commit()
        return batch.id


def _insert_pending_review(
    engine,
    *,
    message_id: int,
    problem_slug: str = "problem-1",
    problem_title: str = "Problem 1",
    status: str = "open",
) -> db_models.PendingReview:
    today = datetime.date.today()
    with Session(engine) as session:
        # ensure problem exists
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
            problem_slug=problem_slug,
            problem_title=problem_title,
            proposed_at=today,
            status=status,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return row


def _coach_json(**overrides) -> dict:
    """A valid coach LLM JSON response. Override fields per-test.

    Per docs/telegram-formatting.md §3.2.3, `tutor_feedback` is PLAIN TEXT
    (no HTML). The code html.escape()s it and wraps it in <blockquote>
    before sending. The lesson footer is built in code from the lesson
    decision fields.
    """
    base = {
        "tutor_feedback": "Correctness: works.\nNext step: try harder.",
        "lesson_title": "",
        "lesson_category": "",
        "lesson_is_recurring": False,
        "lesson_should_graduate": False,
        "solved": True,
        "status": "solved",
        "next_step": "try a harder variant",
    }
    base.update(overrides)
    return base


def _mock_llm(json_response: dict) -> AsyncMock:
    mock = AsyncMock(spec=LLMClient)
    mock.complete = AsyncMock(
        return_value=LLMResponse(
            text=json.dumps(json_response), model="mock", tokens_in=0, tokens_out=0
        )
    )
    return mock


# ===========================================================================
# FR-2.2 routing tests
# ===========================================================================


@pytest.mark.asyncio
async def test_route_reply_to_per_problem_message_goes_coach(sqlite_session_factory):
    """Reply-to a message_id present in pending_review → coach path."""
    _insert_pending_review(sqlite_session_factory, message_id=50, problem_slug="problem-1")
    update = _make_update(text="my code", reply_to_message_id=50)

    coach_calls = []

    async def _fake_coach_path(chat_id, inbound_id, review, user_text):
        coach_calls.append((chat_id, inbound_id, review.problem_slug, user_text))

    with patch.object(flow_b, "_coach_pass_path", _fake_coach_path):
        await handle_update(update)

    assert len(coach_calls) == 1
    assert coach_calls[0][2] == "problem-1"
    assert coach_calls[0][3] == "my code"


@pytest.mark.asyncio
async def test_route_reply_to_extended_yesterday_review_goes_coach(sqlite_session_factory):
    review = _insert_pending_review(sqlite_session_factory, message_id=51)
    with Session(sqlite_session_factory) as session:
        row = session.get(db_models.PendingReview, review.id)
        row.proposed_at = datetime.date.today() - datetime.timedelta(days=1)
        session.add(row)
        session.commit()
    update = _make_update(text="my retry", reply_to_message_id=51)
    coach = AsyncMock()
    with patch.object(flow_b, "_coach_pass_path", coach):
        await handle_update(update)
    coach.assert_awaited_once()


@pytest.mark.asyncio
async def test_route_reply_to_5list_message_goes_pick_parse(sqlite_session_factory):
    """Reply-to a message_id NOT in pending_review → pick-parse path
    (the reply was to the 5-list message, whose id is never stored)."""
    _insert_daily_candidates(sqlite_session_factory)
    update = _make_update(text="2 5", reply_to_message_id=999)  # 999 not in pending_review

    pick_calls = []

    async def _fake_pick(chat_id, text):
        pick_calls.append((chat_id, text))

    with patch.object(flow_b, "_pick_parse_path", _fake_pick):
        await handle_update(update)

    assert len(pick_calls) == 1
    assert pick_calls[0][1] == "2 5"


@pytest.mark.asyncio
async def test_route_no_reply_single_fuzzy_match_goes_coach(sqlite_session_factory):
    """No reply_to + exactly one fuzzy title match → coach path."""
    _insert_pending_review(sqlite_session_factory, message_id=50, problem_title="Merge Intervals")
    update = _make_update(text="merge intervals code")  # contains the title

    coach_calls = []

    async def _fake_coach(chat_id, inbound_id, review, user_text):
        coach_calls.append(review.problem_slug)

    with patch.object(flow_b, "_coach_pass_path", _fake_coach):
        await handle_update(update)

    assert len(coach_calls) == 1


@pytest.mark.asyncio
async def test_route_no_reply_multiple_matches_sends_clarification(sqlite_session_factory):
    """No reply_to + multiple fuzzy matches → clarification, no state change."""
    _insert_pending_review(sqlite_session_factory, message_id=50, problem_title="Binary Search")
    _insert_pending_review(
        sqlite_session_factory,
        message_id=51,
        problem_slug="problem-2",
        problem_title="Search Range",
    )
    update = _make_update(text="search")  # matches both titles

    sent = []

    with (
        patch.object(flow_b, "send_message", AsyncMock(side_effect=lambda c, t: sent.append(t))),
        patch.object(flow_b, "_coach_pass_path", AsyncMock()) as coach_mock,
    ):
        await handle_update(update)

    assert len(sent) == 1
    assert "Which one" in sent[0]
    coach_mock.assert_not_called()  # never guesses on ambiguity


@pytest.mark.asyncio
async def test_route_no_reply_no_matches_no_open_sends_hint(sqlite_session_factory):
    """No reply_to + no open rows → hint message, no coach call."""
    update = _make_update(text="something unrelated")
    sent = []

    with (
        patch.object(flow_b, "send_message", AsyncMock(side_effect=lambda c, t: sent.append(t))),
        patch.object(flow_b, "_coach_pass_path", AsyncMock()) as coach_mock,
    ):
        await handle_update(update)

    assert len(sent) == 1
    assert "No open problems" in sent[0] or "Which one" in sent[0]
    coach_mock.assert_not_called()


@pytest.mark.asyncio
async def test_route_non_text_update_silently_dropped(sqlite_session_factory):
    """A non-text update (no message) is silently dropped (v1 scope)."""
    data = {"update_id": 2}
    update = Update.de_json(data, bot=None)
    with (
        patch.object(flow_b, "_coach_pass_path", AsyncMock()) as coach_mock,
        patch.object(flow_b, "_pick_parse_path", AsyncMock()) as pick_mock,
    ):
        await handle_update(update)
    coach_mock.assert_not_called()
    pick_mock.assert_not_called()


# ===========================================================================
# FR-2.3/2.4 pick-parse tests
# ===========================================================================


@pytest.mark.asyncio
async def test_pick_parse_two_picks_creates_two_threads(sqlite_session_factory):
    """'2 5' → 2 per-problem messages, 2 pending_review rows."""
    _insert_daily_candidates(sqlite_session_factory, count=5)
    msg_ids = iter([100, 200])

    sent_msgs = []

    async def _fake_send(chat_id, text, **kwargs):
        # Per-problem thread messages must be sent with parse_mode="HTML"
        # (Phase 9 prep: HTML-formatted card with html.escape'd titles).
        assert (
            kwargs.get("parse_mode") == "HTML"
        ), f"per-problem send must use parse_mode=HTML, got {kwargs.get('parse_mode')!r}"
        mid = next(msg_ids)
        sent_msgs.append((mid, text))
        return mid

    with patch.object(flow_b, "send_message", AsyncMock(side_effect=_fake_send)):
        await flow_b._pick_parse_path(123456, "2 5")

    assert len(sent_msgs) == 2
    # FR-2.4 order: message → row. Verify message went out first per pick.
    assert "Problem 1/2" in sent_msgs[0][1]
    assert "Problem 2/2" in sent_msgs[1][1]
    # Phase 9: per-problem thread uses hyperlink + difficulty badge shape
    # (docs/telegram-formatting.md §3.2.2). The title is wrapped in <a href>
    # pointing at the LeetCode URL, and a colored emoji badge precedes the
    # difficulty word.
    for _, text in sent_msgs:
        assert '<a href="https://leetcode.com/problems/' in text
        assert "</a>" in text
        assert text.count("🟢") + text.count("🟡") + text.count("🔴") == 1
        assert "<blockquote>" in text  # coaching hint wrapped in blockquote
    # No MarkdownV2 escape artifacts should leak (regression for the
    # user-reported bug, docs/telegram-formatting.md §1).
    for _, text in sent_msgs:
        assert "\\." not in text
        assert "\\-" not in text

    # Verify 2 pending_review rows with the captured ids.
    with Session(sqlite_session_factory) as session:
        rows = session.exec(
            select(db_models.PendingReview).order_by(db_models.PendingReview.id)
        ).all()
    assert len(rows) == 2
    assert {r.message_id for r in rows} == {100, 200}
    assert all(r.status == "open" for r in rows)


@pytest.mark.asyncio
async def test_pick_parse_caps_at_two(sqlite_session_factory):
    """'2 3 4 5' → capped at 2 chosen (FR-2.3)."""
    _insert_daily_candidates(sqlite_session_factory, count=5)
    sent = []

    async def _fake_send(chat_id, text, **kwargs):
        mid = len(sent) + 1
        sent.append(text)
        return mid

    with patch.object(flow_b, "send_message", AsyncMock(side_effect=_fake_send)):
        await flow_b._pick_parse_path(123456, "2 3 4 5")

    assert len(sent) == 2  # capped


@pytest.mark.asyncio
async def test_pick_parse_empty_reply_sends_no_valid_picks(sqlite_session_factory):
    """Empty/garbage reply → 'no valid picks' message, zero rows written."""
    _insert_daily_candidates(sqlite_session_factory)
    sent = []

    with patch.object(flow_b, "send_message", AsyncMock(side_effect=lambda c, t: sent.append(t))):
        await flow_b._pick_parse_path(123456, "hello world")

    assert len(sent) == 1
    assert "No valid picks" in sent[0]
    with Session(sqlite_session_factory) as session:
        rows = session.exec(select(db_models.PendingReview)).all()
    assert len(rows) == 0


@pytest.mark.asyncio
async def test_pick_parse_no_candidates_sends_hint(sqlite_session_factory):
    """No daily_candidates persisted today → fail-loud hint, no rows."""
    sent = []
    with patch.object(flow_b, "send_message", AsyncMock(side_effect=lambda c, t: sent.append(t))):
        await flow_b._pick_parse_path(123456, "1 2")
    assert any("No candidate list" in s for s in sent)


# ===========================================================================
# FR-2.5/2.7 coach path + BUG-2 regression
# ===========================================================================


@pytest.mark.asyncio
async def test_coach_path_runs_four_steps_in_order(sqlite_session_factory):
    """FR-2.7: all four post-coach updates happen in order:
    1. leetcode_log row
    2. leetcode_problems.solved = true (if solved)
    3. pending_review.status = done
    4. Telegram reply
    """
    review = _insert_pending_review(sqlite_session_factory, message_id=50)
    update = _make_update(text="def two_sum(): ...", reply_to_message_id=50)

    call_order: list[str] = []

    async def _fake_complete(system, user, **kw):
        call_order.append("llm")
        return LLMResponse(
            text=json.dumps(_coach_json(status="solved", solved=True, lesson_title="")),
            model="mock",
            tokens_in=0,
            tokens_out=0,
        )

    sent_replies = []

    async def _spy_send_reply(chat_id, reply_to, text, **kwargs):
        call_order.append("reply")
        # Coach reply must be sent with parse_mode="HTML" (Phase 9: the
        # plain-text tutor_feedback is html.escape'd and wrapped in
        # <blockquote>; the lesson footer is built in code as <i>...</i>).
        assert (
            kwargs.get("parse_mode") == "HTML"
        ), f"coach reply must use parse_mode=HTML, got {kwargs.get('parse_mode')!r}"
        sent_replies.append(text)

    with (
        patch.object(flow_b, "LLMClient") as LLMCls,
        patch.object(flow_b, "send_reply", AsyncMock(side_effect=_spy_send_reply)),
        patch.object(flow_b, "send_message", AsyncMock()),
    ):
        LLMCls.return_value.complete = AsyncMock(side_effect=_fake_complete)
        await handle_update(update)

    # Verify leetcode_log row inserted (step 1).
    with Session(sqlite_session_factory) as session:
        logs = session.exec(select(db_models.LeetCodeLog)).all()
        assert len(logs) == 1
        assert logs[0].status == "solved"
        # Verify leetcode_problems.solved = true (step 2).
        prob = session.get(db_models.LeetCodeProblem, review.problem_slug)
        assert prob.solved is True
        # Verify pending_review.status = done (step 3).
        pr = session.exec(
            select(db_models.PendingReview).where(db_models.PendingReview.message_id == 50)
        ).first()
        assert pr.status == "done"

    # Step 4: reply sent with the coach feedback.
    assert len(sent_replies) == 1
    reply = sent_replies[0]
    assert "Correctness" in reply
    # Phase 9: the plain-text tutor_feedback is wrapped in <blockquote>.
    assert "<blockquote>" in reply
    assert "</blockquote>" in reply
    # No lesson fired in this case (lesson_title=""), so no <i> footer.
    assert "<i>" not in reply
    # No MarkdownV2 escape artifacts (regression for the user-reported bug).
    assert "\\." not in reply
    assert "\\-" not in reply

    # Order: llm → reply. (log/problem/pending_review are DB writes
    # interleaved but happen before reply per FR-2.7.)
    assert call_order.index("llm") < call_order.index("reply")


@pytest.mark.asyncio
async def test_closed_review_cannot_finalize_log_or_credit(sqlite_session_factory):
    """A concurrent close after the LLM call must make finalization a no-op."""
    review = _insert_pending_review(
        sqlite_session_factory, message_id=50, status=db_models.ReviewStatus.DONE
    )
    result = CoachResult(
        tutor_feedback="late result",
        lesson_title="",
        lesson_category="",
        lesson_is_recurring=False,
        lesson_should_graduate=False,
        solved=True,
        status="solved",
        next_step="",
        time_spent_min=None,
    )
    outcome = flow_b.LessonOutcome(action="none", title="", times_reinforced=0)

    reply = await flow_b._post_coach_updates(
        chat_id=123456,
        inbound_message_id=99,
        review=review,
        problem_slug=review.problem_slug,
        result=result,
        lesson_outcome=outcome,
        dry_run=True,
    )

    assert reply == ""
    with Session(sqlite_session_factory) as session:
        assert session.exec(select(db_models.LeetCodeLog)).all() == []
        assert session.exec(select(db_models.CreditLedger)).all() == []


@pytest.mark.asyncio
async def test_coach_reply_escapes_special_chars_in_tutor_feedback(sqlite_session_factory):
    """Phase 9 regression (docs/telegram-formatting.md §3.2.3): `tutor_feedback`
    is plain text from the LLM. If it contains `<`, `>`, `&` (e.g., the LLM
    reviewed code with `if a < b`), the code must html.escape() it before
    sending as HTML — otherwise the Telegram HTML parser breaks or, worse,
    the feedback's `<` gets interpreted as a tag.

    Also verifies the MarkdownV2 escape leak regression: even when the
    feedback contains `.`, `-`, `(`, `)`, no backslash-escape artifacts appear.
    """
    _insert_pending_review(sqlite_session_factory, message_id=50)
    update = _make_update(text="my code", reply_to_message_id=50)

    # Plain-text feedback with HTML special chars AND MarkdownV2 special chars.
    raw_feedback = (
        "Correctness: if a < b then b > a & c.\n"
        "Bug: off-by-one on arr[i-1].\n"
        "Next step: handle the (n-1) case."
    )

    async def _fake_complete(system, user, **kw):
        return LLMResponse(
            text=json.dumps(_coach_json(tutor_feedback=raw_feedback, lesson_title="")),
            model="mock",
            tokens_in=0,
            tokens_out=0,
        )

    sent_replies = []

    async def _capture_reply(chat_id, reply_to, text, **kwargs):
        assert kwargs.get("parse_mode") == "HTML"
        sent_replies.append(text)

    with (
        patch.object(flow_b, "LLMClient") as LLMCls,
        patch.object(flow_b, "send_reply", AsyncMock(side_effect=_capture_reply)),
        patch.object(flow_b, "send_message", AsyncMock()),
    ):
        LLMCls.return_value.complete = AsyncMock(side_effect=_fake_complete)
        await handle_update(update)

    assert len(sent_replies) == 1
    reply = sent_replies[0]
    # The HTML special chars in the feedback must be escaped.
    assert "a &lt; b" in reply
    assert "b &gt; a" in reply
    assert "a &amp; c" in reply
    # The raw `<`, `>`, `&` from the feedback must NOT appear unescaped
    # (the only `<`, `>` in the reply should be the HTML tags we add).
    # Strip our known tags to check the feedback content.
    import re as _re

    feedback_block = _re.search(r"<blockquote>(.*?)</blockquote>", reply, _re.DOTALL)
    assert feedback_block is not None, "feedback must be wrapped in <blockquote>"
    inner = feedback_block.group(1)
    assert "<" not in inner  # no unescaped `<` inside the blockquote
    assert ">" not in inner  # no unescaped `>` inside the blockquote
    # Every `&` in the inner text must be the start of an HTML entity
    # (&lt;, &gt;, &amp;), not a bare `&`.
    bare_amp = _re.sub(r"&(?:lt|gt|amp|quot|#\d+);", "", inner)
    assert "&" not in bare_amp, f"unescaped `&` in feedback: {inner!r}"
    # No MarkdownV2 escape artifacts.
    assert "\\." not in reply
    assert "\\-" not in reply
    assert "\\(" not in reply
    assert "\\)" not in reply
    # The plain-text punctuation must survive unescaped (html.escape only
    # touches <, >, & — not . - ( )).
    assert "arr[i-1]" in reply
    assert "(n-1)" in reply


@pytest.mark.asyncio
async def test_coach_reply_includes_lesson_footer_when_lesson_fires(sqlite_session_factory):
    """Phase 9 (docs/telegram-formatting.md §3.2.3): when a lesson fires, the
    reply includes an <i>footer</i> line OUTSIDE the <blockquote>, built in
    code from the lesson decision fields (saved/reinforced/retired).
    """
    # Insert an existing lesson so the reinforce path fires (count 1 → 2).
    _insert_pending_review(sqlite_session_factory, message_id=50)
    with Session(sqlite_session_factory) as session:
        session.add(
            db_models.TutorLesson(
                title="off-by-one on inclusive bounds",
                category="binary-search",
                times_reinforced=1,
                active=True,
            )
        )
        session.commit()
    update = _make_update(text="my code", reply_to_message_id=50)

    async def _fake_complete(system, user, **kw):
        return LLMResponse(
            text=json.dumps(
                _coach_json(
                    tutor_feedback="good progress.",
                    lesson_title="off-by-one on inclusive bounds",
                    lesson_category="binary-search",
                    lesson_is_recurring=True,
                    lesson_should_graduate=False,
                )
            ),
            model="mock",
            tokens_in=0,
            tokens_out=0,
        )

    sent_replies = []

    async def _capture_reply(chat_id, reply_to, text, **kwargs):
        assert kwargs.get("parse_mode") == "HTML"
        sent_replies.append(text)

    with (
        patch.object(flow_b, "LLMClient") as LLMCls,
        patch.object(flow_b, "send_reply", AsyncMock(side_effect=_capture_reply)),
        patch.object(flow_b, "send_message", AsyncMock()),
    ):
        LLMCls.return_value.complete = AsyncMock(side_effect=_fake_complete)
        await handle_update(update)

    assert len(sent_replies) == 1
    reply = sent_replies[0]
    # The feedback is in <blockquote>, the footer is in <i> outside it.
    assert "<blockquote>" in reply
    assert "</blockquote>" in reply
    assert "<i>" in reply
    assert "</i>" in reply
    # The footer comes AFTER the blockquote (reinforce message names the lesson).
    assert reply.index("</blockquote>") < reply.index("<i>")
    # The lesson title (which contains a hyphen) must appear unescaped in
    # the footer (it's built in code from a DB row, not LLM output, but the
    # escape regression must hold for code-built strings too).
    assert "off-by-one" in reply
    assert "\\-" not in reply


# ===========================================================================
# FR-2.6 golden double-gate lesson tests
# ===========================================================================


def _coach_result_with_lesson(graduate: bool) -> CoachResult:
    return CoachResult(
        tutor_feedback="fb",
        lesson_title="off-by-one on inclusive bounds",
        lesson_category="binary-search",
        lesson_is_recurring=True,
        lesson_should_graduate=graduate,
        solved=True,
        status="solved",
        next_step="next",
        time_spent_min=None,
    )


def test_golden_gate_coach_says_graduate_but_db_count_4_bumps_not_graduates(
    sqlite_session_factory,
):
    """GOLDEN TEST (AGENTS.md gotcha #4): coach says lesson_should_graduate=true
    BUT the DB row's times_reinforced is 4 (< threshold 5) → BUMP, not graduate.

    This test MUST FAIL if the DB-count gate is removed (i.e., if the code
    trusts the coach's flag alone). The coach hallucinating a count is a
    known failure mode; the DB is the source of truth.
    """
    engine = sqlite_session_factory
    # Insert an active lesson with times_reinforced = 4 (below threshold).
    with Session(engine) as session:
        lesson = db_models.TutorLesson(
            title="off-by-one on inclusive bounds",
            category="binary-search",
            times_reinforced=4,
            active=True,
        )
        session.add(lesson)
        session.commit()
        session.refresh(lesson)
        active = [lesson]

    result = _coach_result_with_lesson(graduate=True)
    outcome = _lesson_decision(result, active)

    # Must bump, NOT graduate. The DB count (4) is below threshold (5),
    # so even though the coach says graduate, we only reinforce.
    assert outcome.action == "reinforced"
    assert outcome.times_reinforced == 5
    # Lesson stays active (not retired).
    with Session(engine) as session:
        row = session.exec(
            select(db_models.TutorLesson).where(
                db_models.TutorLesson.title == "off-by-one on inclusive bounds"
            )
        ).first()
    assert row.active is True
    assert row.times_reinforced == 5


def test_golden_gate_coach_says_graduate_and_db_count_5_retires(
    sqlite_session_factory,
):
    """Coach says graduate AND DB count ≥ 5 → retire (active=false)."""
    engine = sqlite_session_factory
    with Session(engine) as session:
        lesson = db_models.TutorLesson(
            title="off-by-one on inclusive bounds",
            category="binary-search",
            times_reinforced=5,
            active=True,
        )
        session.add(lesson)
        session.commit()
        session.refresh(lesson)
        active = [lesson]

    result = _coach_result_with_lesson(graduate=True)
    outcome = _lesson_decision(result, active)

    assert outcome.action == "retired"
    assert outcome.times_reinforced == 6  # bumped then retired
    with Session(engine) as session:
        row = session.exec(
            select(db_models.TutorLesson).where(
                db_models.TutorLesson.title == "off-by-one on inclusive bounds"
            )
        ).first()
    assert row.active is False


def test_lesson_reinforce_bumps_existing_no_duplicate(sqlite_session_factory):
    """FR-2.6: recurring lesson bumps existing row, no duplicate inserted."""
    engine = sqlite_session_factory
    with Session(engine) as session:
        lesson = db_models.TutorLesson(
            title="check empty input",
            category="dp",
            times_reinforced=2,
            active=True,
        )
        session.add(lesson)
        session.commit()
        session.refresh(lesson)
        active = [lesson]

    result = CoachResult(
        tutor_feedback="fb",
        lesson_title="check empty input",
        lesson_category="dp",
        lesson_is_recurring=True,
        lesson_should_graduate=False,
        solved=True,
        status="solved",
        next_step="x",
        time_spent_min=None,
    )
    outcome = _lesson_decision(result, active)
    assert outcome.action == "reinforced"
    assert outcome.times_reinforced == 3
    with Session(engine) as session:
        rows = session.exec(
            select(db_models.TutorLesson).where(db_models.TutorLesson.title == "check empty input")
        ).all()
    assert len(rows) == 1  # no duplicate


def test_lesson_new_lesson_inserted_with_count_one(sqlite_session_factory):
    """FR-2.6: new lesson → insert with times_reinforced=1, active=true."""
    result = CoachResult(
        tutor_feedback="fb",
        lesson_title="sliding window invariant",
        lesson_category="two-pointers",
        lesson_is_recurring=False,
        lesson_should_graduate=False,
        solved=True,
        status="solved",
        next_step="x",
        time_spent_min=None,
    )
    outcome = _lesson_decision(result, active_lessons=[])
    assert outcome.action == "saved"
    assert outcome.times_reinforced == 1


def test_lesson_no_title_no_op(sqlite_session_factory):
    """No generalizable lesson (empty title) → no-op."""
    result = CoachResult(
        tutor_feedback="fb",
        lesson_title="",
        lesson_category="",
        lesson_is_recurring=False,
        lesson_should_graduate=False,
        solved=True,
        status="solved",
        next_step="x",
        time_spent_min=None,
    )
    outcome = _lesson_decision(result, active_lessons=[])
    assert outcome.action == "none"


# ===========================================================================
# FR-2.5 status-note tests
# ===========================================================================


@pytest.mark.asyncio
async def test_status_note_skipped_logs_status_only(sqlite_session_factory):
    """'skipped' → status-only result, no review text required (FR-2.5).

    The coach prompt handles the branch within the LLM call; we verify the
    parsed status flows through to the leetcode_log row.
    """
    _insert_pending_review(sqlite_session_factory, message_id=50)
    update = _make_update(text="skipped", reply_to_message_id=50)

    with (
        patch.object(flow_b, "LLMClient") as LLMCls,
        patch.object(flow_b, "send_reply", AsyncMock()),
        patch.object(flow_b, "send_message", AsyncMock()),
    ):
        LLMCls.return_value.complete = AsyncMock(
            return_value=LLMResponse(
                text=json.dumps(_coach_json(status="skipped", solved=False, lesson_title="")),
                model="mock",
                tokens_in=0,
                tokens_out=0,
            )
        )
        await handle_update(update)

    with Session(sqlite_session_factory) as session:
        logs = session.exec(select(db_models.LeetCodeLog)).all()
    assert len(logs) == 1
    assert logs[0].status == "skipped"
    # solved=false → leetcode_problems.solved stays false.
    with Session(sqlite_session_factory) as session:
        prob = session.get(db_models.LeetCodeProblem, "problem-1")
        review = session.exec(select(db_models.PendingReview)).one()
    assert prob.solved is False
    assert review.status == db_models.ReviewStatus.SKIPPED


# ===========================================================================
# #024 coach response parsing tests
# ===========================================================================


def test_parse_coach_result_valid():
    data = _coach_json()
    result = _parse_coach_result(data)
    assert result.status == "solved"
    assert result.solved is True


def test_parse_coach_result_rejects_missing_field():
    data = _coach_json()
    del data["tutor_feedback"]
    with pytest.raises(CoachParseError, match="missing fields"):
        _parse_coach_result(data)


def test_parse_coach_result_rejects_bad_status():
    data = _coach_json(status="invalid")
    with pytest.raises(CoachParseError, match="not in"):
        _parse_coach_result(data)


def test_parse_coach_result_rejects_graduate_without_recurring():
    """You can't graduate a lesson you didn't match to an existing one."""
    data = _coach_json(lesson_should_graduate=True, lesson_is_recurring=False)
    with pytest.raises(CoachParseError, match="lesson_is_recurring=true"):
        _parse_coach_result(data)


def test_parse_coach_result_rejects_bad_lesson_category():
    data = _coach_json(lesson_title="x", lesson_category="not-a-real-category")
    with pytest.raises(CoachParseError, match="lesson_category"):
        _parse_coach_result(data)


def test_parse_coach_result_accepts_empty_lesson_category():
    """Empty lesson_category is valid (means no lesson surfaced)."""
    data = _coach_json(lesson_title="", lesson_category="")
    result = _parse_coach_result(data)
    assert result.lesson_category == ""


# ===========================================================================
# fuzzy title match unit test
# ===========================================================================


def test_fuzzy_title_match_substring_either_way():
    today = datetime.date.today()
    rows = [
        db_models.PendingReview(
            message_id=1,
            problem_slug="a",
            problem_title="Binary Search",
            proposed_at=today,
            status="open",
        ),
        db_models.PendingReview(
            message_id=2,
            problem_slug="b",
            problem_title="Merge Intervals",
            proposed_at=today,
            status="open",
        ),
    ]
    # text contains title
    assert len(_fuzzy_title_match("my binary search solution", rows)) == 1
    # title contains text
    assert len(_fuzzy_title_match("merge", rows)) == 1
    # multiple
    assert len(_fuzzy_title_match("search", rows)) == 1  # only "Binary Search"
    # none
    assert len(_fuzzy_title_match("totally unrelated", rows)) == 0


def test_text_picks_follow_batch_transition_graph(sqlite_session_factory):
    _insert_daily_candidates(sqlite_session_factory, count=2)
    batch_id = _attach_created_batch(sqlite_session_factory)

    first, reason = flow_b._reserve_candidates(batch_id=batch_id, pick_indices=[1])
    assert reason is None and len(first) == 1
    with Session(sqlite_session_factory) as session:
        assert (
            session.get(db_models.ProposalBatch, batch_id).status
            == db_models.ProposalBatchStatus.ACTIVE
        )

    second, reason = flow_b._reserve_candidates(batch_id=batch_id, pick_indices=[2])
    assert reason is None and len(second) == 1
    with Session(sqlite_session_factory) as session:
        assert (
            session.get(db_models.ProposalBatch, batch_id).status
            == db_models.ProposalBatchStatus.PICKED
        )
