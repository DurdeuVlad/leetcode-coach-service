"""Flow B — reply routing, pick-parse, coach pass, lesson decision, post-coach updates.

Single responsibility (per #034): orchestrate the FR-2 pipeline. Holds no
prompt text (that's `prompts/coach.py`), no raw HTTP (that's the integration
clients), no SQL strings (that's `db/`).

Routing is data-driven, not text-driven (FR-2.1):
- reply_to_message_id in `pending_review` → coach pass path
- reply_to_message_id absent from `pending_review` → pick-parse path
  (the reply was to the 5-list message, whose id is never in pending_review)
- no reply_to → fuzzy title match against today's open `pending_review` rows
  (exactly 1 → coach; 0 or >1 → clarification prompt, never guess)

The adaptability loop (FR-2.6) is double-gated for graduation:
- coach says `lesson_should_graduate = true` AND
- the DB row's `times_reinforced >= 5` (read from DB, NOT from the coach —
  AGENTS.md gotcha #4: the coach hallucinating a count is a known failure
  mode).

BUG-2 fix (FR-2.7 step 3, AGENTS.md gotcha #2): the n8n `mark complete`
node dropped coach feedback by replacing notes. We call
`google_tasks.mark_complete(task_id, notes_append=tutor_feedback)` —
append, never replace.
"""

from __future__ import annotations

import datetime
import json
import re
from dataclasses import dataclass

import structlog
from sqlmodel import select
from telegram import Update

from leetcode_coach.db.base import get_session
from leetcode_coach.db.models import (
    DailyCandidate,
    LeetCodeLog,
    LeetCodeProblem,
    PendingReview,
    TutorLesson,
)
from leetcode_coach.errors import LeetCodeCoachError
from leetcode_coach.integrations.google_tasks import create_task, mark_complete
from leetcode_coach.integrations.llm import LLMClient, parse_json_response
from leetcode_coach.integrations.telegram import send_message, send_reply
from leetcode_coach.prompts.coach import COACH_PROMPT, COACH_SYSTEM

log = structlog.get_logger("flow_b")

# FR-2.6 graduation threshold — single named constant so #031 can recalibrate
# without touching the logic (Open/Closed principle).
GRADUATION_THRESHOLD = 5

# FR-2.3: at most 2 picks per day.
MAX_PICKS = 2

# FR-2.5 status-note keywords (lowercase). The coach prompt handles the
# branch within the LLM call, but we use these for a tiny log hint.
_STATUS_KEYWORDS = ("skipped", "saw solution", "saw the solution")


# --- Coach response contract (#024) ---
#
# The LLM returns a JSON object with these fields (see prompts/coach.py):
#   tutor_feedback, lesson_title, lesson_category, lesson_is_recurring,
#   lesson_should_graduate, solved, status, next_step
# `time_spent_min` is optional (the prompt doesn't ask for it; the mock
# includes it; the leetcode_log column is nullable).
_REQUIRED_COACH_FIELDS = (
    "tutor_feedback",
    "lesson_title",
    "lesson_category",
    "lesson_is_recurring",
    "lesson_should_graduate",
    "solved",
    "status",
    "next_step",
)
_VALID_STATUSES = ("solved", "reviewed", "skipped", "saw_solution")
# From the coach prompt's lesson_category enumeration.
_VALID_LESSON_CATEGORIES = (
    "binary-search",
    "dp",
    "graphs",
    "two-pointers",
    "hash-map",
    "heap",
    "backtracking",
    "greedy",
    "design",
)


class CoachParseError(LeetCodeCoachError):
    """The coach LLM returned structurally invalid JSON. Fail loud (NFR-1
    layer 2) — never fabricate fields or "log estimated defaults"."""


class FlowBRoutingError(LeetCodeCoachError):
    """Ambiguous routing (FR-2.2): 0 or >1 fuzzy matches with no reply_to."""


@dataclass
class CoachResult:
    """Typed coach pass output (#024). Handed to #025 (lesson decision) and
    #026 (post-coach updates)."""

    tutor_feedback: str
    lesson_title: str
    lesson_category: str
    lesson_is_recurring: bool
    lesson_should_graduate: bool
    solved: bool
    status: str
    next_step: str
    time_spent_min: int | None  # optional, not in the prompt contract


