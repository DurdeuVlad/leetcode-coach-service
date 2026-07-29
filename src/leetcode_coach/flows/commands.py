"""Slash command router (issue #035, FR-6).

Single responsibility: parse ``/``-prefixed Telegram messages and dispatch
to the matching flow internal. This is the **text-driven exception** to
FR-2.1's data-driven routing rule — the router runs *before* FR-2.2 reply
correlation and returns early if the message is a recognized command.

Routing priority in ``flow_b.handle_update``:
1. ``route_command(update)`` — if the message starts with ``/`` and is a
   recognized command, handle it and return. If it starts with ``/`` but
   is unknown, reply "unknown command" and return. Either way, FR-2.2 is
   skipped.
2. Otherwise, fall through to FR-2.2 reply correlation (unchanged).

Commands (FR-6.1 - 6.6):
- ``/propose`` → ``flow_a.propose_5(dry_run=False)``.
- ``/pick <ints>`` → ``flow_b._pick_parse_path`` (reuses the regex parse +
  cap-at-2 + "no valid picks" reply that already exists).
- ``/coach <text>`` → #037 (target resolution + ``_coach_pass_path``).
- ``/status`` → #038 (read-only DB dump, no LLM).
- ``/why <slug>`` → #038 (single bounded LLM call).
- unknown ``/foo`` → short "unknown command" reply, no LLM, no DB write
  (FR-6.6).

Security (FR-6.5): commands rely on the existing chat-id allowlist. The
webhook enforces it before ``handle_update`` is called, so by the time we
reach ``route_command`` the chat is already verified. No new auth surface.

The router is the **only** place that knows about commands. Flow internals
stay command-agnostic — they're called the same way the admin API calls
them, just with ``dry_run=False``.
"""

from __future__ import annotations

import datetime

import structlog
from sqlmodel import select
from telegram import Update

from leetcode_coach.db.base import get_session
from leetcode_coach.db.models import LeetCodeLog, LeetCodeProblem, PendingReview, TutorLesson
from leetcode_coach.flows import flow_a, flow_b
from leetcode_coach.integrations.llm import LLMClient
from leetcode_coach.integrations.telegram import send_message

log = structlog.get_logger("commands")

# Known command names. The dispatch table maps each to a handler coroutine.
# /status and /why are stubbed here and fleshed out in #038.
_KNOWN_COMMANDS = ("propose", "pick", "coach", "status", "why")


async def route_command(update: Update) -> bool:
    """If ``update`` is a slash command, handle it and return ``True``.

    Returns ``False`` when the message is not ``/``-prefixed, so the caller
    (``flow_b.handle_update``) falls through to FR-2.2 data-driven routing.

    FR-6.1: any message starting with ``/`` is a command.
    FR-6.6: unknown command → short reply, no LLM, no DB write, return True
    (the message was still a command — we just didn't recognize it).
    """
    msg = update.message
    if msg is None or msg.text is None:
        return False
    text = msg.text.strip()
    if not text.startswith("/"):
        return False

    chat_id = msg.chat.id

    # Parse: first whitespace-delimited token = command (strip leading "/"
    # and lowercase), remainder = args string (may be empty).
    parts = text.split(maxsplit=1)
    command = parts[0].lstrip("/").lower()
    args = parts[1] if len(parts) > 1 else ""

    handler = _DISPATCH.get(command)
    if handler is None:
        # FR-6.6: unknown command. Short reply, no side effects.
        await send_message(
            chat_id,
            f"Unknown command: /{command}. Try /propose, /pick, /coach, /status, /why.",
        )
        log.info("command_unknown", command=command)
        return True

    log.info("command_dispatch", command=command, args_len=len(args))
    await handler(update, args)
    return True


# --- Handlers ---------------------------------------------------------------
#
# Each handler is an async function (update, args) -> None. They call the
# existing flow internals with dry_run=False — the same internals the admin
# API calls with dry_run=True. No business logic lives here; this is glue.


async def _cmd_propose(update: Update, args: str) -> None:
    """``/propose`` → run Flow A (5-candidate proposal) end-to-end.

    Flow A persists the 5 candidates to ``daily_candidates`` and sends the
    Telegram message. The pinned-message refresh hook (#039) fires inside
    ``propose_5`` once #039 lands.
    """
    await flow_a.propose_5(dry_run=False)


async def _cmd_pick(update: Update, args: str) -> None:
    """``/pick <ints>`` → run Flow B's pick-parse path.

    ``_pick_parse_path`` already does the regex parse (``\\d+``), caps at
    MAX_PICKS (2), maps indices → today's ``daily_candidates`` rows, sends
    the per-problem Telegram messages, creates Google Tasks, and inserts
    ``pending_review`` rows. We just pass the args string through — it
    expects the "1 2" shape.
    """
    chat_id = update.message.chat.id
    await flow_b._pick_parse_path(chat_id, args, dry_run=False)


