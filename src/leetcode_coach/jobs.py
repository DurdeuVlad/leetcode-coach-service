"""Idempotent scheduled jobs for the V2 service."""

from __future__ import annotations

import datetime as dt

import sqlalchemy as sa
from sqlmodel import Session, select

from leetcode_coach.application import CoachApplication
from leetcode_coach.clock import local_today
from leetcode_coach.config import get_settings
from leetcode_coach.db.base import create_db_engine
from leetcode_coach.db.models import (
    ProposalStatus,
    ReviewStatus,
    V2BotState,
    V2FollowUp,
    V2PendingReview,
    V2Problem,
    V2ProposalBatch,
    utcnow,
)
from leetcode_coach.domain.services import CoachDomain
from leetcode_coach.integrations.leetcode import fetch_problemset, fetch_recent_solved
from leetcode_coach.integrations.telegram import edit_message, send_message

settings = get_settings()
engine = create_db_engine(settings.database_url)
FOLLOW_UP_CLAIM_LEASE = dt.timedelta(minutes=5)


async def apply_daily_tax() -> None:
    with Session(engine) as session:
        CoachDomain(session).apply_daily_tax(int(settings.telegram_chat_id), local_today())
        session.commit()


async def queue_refill() -> None:
    chat_id = int(settings.telegram_chat_id)
    await CoachApplication(engine).handle_text(
        chat_id=chat_id,
        text=(
            "Scheduled morning coaching decision: assess my current profile and work, then "
            "decide the most useful next action. A new practice set is optional."
        ),
        message_id=-local_today().toordinal(),
        reply_to_message_id=None,
    )


async def send_nudge() -> None:
    chat_id = int(settings.telegram_chat_id)
    with Session(engine) as session:
        balance = CoachDomain(session).credit_balance(chat_id)
        snoozed = session.exec(
            select(V2BotState).where(
                V2BotState.chat_id == chat_id, V2BotState.key == "nudge_snoozed_on"
            )
        ).first()
    if balance < 0 and (snoozed is None or snoozed.value != local_today().isoformat()):
        await send_message(
            chat_id,
            f"You're behind by {abs(balance)} credits.",
            reply_markup={
                "inline_keyboard": [
                    [
                        {"text": "Solve now", "callback_data": "v2n:solve"},
                        {"text": "Accept deficit", "callback_data": "v2n:accept"},
                        {"text": "Snooze", "callback_data": "v2n:snooze"},
                    ]
                ]
            },
        )


async def expire_state() -> None:
    now = utcnow()
    expired_messages: list[tuple[int, int]] = []
    with Session(engine) as session:
        batches = session.exec(
            select(V2ProposalBatch).where(
                V2ProposalBatch.status == ProposalStatus.OPEN,
                V2ProposalBatch.expires_at <= now,
            )
        ).all()
        for batch in batches:
            batch.status = ProposalStatus.EXPIRED
            if batch.telegram_message_id is not None and batch.id is not None:
                expired_messages.append((batch.id, batch.telegram_message_id))
        reviews = session.exec(
            select(V2PendingReview).where(
                V2PendingReview.status == ReviewStatus.OPEN,
                V2PendingReview.proposed_on < local_today(),
            )
        ).all()
        for review in reviews:
            review.status = ReviewStatus.EXPIRED
            review.updated_at = now
        session.commit()
    for batch_id, message_id in expired_messages:
        await edit_message(
            settings.telegram_chat_id,
            message_id,
            reply_markup={
                "inline_keyboard": [[{"text": "Extend 24h", "callback_data": f"v2x:{batch_id}"}]]
            },
        )


async def deliver_due_follow_ups() -> None:
    """Deliver due messages at least once; a post-send crash may duplicate one message."""
    now = utcnow()
    stale_before = now - FOLLOW_UP_CLAIM_LEASE
    deliverable = sa.or_(
        V2FollowUp.status == "scheduled",
        sa.and_(V2FollowUp.status == "delivering", V2FollowUp.updated_at <= stale_before),
    )
    with Session(engine) as session:
        ids = [
            row.id
            for row in session.exec(
                select(V2FollowUp)
                .where(deliverable, V2FollowUp.due_at <= now)
                .order_by(V2FollowUp.due_at)
            ).all()
        ]
    for follow_up_id in ids:
        with Session(engine) as session:
            claim = session.exec(
                sa.update(V2FollowUp)
                .where(
                    V2FollowUp.id == follow_up_id,
                    sa.or_(
                        V2FollowUp.status == "scheduled",
                        sa.and_(
                            V2FollowUp.status == "delivering",
                            V2FollowUp.updated_at <= stale_before,
                        ),
                    ),
                )
                .values(status="delivering", updated_at=now)
            )
            if claim.rowcount != 1:
                session.rollback()
                continue
            session.commit()
            row = session.get(V2FollowUp, follow_up_id)
            assert row is not None
            chat_id, message = row.chat_id, row.message
        try:
            await send_message(chat_id, message)
        except Exception as exc:
            with Session(engine) as session:
                row = session.get(V2FollowUp, follow_up_id)
                if row is not None and row.status == "delivering":
                    row.status = "scheduled"
                    row.attempt_count += 1
                    row.last_error = str(exc)[:1000]
                    row.updated_at = utcnow()
                    session.commit()
            continue
        with Session(engine) as session:
            row = session.get(V2FollowUp, follow_up_id)
            if row is not None and row.status == "delivering":
                row.status = "delivered"
                row.attempt_count += 1
                row.last_error = None
                row.delivered_at = utcnow()
                row.updated_at = row.delivered_at
                session.commit()


async def refresh_problem_pool() -> None:
    # 1. Populate the unsolved pool with canonical medium+hard problems from LeetCode.
    pool_records = await fetch_problemset()
    # 2. Mark recently-solved problems as solved in the pool.
    solved_records = await fetch_recent_solved()
    with Session(engine) as session:
        for record in pool_records:
            problem = session.get(V2Problem, record.slug)
            if problem is None:
                problem = V2Problem(
                    slug=record.slug,
                    title=record.title,
                    url=f"https://leetcode.com/problems/{record.slug}/",
                    difficulty=record.difficulty,
                    tags=record.tags,
                )
                session.add(problem)
            else:
                problem.title = record.title
                problem.url = f"https://leetcode.com/problems/{record.slug}/"
                problem.difficulty = record.difficulty
                problem.tags = record.tags
        for record in solved_records:
            problem = session.get(V2Problem, record.slug)
            if problem is None:
                problem = V2Problem(
                    slug=record.slug,
                    title=record.title,
                    url=f"https://leetcode.com/problems/{record.slug}/",
                    difficulty=record.difficulty,
                    tags=record.tags,
                )
                session.add(problem)
            else:
                problem.title = record.title
                problem.url = f"https://leetcode.com/problems/{record.slug}/"
                problem.difficulty = record.difficulty
                problem.tags = record.tags
            problem.solved = True
            problem.verified_solved = True
        session.commit()