@dataclass
class LessonOutcome:
    """What #025 decided about a lesson: saved (new), reinforced (bumped), or
    retired (graduated). `None` row means no lesson fired."""

    action: str  # "saved" | "reinforced" | "retired" | "none"
    title: str
    times_reinforced: int  # the new count after the update


# ===========================================================================
# #021 — handle_update: the data-driven router (FR-2.2)
# ===========================================================================


async def handle_update(update: Update) -> None:
    """Route an inbound Telegram Update per FR-2.2.

    This function *routes only* — it decides the path and delegates to
    `_pick_parse_path` (#022) or `_coach_pass_path` (#024+#025+#026). It
    performs no side effects beyond the delegation.

    Routing priority (FR-2.1 amended, FR-6):
    0. Slash command (FR-6): if the message starts with `/`, dispatch via
       `flows.commands.route_command` and return. This is the text-driven
       exception to the data-driven rule below.
    1. reply_to_message.message_id present:
       - found in pending_review (today, any status) → coach pass
       - not found → pick-parse (reply was to the 5-list)
    2. no reply_to: fuzzy title match against today's OPEN pending_review:
       - exactly 1 → coach pass
       - 0 or >1 → clarification prompt, stop (never guess)
    """
    msg = update.message
    if msg is None or msg.text is None:
        # callback_query or non-text update — out of scope for v1 (FR-2.1
        # mentions callback_query for the inline keyboard UI, but the
        # current Flow A sends plain text; revisit if/when Flow A adds
        # reply_markup). Silent drop.
        log.info("flow_b_skip_non_text", update_id=update.update_id)
        return

    # FR-6: slash commands are parsed BEFORE data-driven routing. The
    # router returns True if it handled the message (recognized or unknown
    # command); False if the message is not `/`-prefixed and we should
    # fall through to FR-2.2. Imported lazily to avoid a circular import
    # at module load (commands.py imports flow_b for the handlers).
    from leetcode_coach.flows.commands import route_command

    if await route_command(update):
        return

    chat_id = msg.chat.id
    text = msg.text.strip()
    reply_to = msg.reply_to_message

    today = datetime.date.today()

    if reply_to is not None and reply_to.message_id is not None:
        # FR-2.2.1: reply_to present → lookup pending_review by message_id.
        with next(get_session()) as session:
            row = session.exec(
                select(PendingReview).where(
                    PendingReview.message_id == reply_to.message_id,
                    PendingReview.proposed_at == today,
                )
            ).first()
        if row is not None:
            log.info(
                "flow_b_route_coach_reply_to",
                message_id=reply_to.message_id,
                problem_slug=row.problem_slug,
            )
            await _coach_pass_path(chat_id, msg.message_id, row, text)
            return
        # Not found → it was a reply to the 5-list → pick-parse path.
        log.info(
            "flow_b_route_pick_parse",
            reply_to_message_id=reply_to.message_id,
        )
        await _pick_parse_path(chat_id, text)
        return

    # FR-2.2.2: no reply_to → fuzzy title match against today's OPEN rows.
    with next(get_session()) as session:
        open_rows = session.exec(
            select(PendingReview).where(
                PendingReview.proposed_at == today,
                PendingReview.status == "open",
            )
        ).all()

    matches = _fuzzy_title_match(text, open_rows)
    if len(matches) == 1:
        row = matches[0]
        log.info(
            "flow_b_route_coach_fuzzy",
            problem_slug=row.problem_slug,
        )
        await _coach_pass_path(chat_id, msg.message_id, row, text)
        return

    # 0 or >1 matches → clarification prompt, stop. Never guess (FR-2.2).
    if not open_rows:
        await send_message(
            chat_id,
            "No open problems today. Reply to the 5-list with two numbers " "(e.g. '2 5') to pick.",
        )
    else:
        lines = [f"{i + 1}. {r.problem_title}" for i, r in enumerate(open_rows)]
        await send_message(
            chat_id,
            "Which one — " + " ".join(lines) + " ? Reply to the problem's "
            "message, or paste the problem title.",
        )
    log.info("flow_b_route_clarification", matches=len(matches))


def _fuzzy_title_match(text: str, rows: list[PendingReview]) -> list[PendingReview]:
    """FR-2.2.2 fuzzy match: case-insensitive substring match either way
    (text contains title OR title contains text). KISS — no embeddings,
    no scoring; the spec says "fuzzy title match" and this is the simplest
    interpretation that satisfies it."""
    t = text.lower()
    return [
        r
        for r in rows
        if r.problem_title and (t in r.problem_title.lower() or r.problem_title.lower() in t)
    ]


