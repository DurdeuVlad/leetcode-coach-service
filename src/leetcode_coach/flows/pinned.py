"""Pinned progression message — one pinned Telegram message with a compact
snapshot of today's status and the user's streak (issue #039, FR-8).

Refreshed after each Flow A run, Flow B pick, and Flow B coach pass. No
cron job (FR-8.2: refresh on event, not on schedule).

The snapshot is a strict subset of ``/status`` (#038): just the counts and
the streak, not the full lesson list. Keep it short — it's pinned, not a
feed.

DRY: the streak calculation is shared with #038's ``/status`` via the
``_compute_streak`` helper in ``flows/commands.py``.
"""

from __future__ import annotations

import datetime

import structlog
from sqlmodel import func, select

from leetcode_coach.config import get_settings
from leetcode_coach.db.base import get_session
from leetcode_coach.db.models import DailyCandidate, LeetCodeLog, PendingReview, TutorLesson
from leetcode_coach.db.queries import get_state, set_state
from leetcode_coach.errors import TelegramError
from leetcode_coach.flows.commands import _compute_streak
from leetcode_coach.integrations.telegram import (
    edit_message_text,
    pin_message,
    send_message,
    unpin_message,
)

log = structlog.get_logger("pinned")

_PINNED_KEY = "pinned_message_id"

# Statuses that count as "coached" for the streak (same as #038).
_COACHED_STATUSES = ("solved", "reviewed")


def _build_snapshot() -> str:
    """Build the compact pinned-message text (≤10 lines).

    Snapshot contents (FR-8.1):
    - Today's status counts: proposed (daily_candidates), picked (open
      pending_review), coached (leetcode_log today with coached status),
      expired (pending_review status=expired).
    - Active lessons count.
    - Current streak.
    """
    today = datetime.date.today()

    with next(get_session()) as session:
        # Proposed: today's daily_candidates count.
        proposed = session.exec(
            select(func.count(DailyCandidate.id)).where(DailyCandidate.proposed_at == today)
        ).one()

        # Picked: today's pending_review with status=open (actively being worked).
        picked = session.exec(
            select(func.count(PendingReview.id)).where(
                PendingReview.proposed_at == today,
                PendingReview.status == "open",
            )
        ).one()

        # Coached: today's leetcode_log entries with coached status.
        coached = session.exec(
            select(func.count(LeetCodeLog.id)).where(
                LeetCodeLog.date == today,
                LeetCodeLog.status.in_(_COACHED_STATUSES),
            )
        ).one()

        # Expired: today's pending_review with status=expired.
        expired = session.exec(
            select(func.count(PendingReview.id)).where(
                PendingReview.proposed_at == today,
                PendingReview.status == "expired",
            )
        ).one()

        # Active lessons count.
        active_lessons = session.exec(
            select(func.count(TutorLesson.id)).where(TutorLesson.active == True)  # noqa: E712
        ).one()

        # Streak (shared helper from #038).
        streak = _compute_streak(session, today)

    lines = [
        "📊 Today's Progress",
        f"Proposed: {proposed} | Picked: {picked} | Coached: {coached} | Expired: {expired}",
        f"📚 Active lessons: {active_lessons}",
        f"🔥 Streak: {streak} day{'s' if streak != 1 else ''}",
    ]
    return "\n".join(lines)


async def refresh_pinned_message() -> None:
    """Refresh the pinned progression message (FR-8).

    - If ``pinned_message_id`` is unset in ``bot_state``, create + pin a
      new message and store the ID.
    - If present, ``editMessageText`` on that message with the new snapshot.
    - On edit failure: if "message is not modified" → no-op (not an error).
      If "message to edit not found" or other → unpin old (best-effort),
      create + pin a new message, store the new ID.

    Fire-and-forget from the flow's perspective: a failure here must not
    fail the flow itself. The caller wraps this in try/except.
    """
    chat_id = get_settings().telegram_chat_id
    if not chat_id:
        log.info("pinned_skip_no_chat_id")
        return

    snapshot = _build_snapshot()
    pinned_id_str = get_state(_PINNED_KEY)

    if pinned_id_str is None:
        await _create_and_pin(chat_id, snapshot)
        return

    pinned_id = int(pinned_id_str)
    try:
        await edit_message_text(chat_id, pinned_id, snapshot)
        log.info("pinned_refreshed", message_id=pinned_id)
    except TelegramError as e:
        err_str = str(e).lower()
        if "message is not modified" in err_str:
            # FR-8.4: this is a no-op, not an error.
            log.info("pinned_not_modified", message_id=pinned_id)
            return
        # Real failure (message deleted, permissions changed, etc.).
        # Recovery path: unpin old (best-effort), create + pin new.
        log.warning("pinned_edit_failed_recreating", message_id=pinned_id, error=str(e))
        try:
            await unpin_message(chat_id, pinned_id)
        except TelegramError:
            log.warning("pinned_unpin_failed", message_id=pinned_id)
        await _create_and_pin(chat_id, snapshot)


async def _create_and_pin(chat_id: str, snapshot: str) -> None:
    """Create a new pinned message and store its ID in bot_state."""
    message_id = await send_message(chat_id, snapshot)
    if message_id == -1:
        # Mock mode — don't store a fake ID.
        log.info("pinned_mock_mode")
        return
    try:
        await pin_message(chat_id, message_id)
    except TelegramError:
        log.warning("pinned_pin_failed", message_id=message_id)
    set_state(_PINNED_KEY, str(message_id))
    log.info("pinned_created", message_id=message_id)
