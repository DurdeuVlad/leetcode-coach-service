"""Transactional handlers for Telegram's inline learning controls."""

from __future__ import annotations

import datetime
import html

from sqlmodel import select

from leetcode_coach.db.base import get_session
from leetcode_coach.db.models import (
    CandidateStatus,
    DailyCandidate,
    LeetCodeLog,
    LeetCodeProblem,
    PendingReview,
    ProposalBatch,
    ProposalBatchStatus,
    ReviewStatus,
)
from leetcode_coach.db.state import InvalidTransitionError, transition_batch, transition_review
from leetcode_coach.flows.credits import award_review
from leetcode_coach.integrations.telegram import edit_message_reply_markup, send_message
from leetcode_coach.webhooks.callbacks import StaleCallbackError, encode_callback, register_callback


def _thread_keyboard(review_id: int, candidate_id: int) -> dict:
    return {"inline_keyboard": [[
        {"text": "Skip", "callback_data": encode_callback("skip", {"review_id": review_id})},
        {"text": "Hint", "callback_data": encode_callback("hint", {"review_id": review_id, "candidate_id": candidate_id})},
        {"text": "Solution", "callback_data": encode_callback("solution", {"review_id": review_id})},
    ]]}


async def handle_pick(callback, payload: dict) -> None:
    """Reserve exactly one candidate and create its review atomically."""
    batch_id, index = int(payload["batch_id"]), int(payload["pick_index"])
    with next(get_session()) as session:
        batch = session.exec(select(ProposalBatch).where(ProposalBatch.id == batch_id).with_for_update()).first()
        candidate = session.exec(
            select(DailyCandidate)
            .where(DailyCandidate.batch_id == batch_id, DailyCandidate.pick_index == index)
            .with_for_update()
        ).first()
        if batch is None or candidate is None or batch.status not in (ProposalBatchStatus.ACTIVE, ProposalBatchStatus.CREATED):
            raise StaleCallbackError()
        if candidate.status != CandidateStatus.AVAILABLE:
            raise StaleCallbackError()
        slots = session.exec(select(PendingReview).where(PendingReview.batch_id == batch_id)).all()
        if len(slots) >= 2:
            raise StaleCallbackError()
        candidate.status = CandidateStatus.SELECTED
        review = PendingReview(
            message_id=-1,
            problem_slug=candidate.slug,
            problem_title=candidate.title,
            proposed_at=datetime.date.today(),
            batch_id=batch_id,
            candidate_id=candidate.id,
            pick_slot=len(slots) + 1,
            status=ReviewStatus.OPEN,
        )
        session.add(review)
        if len(slots) + 1 == 2:
            batch.status = transition_batch(batch.status, ProposalBatchStatus.PICKED)
        elif batch.status == ProposalBatchStatus.CREATED:
            batch.status = transition_batch(batch.status, ProposalBatchStatus.ACTIVE)
        session.add_all([candidate, batch])
        session.commit()
        session.refresh(review)
        review_id, candidate_id = review.id, candidate.id
        title, url, difficulty, hint = candidate.title, candidate.url, candidate.difficulty, candidate.coaching_hint

    assert review_id is not None and candidate_id is not None
    text = (
        f'<b><a href="{html.escape(url, quote=True)}">{html.escape(title)}</a></b> '
        f'({html.escape(difficulty)})\n\n<blockquote>{html.escape(hint)}</blockquote>\n\nReply with your code.'
    )
    chat_id = str(callback.message.chat.id)
    message_id = await send_message(chat_id, text, parse_mode="HTML", reply_markup=_thread_keyboard(review_id, candidate_id))
    if message_id != -1:
        with next(get_session()) as session:
            review = session.get(PendingReview, review_id)
            if review is not None and review.message_id == -1:
                review.message_id = message_id
                session.add(review)
                session.commit()


async def _close_review(callback, payload: dict, target: ReviewStatus) -> None:
    review_id = int(payload["review_id"])
    with next(get_session()) as session:
        review = session.exec(select(PendingReview).where(PendingReview.id == review_id).with_for_update()).first()
        if review is None:
            raise StaleCallbackError()
        try:
            review.status = transition_review(review.status, target)
        except InvalidTransitionError as exc:
            raise StaleCallbackError() from exc
        problem = session.get(LeetCodeProblem, review.problem_slug)
        log = LeetCodeLog(problem_slug=review.problem_slug, status=target.value)
        session.add(log)
        session.flush()
        award_review(session, review_id=review.id, log=log, difficulty=problem.difficulty if problem else "medium")
        session.add(review)
        session.commit()
    await edit_message_reply_markup(str(callback.message.chat.id), callback.message.message_id, None)