# ===========================================================================
# #022 — pick-parse path (FR-2.3, FR-2.4)
# ===========================================================================


async def _pick_parse_path(chat_id: str, text: str, *, dry_run: bool = False) -> list[dict]:
    """Parse a pick-list reply (e.g. '2 5') into ≤2 chosen problems, then
    create per-problem threads, Google Tasks, and pending_review rows.

    FR-2.3: regex `\\d+`, no LLM. Cap at MAX_PICKS (2). Map indices → today's
    daily_candidates rows (ordered by pick_index). Empty/invalid → short
    "no valid picks" message, persist nothing, return.

    FR-2.4: for each chosen problem, IN ORDER:
    1. Send per-problem Telegram message (incl. coaching_hint); capture message_id.
    2. Create Google Task (title, notes, due=next day); capture task_id.
    3. Insert pending_review row (message_id, google_task_id, slug, title, today, open).

    When ``dry_run`` is True, Telegram sends are skipped (message_id = -1) but
    Google Tasks and pending_review rows are still created. Returns a list of
    dicts describing each created thread (empty in the non-dry-run path).
    """
    # FR-2.3: regex parse, cap at MAX_PICKS.
    nums = [int(n) for n in re.findall(r"\d+", text)]
    nums = [n for n in nums if 1 <= n <= 5][:MAX_PICKS]
    if not nums:
        if not dry_run:
            await send_message(chat_id, "No valid picks. Reply with up to 2 numbers, e.g. '2 5'.")
        log.info("flow_b_pick_parse_empty", text=text[:100], dry_run=dry_run)
        return []

    today = datetime.date.today()
    with next(get_session()) as session:
        candidates = session.exec(
            select(DailyCandidate)
            .where(DailyCandidate.proposed_at == today)
            .order_by(DailyCandidate.pick_index)
        ).all()

    if not candidates:
        # No 5-list was persisted today (Flow A didn't run, or #020 failed).
        # Fail loud — this is a state error, not user error.
        if not dry_run:
            await send_message(chat_id, "No candidate list today. Run /propose first.")
        log.warning("flow_b_pick_parse_no_candidates", date=today.isoformat(), dry_run=dry_run)
        return []

    chosen: list[DailyCandidate] = []
    for n in nums:
        # pick_index is 1-based; candidates ordered by pick_index.
        for c in candidates:
            if c.pick_index == n:
                chosen.append(c)
                break

    if not chosen:
        if not dry_run:
            await send_message(chat_id, "No valid picks. Reply with up to 2 numbers, e.g. '2 5'.")
        log.info("flow_b_pick_parse_no_match", nums=nums, dry_run=dry_run)
        return []

    log.info("flow_b_pick_parse_chosen", count=len(chosen), indices=nums, dry_run=dry_run)

    # FR-2.4: per-pick side effects, in order.
    due_date = (today + datetime.timedelta(days=1)).isoformat()
    created_threads: list[dict] = []
    for i, c in enumerate(chosen, start=1):
        # Step 1: send per-problem message, capture message_id.
        per_problem_text = (
            f"*Problem {i}/{len(chosen)}: {c.title}* ({c.difficulty})\n\n"
            f"{c.coaching_hint}\n\n"
            f"Send your code as a reply to this message."
        )
        if dry_run:
            message_id = -1
        else:
            message_id = await send_message(chat_id, per_problem_text)

        # Step 2: create Google Task, capture task_id.
        notes = f"slug: {c.slug}\nreasoning: {c.reasoning}\nhint: {c.coaching_hint}"
        task_id = await create_task(c.title, notes, due_date)

        # Step 3: insert pending_review row.
        with next(get_session()) as session:
            review = PendingReview(
                message_id=message_id,
                google_task_id=task_id,
                problem_slug=c.slug,
                problem_title=c.title,
                proposed_at=today,
                status="open",
            )
            session.add(review)
            session.commit()
            session.refresh(review)
            pending_review_id = review.id
        log.info(
            "flow_b_pick_parse_created",
            problem_slug=c.slug,
            message_id=message_id,
            task_id=task_id,
            dry_run=dry_run,
        )
        created_threads.append(
            {
                "pick_index": c.pick_index,
                "problem_slug": c.slug,
                "problem_title": c.title,
                "difficulty": c.difficulty,
                "message_id": message_id,
                "task_id": task_id,
                "pending_review_id": pending_review_id,
            }
        )

    # FR-8.2: refresh the pinned progression message after picks are created.
    # Fire-and-forget: a pinned-message failure must not fail the flow.
    if not dry_run:
        try:
            from leetcode_coach.flows.pinned import refresh_pinned_message

            await refresh_pinned_message()
        except Exception:
            log.warning("pinned_refresh_failed_after_pick")

    return created_threads


