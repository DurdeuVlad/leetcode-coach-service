"""Idempotent scheduled jobs for the V2 service."""

from __future__ import annotations

import datetime as dt

from sqlmodel import Session, func, select

from leetcode_coach.application import CoachApplication
from leetcode_coach.config import get_settings
from leetcode_coach.db.base import create_db_engine
from leetcode_coach.db.models import (
    ApprovalStatus,
    ProposalStatus,
    ReviewStatus,
    V2BotState,
    V2PendingApproval,
    V2PendingReview,
    V2Problem,
    V2ProposalBatch,
    utcnow,
)
from leetcode_coach.domain.services import CoachDomain
from leetcode_coach.integrations.leetcode import fetch_recent_solved
from leetcode_coach.integrations.telegram import edit_message, send_message

settings = get_settings()
engine = create_db_engine(settings.database_url)


async def apply_daily_tax() -> None:
    with Session(engine) as session:
        CoachDomain(session).apply_daily_tax(int(settings.telegram_chat_id), dt.date.today())
        session.commit()


async def queue_refill() -> None:
    chat_id = int(settings.telegram_chat_id)
    with Session(engine) as session:
        count = session.exec(
            select(func.count(V2PendingReview.id)).where(
                V2PendingReview.chat_id == chat_id,
                V2PendingReview.status == ReviewStatus.OPEN,
            )
        ).one()
    if count < 3:
        await CoachApplication(engine).handle_text(
            chat_id=chat_id,
            text=(
                "Scheduled morning check: use my learning profile and canonical problem pool "
                "to draft exactly five candidates (2-3 medium and 2-3 hard)."
            ),
            message_id=0,
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
    if balance < 0 and (snoozed is None or snoozed.value != dt.date.today().isoformat()):
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
                V2PendingReview.proposed_on < dt.date.today(),
            )
        ).all()
        for review in reviews:
            review.status = ReviewStatus.EXPIRED
            review.updated_at = now
        approvals = session.exec(
            select(V2PendingApproval).where(
                V2PendingApproval.status == ApprovalStatus.PENDING,
                V2PendingApproval.expires_at <= now,
            )
        ).all()
        for approval in approvals:
            approval.status = ApprovalStatus.EXPIRED
            approval.resolved_at = now
        session.commit()
    for batch_id, message_id in expired_messages:
        await edit_message(
            settings.telegram_chat_id,
            message_id,
            reply_markup={
                "inline_keyboard": [[{"text": "Extend 24h", "callback_data": f"v2x:{batch_id}"}]]
            },
        )


async def refresh_problem_pool() -> None:
    records = await fetch_recent_solved()
    with Session(engine) as session:
        for record in records:
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
        session.commit()