async def handle_skip(callback, payload: dict) -> None:
    await _close_review(callback, payload, ReviewStatus.SKIPPED)


async def handle_solution(callback, payload: dict) -> None:
    await _close_review(callback, payload, ReviewStatus.SAW_SOLUTION)


async def handle_hint(callback, payload: dict) -> None:
    with next(get_session()) as session:
        review = session.exec(
            select(PendingReview)
            .where(PendingReview.id == int(payload["review_id"]))
            .with_for_update()
        ).first()
        candidate = session.exec(
            select(DailyCandidate)
            .where(DailyCandidate.id == int(payload["candidate_id"]))
            .with_for_update()
        ).first()
        if (
            review is None
            or candidate is None
            or review.candidate_id != candidate.id
            or review.status != ReviewStatus.OPEN
        ):
            raise StaleCallbackError()
        hint = candidate.coaching_hint
    await send_message(str(callback.message.chat.id), f"Hint: {hint}")


async def handle_extend(callback, payload: dict) -> None:
    """The sole explicit reopening path for an expired proposal batch."""
    batch_id = int(payload["batch_id"])
    with next(get_session()) as session:
        batch = session.exec(select(ProposalBatch).where(ProposalBatch.id == batch_id).with_for_update()).first()
        if batch is None or batch.status != ProposalBatchStatus.EXPIRED or batch.extended_until is not None:
            raise StaleCallbackError()
        batch.status = transition_batch(batch.status, ProposalBatchStatus.ACTIVE)
        batch.extended_until = datetime.date.today() + datetime.timedelta(days=1)
        reviews = session.exec(
            select(PendingReview).where(PendingReview.batch_id == batch_id, PendingReview.status == ReviewStatus.EXPIRED).with_for_update()
        ).all()
        for review in reviews:
            review.status = transition_review(review.status, ReviewStatus.OPEN)
            session.add(review)
        session.add(batch)
        session.commit()


async def handle_next(callback, payload: dict) -> None:
    with next(get_session()) as session:
        review = session.exec(
            select(PendingReview).where(PendingReview.status == ReviewStatus.OPEN).order_by(PendingReview.id)
        ).first()
    if review is not None:
        await send_message(str(callback.message.chat.id), f"Next open problem: {review.problem_title}. Reply to its thread with code.")
    else:
        raise StaleCallbackError()


async def handle_why_lesson(callback, payload: dict) -> None:
    await send_message(str(callback.message.chat.id), "That lesson was saved because the coach found a reusable pattern in this attempt.")


async def handle_reattempt(callback, payload: dict) -> None:
    """Offer a deterministic retry path without reopening a terminal review."""
    await send_message(
        str(callback.message.chat.id),
        "Re-attempt it from a fresh proposal with /propose. Closed reviews stay immutable."
    )


async def handle_cancel(callback, payload: dict) -> None:
    """Cancel an unpicked proposal batch atomically."""
    batch_id = int(payload["batch_id"])
    with next(get_session()) as session:
        batch = session.exec(
            select(ProposalBatch).where(ProposalBatch.id == batch_id).with_for_update()
        ).first()
        if batch is None or batch.status not in {ProposalBatchStatus.CREATED, ProposalBatchStatus.ACTIVE}:
            raise StaleCallbackError()
        candidates = session.exec(
            select(DailyCandidate)
            .where(DailyCandidate.batch_id == batch_id)
            .with_for_update()
        ).all()
        if any(candidate.status == CandidateStatus.SELECTED for candidate in candidates):
            raise StaleCallbackError()
        batch.status = transition_batch(batch.status, ProposalBatchStatus.CANCELLED)
        for candidate in candidates:
            candidate.status = CandidateStatus.CANCELLED
            session.add(candidate)
        session.add(batch)
        session.commit()
    await edit_message_reply_markup(str(callback.message.chat.id), callback.message.message_id, None)


def register_handlers() -> None:
    register_callback("pick", handle_pick)
    register_callback("skip", handle_skip)
    register_callback("solution", handle_solution)
    register_callback("hint", handle_hint)
    register_callback("extend", handle_extend)
    register_callback("next", handle_next)
    register_callback("why_lesson", handle_why_lesson)
    register_callback("reattempt", handle_reattempt)
    register_callback("cancel", handle_cancel)