# ===========================================================================
# #024 — coach pass: call LLM + parse structured response (FR-2.5)
# ===========================================================================


async def _coach_pass_path(
    chat_id: str,
    inbound_message_id: int,
    review: PendingReview,
    user_text: str,
    *,
    dry_run: bool = False,
) -> tuple[CoachResult, LessonOutcome, str]:
    """Run the coach LLM call for a submission, parse the response, then run
    the lesson decision (#025) and post-coach updates (#026).

    `review` is the matched `pending_review` row. `inbound_message_id` is
    the user's inbound message id — we reply to it (#026 step 5).

    When ``dry_run`` is True, the Telegram reply is skipped but all DB writes
    and the Google Task update still happen. Returns a tuple of
    ``(CoachResult, LessonOutcome, reply_text)`` so callers (admin API, tests)
    can inspect the full output without scraping Telegram.
    """
    # Gather inputs: problem metadata + active lessons.
    with next(get_session()) as session:
        problem = session.get(LeetCodeProblem, review.problem_slug)
        active_lessons = session.exec(
            select(TutorLesson).where(TutorLesson.active == True)  # noqa: E712
        ).all()

    problem_title = review.problem_title
    problem_url = problem.url if problem is not None else ""
    difficulty = problem.difficulty if problem is not None else "unknown"
    tags = problem.tags if problem is not None else ""

    active_lessons_json = json.dumps(
        [les.model_dump() for les in active_lessons], indent=2, default=str
    )

    user_prompt = COACH_PROMPT.format(
        problem_title=problem_title,
        problem_url=problem_url,
        difficulty=difficulty,
        tags=tags,
        user_text=user_text,
        active_lessons_json=active_lessons_json,
    )

    log.info(
        "flow_b_coach_call",
        problem_slug=review.problem_slug,
        is_status_note=any(k in user_text.lower() for k in _STATUS_KEYWORDS),
        active_lessons_count=len(active_lessons),
        dry_run=dry_run,
    )

    client = LLMClient()
    response = await client.complete(COACH_SYSTEM, user_prompt)

    try:
        data = parse_json_response(response.text)
    except Exception as e:
        raise CoachParseError(f"coach JSON parse failed: {e}") from e

    result = _parse_coach_result(data)

    # #025 — lesson decision (double-gated graduation).
    lesson_outcome = _lesson_decision(result, active_lessons)

    # #026 — post-coach updates (5 ordered steps + BUG-2 notes append).
    reply_text = await _post_coach_updates(
        chat_id=chat_id,
        inbound_message_id=inbound_message_id,
        review=review,
        problem_slug=review.problem_slug,
        result=result,
        lesson_outcome=lesson_outcome,
        dry_run=dry_run,
    )
    return result, lesson_outcome, reply_text


def _parse_coach_result(data: dict) -> CoachResult:
    """Validate + parse the coach LLM JSON into a typed `CoachResult`.

    Fail loud on missing fields, bad enum values, or impossible combinations
    (NFR-1 layer 2 — never fabricate or "log estimated defaults").
    """
    missing = [k for k in _REQUIRED_COACH_FIELDS if k not in data]
    if missing:
        raise CoachParseError(f"coach response missing fields: {missing}")

    status = data["status"]
    if status not in _VALID_STATUSES:
        raise CoachParseError(f"coach status {status!r} not in {_VALID_STATUSES}")

    lesson_category = data["lesson_category"]
    if lesson_category and lesson_category not in _VALID_LESSON_CATEGORIES:
        # The prompt lists 9 categories; an out-of-set value is a hallucination
        # or a model that ignored the contract. Fail loud rather than persist
        # a bad category.
        raise CoachParseError(
            f"coach lesson_category {lesson_category!r} not in " f"{_VALID_LESSON_CATEGORIES}"
        )

    lesson_should_graduate = bool(data["lesson_should_graduate"])
    lesson_is_recurring = bool(data["lesson_is_recurring"])
    # You can't graduate a lesson you didn't match to an existing one.
    if lesson_should_graduate and not lesson_is_recurring:
        raise CoachParseError(
            "lesson_should_graduate=true requires lesson_is_recurring=true "
            "(can't graduate a non-matched lesson)"
        )

    return CoachResult(
        tutor_feedback=str(data["tutor_feedback"]),
        lesson_title=str(data["lesson_title"]),
        lesson_category=lesson_category,
        lesson_is_recurring=lesson_is_recurring,
        lesson_should_graduate=lesson_should_graduate,
        solved=bool(data["solved"]),
        status=status,
        next_step=str(data["next_step"]),
        time_spent_min=int(data["time_spent_min"]) if data.get("time_spent_min") else None,
    )