async def _cmd_coach(update: Update, args: str) -> None:
    """``/coach <text>`` → run Flow B's coach pass (issue #037, FR-6.4).

    Target resolution is a 3-step waterfall (cheapest first, no LLM in the
    resolver — KISS):

    1. **Reply-to present**: look up ``pending_review`` by the replied-to
       ``message_id`` (same as FR-2.2.1). The text after ``/coach`` is the
       submission. If the replied-to message isn't an open problem thread,
       reply "not a problem thread" and stop.

    2. **First token is a slug of an open review**: parse
       ``/coach <slug> <text>``. If the first whitespace-delimited token
       after ``/coach`` matches the ``problem_slug`` of an open
       ``pending_review`` today, use that row; the rest is the submission.
       This is the only ambiguity (issue #037 notes): if the first token
       is a valid slug, treat it as the slug; otherwise treat the whole
       remainder as the submission and fall through to step 3.

    3. **No slug, single open review**: if exactly one open
       ``pending_review`` today, use it; the whole remainder is the
       submission. If >1, reply with the list of open problems and stop
       (never guess — mirrors FR-2.2.2). If 0, reply "no open problems
       today" and stop.

    The 0-target and >1-target cases: short reply, no LLM call, no DB
    write. After a successful coach, the pinned-message refresh hook
    (#039) fires inside ``_coach_pass_path`` once #039 lands.
    """
    chat_id = update.message.chat.id
    inbound_id = update.message.message_id
    reply_to = update.message.reply_to_message
    today = datetime.date.today()

    # Step 1: reply_to present → lookup by message_id (FR-2.2.1 shape).
    if reply_to is not None and reply_to.message_id is not None:
        with next(get_session()) as session:
            review = session.exec(
                select(PendingReview).where(
                    PendingReview.message_id == reply_to.message_id,
                    PendingReview.proposed_at == today,
                )
            ).first()
        if review is None:
            await send_message(
                chat_id,
                "That message isn't an open problem thread. Reply to a "
                "problem's message, or use /coach <slug> <code>.",
            )
            log.info("coach_no_review_for_reply_to", message_id=reply_to.message_id)
            return
        submission = args
        await flow_b._coach_pass_path(
            chat_id, inbound_id, review, user_text=submission, dry_run=False
        )
        log.info("coach_via_reply_to", problem_slug=review.problem_slug)
        return

    # Steps 2 & 3 need the open reviews for today.
    with next(get_session()) as session:
        open_reviews = session.exec(
            select(PendingReview).where(
                PendingReview.proposed_at == today,
                PendingReview.status == "open",
            )
        ).all()

    # Step 2: first token matches an open review's slug.
    tokens = args.split(maxsplit=1)
    if tokens:
        first_token = tokens[0]
        slug_match = next(
            (r for r in open_reviews if r.problem_slug == first_token), None
        )
        if slug_match is not None:
            submission = tokens[1] if len(tokens) > 1 else ""
            await flow_b._coach_pass_path(
                chat_id, inbound_id, slug_match, user_text=submission, dry_run=False
            )
            log.info("coach_via_slug", problem_slug=slug_match.problem_slug)
            return

    # Step 3: no slug → single-open-review case.
    if len(open_reviews) == 0:
        await send_message(chat_id, "No open problems today.")
        log.info("coach_no_open_reviews")
        return
    if len(open_reviews) > 1:
        lines = [f"- {r.problem_title} ({r.problem_slug})" for r in open_reviews]
        await send_message(
            chat_id,
            "Which problem? Open today:\n" + "\n".join(lines)
            + "\n\nUse /coach <slug> <code>, or reply to a problem's message.",
        )
        log.info("coach_ambiguous", open_count=len(open_reviews))
        return

    review = open_reviews[0]
    await flow_b._coach_pass_path(
        chat_id, inbound_id, review, user_text=args, dry_run=False
    )
    log.info("coach_via_single_open", problem_slug=review.problem_slug)


async def _cmd_status(update: Update, args: str) -> None:
    """``/status`` → deterministic DB dump, no LLM (issue #038, FR-7.1).

    Shows:
    - Active lessons (title + times_reinforced), ordered by count desc.
    - Last 7 days of leetcode_log (date, problem, status, lesson).
    - Current streak (consecutive days with ≥1 coached attempt).

    Read-only: no inserts, no updates, no LLM calls.
    """
    chat_id = update.message.chat.id
    today = datetime.date.today()
    seven_days_ago = today - datetime.timedelta(days=6)  # today + 6 prior = 7 days

    with next(get_session()) as session:
        active_lessons = session.exec(
            select(TutorLesson)
            .where(TutorLesson.active == True)  # noqa: E712 — SQLModel needs ==
            .order_by(TutorLesson.times_reinforced.desc())
        ).all()

        recent_log = session.exec(
            select(LeetCodeLog)
            .where(LeetCodeLog.date >= seven_days_ago)
            .order_by(LeetCodeLog.date.desc(), LeetCodeLog.id.desc())
        ).all()

        streak = _compute_streak(session, today)

    # Format the message.
    lines: list[str] = []

    # Streak
    lines.append(f"🔥 Streak: {streak} day{'s' if streak != 1 else ''}")
    lines.append("")

    # Active lessons
    if active_lessons:
        lines.append("📚 Active lessons:")
        for lesson in active_lessons:
            lines.append(f"  • {lesson.title} ({lesson.times_reinforced}x)")
    else:
        lines.append("📚 No active lessons.")
    lines.append("")

    # Recent log (last 7 days)
    if recent_log:
        lines.append("📋 Last 7 days:")
        for entry in recent_log:
            mark = "✅" if entry.status == "solved" else "📝"
            lesson_str = f" → {entry.lesson_title}" if entry.lesson_title else ""
            lines.append(f"  {entry.date} {mark} {entry.problem_slug}{lesson_str}")
    else:
        lines.append("📋 No attempts in the last 7 days.")

    await send_message(chat_id, "\n".join(lines))
    log.info("status_sent", streak=streak, lessons=len(active_lessons), log_rows=len(recent_log))


