"""Flow C — the daily 05:05 expiry sweep.

Single responsibility (per #034): mark today's still-open `pending_review`
rows as expired and send exactly one Telegram summary message (FR-3.3).

This flow is fire-and-forget (scheduled by APScheduler at `5 5 * * *`
Europe/Bucharest, wired in `scheduling/cron.py`). It is not user-facing
and has no latency target (NFR-3). It must be idempotent: a re-run on the
same day only affects rows still `status = open` (already-expired rows are
skipped).

The caller (`_safe_sweep_expired` in cron.py) wraps the whole thing in
the #008 alert handler so an escaped exception sends exactly one Telegram
alert.
"""

from __future__ import annotations

import datetime

import structlog
from sqlmodel import select

from leetcode_coach.config import get_settings
from leetcode_coach.db.base import get_session
from leetcode_coach.db.models import PendingReview
from leetcode_coach.integrations.telegram import send_message

log = structlog.get_logger("flow_expiry")


async def sweep_expired(*, chat_id: str | None = None) -> int:
    """Mark today's open `pending_review` rows expired and send one summary.

    FR-3.1: select today's rows where `status = open`.
    FR-3.2: for each, set `status = expired`.
    FR-3.3: send exactly one Telegram summary message listing the expired
            problems (or "No problems expired today" if none).

    Args:
        chat_id: override the target chat (tests use this; production uses
            the configured TELEGRAM_CHAT_ID via send_message's allowlist
            default).

    Returns:
        The number of rows marked expired (0 means the summary still sends
        the "No problems expired today" message; useful for tests + logs).
    """
    settings = get_settings()
    target_chat = chat_id or settings.telegram_chat_id
    today = datetime.date.today()

    # 1. Select today's open rows.
    with next(get_session()) as session:
        open_rows = list(
            session.exec(
                select(PendingReview).where(
                    PendingReview.proposed_at == today,
                    PendingReview.status == "open",
                )
            ).all()
        )
        titles = [r.problem_title for r in open_rows]

    # 2. Per-row: mark expired.
    expired_count = 0
    for row in open_rows:
        with next(get_session()) as session:
            db_row = session.get(PendingReview, row.id)
            if db_row is not None and db_row.status == "open":
                db_row.status = "expired"
                session.add(db_row)
                session.commit()
                expired_count += 1

    # 3. One Telegram summary message (FR-3.3).
    if expired_count == 0:
        summary = "No problems expired today."
    else:
        bullet_list = "\n".join(f"  {i}. {t}" for i, t in enumerate(titles, start=1))
        summary = (
            f"Expired {expired_count} problem(s) without reply on {today.isoformat()}:\n"
            f"{bullet_list}"
        )
    await send_message(target_chat, summary)

    log.info("expiry_sweep_done", expired_count=expired_count, date=today.isoformat())
    return expired_count