# ===========================================================================
# #025 — lesson decision (double-gated graduation, FR-2.6)
# ===========================================================================


def _lesson_decision(result: CoachResult, active_lessons: list[TutorLesson]) -> LessonOutcome:
    """Apply the adaptability loop: save / reinforce / graduate a lesson.

    FR-2.6:
    - No generalizable lesson (empty lesson_title) → no-op.
    - Existing active lesson matches (title similarity OR same category) →
      bump times_reinforced. Do not duplicate.
    - New lesson → insert with times_reinforced=1, active=true.
    - Graduation is double-gated: coach says graduate AND DB count >= 5.
      On graduation: active=false.

    The count is ALWAYS read from the DB row, never from the coach
    (AGENTS.md gotcha #4). A coach hallucinating times_reinforced=7 must
    not graduate a count-2 lesson.
    """
    if not result.lesson_title:
        return LessonOutcome(action="none", title="", times_reinforced=0)

    # Find an existing active match: title similarity OR same category.
    existing = _find_existing_lesson(result.lesson_title, result.lesson_category, active_lessons)

    with next(get_session()) as session:
        if existing is not None:
            # Re-fetch to get the authoritative DB count (don't trust the
            # in-memory list — it could be stale within this request).
            row = session.get(TutorLesson, existing.id)
            if row is None:
                # Was deleted concurrently — treat as new.
                session.add(
                    TutorLesson(
                        title=result.lesson_title,
                        category=result.lesson_category,
                        times_reinforced=1,
                        active=True,
                    )
                )
                session.commit()
                return LessonOutcome(action="saved", title=result.lesson_title, times_reinforced=1)

            # Read the authoritative DB count BEFORE bumping — this is the
            # gate value (AGENTS.md gotcha #4: "the DB row's times_reinforced
            # >= 5"). The count as it exists in the DB, not after this session.
            old_count = row.times_reinforced

            # Bump the count (the reinforce action).
            row.times_reinforced += 1
            new_count = row.times_reinforced

            # Double-gate: coach says graduate AND the DB count (before this
            # bump) >= threshold. The DB count is the source of truth — NOT
            # the coach. A coach hallucinating times_reinforced=7 must not
            # graduate a count-2 lesson; and a count-4 lesson hasn't earned
            # graduation even if the coach says so (it needs 5 prior
            # reinforcements, this bump is the 6th).
            if result.lesson_should_graduate and old_count >= GRADUATION_THRESHOLD:
                row.active = False
                session.add(row)
                session.commit()
                return LessonOutcome(action="retired", title=row.title, times_reinforced=new_count)

            # Otherwise just bump (reinforce).
            session.add(row)
            session.commit()
            return LessonOutcome(action="reinforced", title=row.title, times_reinforced=new_count)

        # No existing match → insert new lesson.
        session.add(
            TutorLesson(
                title=result.lesson_title,
                category=result.lesson_category,
                times_reinforced=1,
                active=True,
            )
        )
        session.commit()
        return LessonOutcome(action="saved", title=result.lesson_title, times_reinforced=1)


def _find_existing_lesson(
    title: str, category: str, active_lessons: list[TutorLesson]
) -> TutorLesson | None:
    """FR-2.6 matcher: title similarity (case-insensitive substring either
    way) OR same category. KISS — no embeddings, no scoring. The spec says
    "title similarity OR same category + same pattern"; we approximate
    "same pattern" as "same category" since the coach already decided the
    match is recurring."""
    t = title.lower()
    for les in active_lessons:
        lt = les.title.lower()
        if t in lt or lt in t:
            return les
    if category:
        for les in active_lessons:
            if les.category == category:
                return les
    return None