async def _cmd_why(update: Update, args: str) -> None:
    """``/why <slug>`` → single bounded LLM call (issue #038, FR-7.2).

    Explains why a problem was proposed or what lesson it targets.
    2-3 sentences, max 300 tokens. Read-only: no DB writes.

    No args → "usage: /why <slug>" reply, no LLM call.
    Bad slug → "no such problem" reply, no LLM call.
    """
    chat_id = update.message.chat.id

    if not args.strip():
        await send_message(chat_id, "Usage: /why <slug>")
        return

    slug = args.strip().split()[0]  # first token only

    with next(get_session()) as session:
        problem = session.get(LeetCodeProblem, slug)
        if problem is None:
            await send_message(chat_id, f"No such problem: {slug}")
            log.info("why_no_such_problem", slug=slug)
            return

        # Gather context: active lessons + recent log for the system prompt.
        active_lessons = session.exec(
            select(TutorLesson)
            .where(TutorLesson.active == True)  # noqa: E712
            .order_by(TutorLesson.times_reinforced.desc())
        ).all()

        recent_log = session.exec(
            select(LeetCodeLog)
            .where(LeetCodeLog.problem_slug == slug)
            .order_by(LeetCodeLog.date.desc())
            .limit(5)
        ).all()

    # Build the LLM prompt.
    lesson_lines = [
        f"- {lesson.title} ({lesson.category}, {lesson.times_reinforced}x)"
        for lesson in active_lessons
    ] or ["(none yet)"]
    log_lines = [
        f"- {e.date}: {e.status}" + (f" → {e.lesson_title}" if e.lesson_title else "")
        for e in recent_log
    ] or ["(no prior attempts)"]

    system = (
        "You are a LeetCode coach. Explain why a problem was proposed or what "
        "lesson it targets, in 2-3 sentences. Be specific and concise. "
        "Reference the user's active lessons and past attempts if relevant."
    )
    user = (
        f"Problem: {problem.title} ({problem.slug})\n"
        f"Difficulty: {problem.difficulty}\n"
        f"Tags: {problem.tags}\n\n"
        "Active lessons:\n" + "\n".join(lesson_lines) + "\n\n"
        "Prior attempts on this problem:\n" + "\n".join(log_lines) + "\n\n"
        f"Why was {problem.slug} proposed, or what lesson does it target?"
    )

    client = LLMClient()
    response = await client.complete(system, user, max_tokens=300)
    await send_message(chat_id, response.text.strip())
    log.info("why_sent", slug=slug, tokens_out=response.tokens_out)


def _compute_streak(session, today: datetime.date) -> int:
    """Count consecutive days (ending today or yesterday) with ≥1 coached
    attempt (status = 'solved' or 'reviewed').

    A coached attempt is one where the user submitted code and got coach
    feedback — i.e. status is 'solved' or 'reviewed'. 'skipped' and
    'saw_solution' don't count (passive, no coaching).
    """
    coached_statuses = ("solved", "reviewed")

    # Get all distinct dates with coached attempts, up to today.
    rows = session.exec(
        select(LeetCodeLog.date)
        .where(
            LeetCodeLog.date <= today,
            LeetCodeLog.status.in_(coached_statuses),
        )
        .distinct()
    ).all()
    coached_dates = set(rows)

    if not coached_dates:
        return 0

    # Streak can start from today or yesterday (if today has no attempt yet).
    if today in coached_dates:
        cursor = today
    elif (today - datetime.timedelta(days=1)) in coached_dates:
        cursor = today - datetime.timedelta(days=1)
    else:
        return 0

    streak = 0
    while cursor in coached_dates:
        streak += 1
        cursor -= datetime.timedelta(days=1)
    return streak


# Dispatch table — built after the handlers so the names resolve.
_DISPATCH = {
    "propose": _cmd_propose,
    "pick": _cmd_pick,
    "coach": _cmd_coach,
    "status": _cmd_status,
    "why": _cmd_why,
}
