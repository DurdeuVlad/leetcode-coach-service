"""Expiry sweep for overdue review work."""

from __future__ import annotations

import datetime
from collections import defaultdict

import structlog
from sqlmodel import select

from leetcode_coach.config import get_settings
from leetcode_coach.db.base import get_session
from leetcode_coach.db.models import PendingReview, ProposalBatch, ProposalBatchStatus, ReviewStatus
from leetcode_coach.db.state import transition_batch, transition_review
from leetcode_coach.integrations.telegram import edit_message_reply_markup

log = structlog.get_logger("flow_expiry")


async def sweep_expired(*, chat_id: str | None = None) -> int:
    """Expire due work in the shared lock order: batch, then reviews.

    Batch due dates supersede proposal dates.  The initial read is only a
    worklist; each mutable row is re-read with ``FOR UPDATE`` before its
    transition.  Empty sweeps are silent.
    """
    today = datetime.date.today()
    with next(get_session()) as session:
        worklist = session.exec(
            select(PendingReview.id, PendingReview.batch_id).where(
                PendingReview.status == ReviewStatus.OPEN
            )
        ).all()

    grouped: dict[int, list[int]] = defaultdict(list)
    legacy_ids: list[int] = []
    for review_id, batch_id in worklist:
        if batch_id is None:
            legacy_ids.append(review_id)
        else:
            grouped[batch_id].append(review_id)

    expired_threads: list[int] = []
    expired_batches: dict[int, int] = {}
    for batch_id in grouped:
        with next(get_session()) as session:
            # Keep lock order aligned with both callback and text picks.
            batch = session.exec(
                select(ProposalBatch).where(ProposalBatch.id == batch_id).with_for_update()
            ).first()
            if batch is None:
                continue
            due_date = batch.extended_until or batch.expires_at or batch.proposed_at
            if due_date > today:
                continue
            reviews = session.exec(
                select(PendingReview)
                .where(
                    PendingReview.batch_id == batch_id, PendingReview.status == ReviewStatus.OPEN
                )
                .with_for_update()
            ).all()
            for review in reviews:
                review.status = transition_review(review.status, ReviewStatus.EXPIRED)
                session.add(review)
                expired_threads.append(review.message_id)
            if reviews and batch.status in {
                ProposalBatchStatus.CREATED,
                ProposalBatchStatus.ACTIVE,
                ProposalBatchStatus.PICKED,
            }:
                batch.status = transition_batch(batch.status, ProposalBatchStatus.EXPIRED)
                session.add(batch)
                if batch.telegram_message_id is not None:
                    expired_batches[batch_id] = batch.telegram_message_id
            if reviews:
                session.commit()

    # Legacy rows have no batch/candidate state to coordinate with.
    for review_id in legacy_ids:
        with next(get_session()) as session:
            review = session.exec(
                select(PendingReview).where(PendingReview.id == review_id).with_for_update()
            ).first()
            if review is None or review.status != ReviewStatus.OPEN or review.proposed_at > today:
                continue
            review.status = transition_review(review.status, ReviewStatus.EXPIRED)
            session.add(review)
            session.commit()
            expired_threads.append(review.message_id)

    target_chat = chat_id or get_settings().telegram_chat_id
    for message_id in expired_threads:
        if message_id > 0:
            await edit_message_reply_markup(target_chat, message_id, None)
    for batch_id, message_id in expired_batches.items():
        from leetcode_coach.webhooks.callbacks import encode_callback

        await edit_message_reply_markup(
            target_chat,
            message_id,
            {
                "inline_keyboard": [
                    [
                        {
                            "text": "Extend to tomorrow",
                            "callback_data": encode_callback("extend", {"batch_id": batch_id}),
                        }
                    ]
                ]
            },
        )

    log.info("expiry_sweep_done", expired_count=len(expired_threads), date=today.isoformat())
    return len(expired_threads)