# ===========================================================================
# #026 — post-coach updates (FR-2.7, 5 ordered steps + BUG-2)
# ===========================================================================


async def _post_coach_updates(
    *,
    chat_id: str,
    inbound_message_id: int,
    review: PendingReview,
    problem_slug: str,
    result: CoachResult,
    lesson_outcome: LessonOutcome,
    dry_run: bool = False,
) -> str:
    """Apply the five ordered side effects after a coach pass (FR-2.7).

    1. Insert leetcode_log row (full schema, incl. lesson_title if fired).
    2. If solved → set leetcode_problems.solved = true.
    3. mark_complete(task_id, notes_append=tutor_feedback) — APPEND (BUG-2).
    4. Update pending_review.status = done.
    5. Telegram reply: short confirmation + coach feedback, naming the
       lesson outcome.

    When ``dry_run`` is True, step 5 (Telegram reply) is skipped but all DB
    writes and the Google Task update still happen. Returns the reply text
    that would have been sent (useful for admin API / tests).

    `GoogleAuthExpiredError` from step 3 is re-raised (the webhook's global
    catch routes it to the distinct #008 alert — it's already a typed error).
    """
    today = datetime.date.today()

    # Step 1: insert leetcode_log row.
    with next(get_session()) as session:
        session.add(
            LeetCodeLog(
                problem_slug=problem_slug,
                date=today,
                status=result.status,
                time_spent_min=result.time_spent_min,
                tutor_feedback=result.tutor_feedback,
                lesson_title=result.lesson_title if lesson_outcome.action != "none" else None,
            )
        )
        session.commit()

    # Step 2: if solved, mark leetcode_problems.solved = true.
    if result.solved:
        with next(get_session()) as session:
            problem = session.get(LeetCodeProblem, problem_slug)
            if problem is not None:
                problem.solved = True
                problem.last_attempted = today
                problem.times_attempted += 1
                session.add(problem)
                session.commit()

    # Step 3: mark_complete with notes_append (BUG-2 fix — append, not replace).
    # GoogleAuthExpiredError propagates to the webhook's global catch → alert.
    await mark_complete(review.google_task_id, notes_append=result.tutor_feedback)

    # Step 4: update pending_review.status = done.
    with next(get_session()) as session:
        row = session.get(PendingReview, review.id)
        if row is not None:
            row.status = "done"
            session.add(row)
            session.commit()

    # Step 5: Telegram reply with confirmation + coach feedback, naming the
    # lesson outcome (saved / reinforced / retired).
    footer = _lesson_footer(lesson_outcome)
    reply_text = f"{result.tutor_feedback}\n\n{footer}" if footer else result.tutor_feedback
    if not dry_run:
        await send_reply(chat_id, inbound_message_id, reply_text)

    log.info(
        "flow_b_post_coach_done",
        problem_slug=problem_slug,
        status=result.status,
        lesson_action=lesson_outcome.action,
        dry_run=dry_run,
    )

    # FR-8.2: refresh the pinned progression message after a coach pass.
    # Fire-and-forget: a pinned-message failure must not fail the flow.
    if not dry_run:
        try:
            from leetcode_coach.flows.pinned import refresh_pinned_message

            await refresh_pinned_message()
        except Exception:
            log.warning("pinned_refresh_failed_after_coach")

    return reply_text


def _lesson_footer(outcome: LessonOutcome) -> str:
    """Build the lesson-outcome footer for the Telegram reply (FR-2.7 step 5).

    Mirrors the prompt's required footer phrasing:
    - saved → 'Saved lesson: <b><title></b>.'
    - reinforced → 'Reinforcing lesson: <b><title></b> (Nth time).'
    - retired → 'Retiring lesson: <b><title></b> — demonstrated consistently.'
    - none → empty string (no footer).
    """
    if outcome.action == "none":
        return ""
    if outcome.action == "saved":
        return f"Saved lesson: <b>{outcome.title}</b>."
    if outcome.action == "reinforced":
        return f"Reinforcing lesson: <b>{outcome.title}</b> ({outcome.times_reinforced}th time)."
    if outcome.action == "retired":
        return f"Retiring lesson: <b>{outcome.title}</b> — demonstrated consistently."
    return ""
