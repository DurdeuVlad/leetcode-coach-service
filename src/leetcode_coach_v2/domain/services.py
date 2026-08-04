from __future__ import annotations

import datetime as dt
import json
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import func
from sqlmodel import Session, select

from leetcode_coach_v2.db.models import (
    ApprovalStatus,
    CandidateStatus,
    ProposalStatus,
    ReviewStatus,
    V2Attempt,
    V2BotState,
    V2CreditLedger,
    V2Lesson,
    V2PendingApproval,
    V2PendingReview,
    V2Problem,
    V2ProcessedUpdate,
    V2ProposalBatch,
    V2ProposalCandidate,
    as_utc,
    utcnow,
)
from leetcode_coach_v2.domain.exceptions import ApprovalExpired, Conflict, DomainError, NotFound
from leetcode_coach_v2.domain.proposals import hydrate_proposal
from leetcode_coach_v2.domain.schemas import ProposalPreview, ProposalSelection


class CoachDomain:
    """Atomic business operations backing V2 agent tools.

    The Telegram/agent adapters must request approval before invoking an
    externally initiated write. These methods remain strict so a resumed run
    cannot apply stale or model-invented identifiers.
    """

    def __init__(self, session: Session):
        self.session = session

    def draft_proposal(
        self, selections: list[ProposalSelection], *, required_mix: dict[str, int] | None = None
    ) -> ProposalPreview:
        return hydrate_proposal(
            self.session,
            selections,
            required_mix=required_mix,
            expected_count=sum(required_mix.values()) if required_mix else None,
        )

    def create_proposal(
        self,
        chat_id: int,
        selections: list[ProposalSelection],
        *,
        required_mix: dict[str, int] | None = None,
        expires_at: dt.datetime | None = None,
    ) -> tuple[V2ProposalBatch, ProposalPreview]:
        preview = self.draft_proposal(selections, required_mix=required_mix)
        batch = V2ProposalBatch(
            chat_id=chat_id, expires_at=expires_at or utcnow() + dt.timedelta(hours=24)
        )
        self.session.add(batch)
        self.session.flush()
        for candidate in preview.candidates:
            self.session.add(
                V2ProposalCandidate(
                    batch_id=batch.id,
                    position=candidate.position,
                    problem_slug=candidate.slug,
                    reasoning=candidate.reasoning,
                    coaching_hint=candidate.coaching_hint,
                )
            )
        self.session.flush()
        return batch, preview

    def commit_picks(self, chat_id: int, batch_id: int, slugs: list[str]) -> list[V2PendingReview]:
        """Consume up to two available canonical candidates in one transaction."""
        if not slugs or len(slugs) > 2 or len(set(slugs)) != len(slugs):
            raise DomainError("pick one or two distinct candidate slugs")
        batch = self.session.get(V2ProposalBatch, batch_id)
        if batch is None or batch.chat_id != chat_id:
            raise NotFound("proposal batch not found")
        if batch.status != ProposalStatus.OPEN or as_utc(batch.expires_at) <= utcnow():
            raise Conflict("proposal batch is no longer open")
        candidates = self.session.exec(
            select(V2ProposalCandidate).where(
                V2ProposalCandidate.batch_id == batch_id,
                V2ProposalCandidate.problem_slug.in_(slugs),
            )
        ).all()
        if len(candidates) != len(slugs) or any(
            item.status != CandidateStatus.AVAILABLE for item in candidates
        ):
            raise Conflict("one or more requested picks are unavailable")
        existing = self.session.exec(
            select(V2PendingReview).where(
                V2PendingReview.chat_id == chat_id,
                V2PendingReview.status == ReviewStatus.OPEN,
                V2PendingReview.proposed_on == batch.proposed_on,
            )
        ).all()
        if len(existing) + len(slugs) > 2:
            raise Conflict("at most two open problems may be picked per day")
        ordered = sorted(candidates, key=lambda candidate: candidate.position)
        reviews: list[V2PendingReview] = []
        for candidate in ordered:
            candidate.status = CandidateStatus.SELECTED
            review = V2PendingReview(
                chat_id=chat_id,
                candidate_id=candidate.id,
                batch_id=batch.id,
                problem_slug=candidate.problem_slug,
                proposed_on=batch.proposed_on,
            )
            self.session.add(review)
            reviews.append(review)
        batch.status = ProposalStatus.PICKED
        self.session.flush()
        return reviews

    def commit_attempt(
        self,
        chat_id: int,
        review_id: int,
        outcome: str,
        feedback: str = "",
        lesson_delta: dict[str, Any] | None = None,
    ) -> V2Attempt:
        review = self._open_review(chat_id, review_id)
        if outcome not in {"solved", "reviewed"}:
            raise DomainError("outcome must be solved or reviewed")
        review.status = ReviewStatus.DONE
        review.updated_at = utcnow()
        problem = self.session.get(V2Problem, review.problem_slug)
        assert problem is not None
        problem.times_attempted += 1
        problem.last_attempted = dt.date.today()
        if outcome == "solved":
            problem.solved = True
        attempt = V2Attempt(
            chat_id=chat_id,
            review_id=review.id,
            problem_slug=review.problem_slug,
            outcome=outcome,
            feedback=feedback,
        )
        self.session.add(attempt)
        self.session.flush()
        self._apply_lesson_delta(chat_id, lesson_delta)
        self.add_credit(
            chat_id,
            Decimal("1.00") if outcome == "solved" else Decimal("0.50"),
            outcome,
            f"attempt:{attempt.id}",
            review.id,
        )
        return attempt

    def skip_problem(self, chat_id: int, review_id: int) -> V2PendingReview:
        review = self._open_review(chat_id, review_id)
        review.status = ReviewStatus.SKIPPED
        review.updated_at = utcnow()
        self.session.flush()
        return review

    def mark_solution_viewed(self, chat_id: int, review_id: int) -> V2PendingReview:
        review = self._open_review(chat_id, review_id)
        review.status = ReviewStatus.SAW_SOLUTION
        review.updated_at = utcnow()
        self.add_credit(
            chat_id, Decimal("0.25"), "saw_solution", f"solution:{review.id}", review.id
        )
        self.session.flush()
        return review

    def reattempt_problem(self, chat_id: int, review_id: int) -> V2PendingReview:
        source = self.session.get(V2PendingReview, review_id)
        if source is None or source.chat_id != chat_id:
            raise NotFound("review not found")
        if source.status not in {
            ReviewStatus.SKIPPED,
            ReviewStatus.SAW_SOLUTION,
            ReviewStatus.DONE,
        }:
            raise Conflict("review cannot be reattempted")
        existing = self.session.exec(
            select(V2PendingReview).where(
                V2PendingReview.chat_id == chat_id,
                V2PendingReview.problem_slug == source.problem_slug,
                V2PendingReview.status == ReviewStatus.OPEN,
                V2PendingReview.candidate_id == None,  # noqa: E711
            )
        ).first()
        if existing is not None:
            return existing
        review = V2PendingReview(chat_id=chat_id, problem_slug=source.problem_slug)
        self.session.add(review)
        self.session.flush()
        return review

    def extend_proposal(self, chat_id: int, batch_id: int, hours: int = 24) -> V2ProposalBatch:
        if not 1 <= hours <= 72:
            raise DomainError("extension must be between 1 and 72 hours")
        batch = self.session.get(V2ProposalBatch, batch_id)
        if batch is None or batch.chat_id != chat_id:
            raise NotFound("proposal batch not found")
        if batch.status not in {ProposalStatus.OPEN, ProposalStatus.EXPIRED}:
            raise Conflict("proposal batch cannot be extended")
        batch.status = ProposalStatus.OPEN
        batch.extended_until = utcnow() + dt.timedelta(hours=hours)
        batch.expires_at = batch.extended_until
        self.session.flush()
        return batch

    def add_credit(
        self,
        chat_id: int,
        amount: Decimal,
        reason: str,
        idempotency_key: str,
        review_id: int | None = None,
    ) -> V2CreditLedger:
        existing = self.session.exec(
            select(V2CreditLedger).where(V2CreditLedger.idempotency_key == idempotency_key)
        ).first()
        if existing is not None:
            return existing
        entry = V2CreditLedger(
            chat_id=chat_id,
            amount=amount,
            reason=reason,
            idempotency_key=idempotency_key,
            review_id=review_id,
        )
        self.session.add(entry)
        self.session.flush()
        return entry

    def accept_credit_deficit(self, chat_id: int, on_date: dt.date) -> V2CreditLedger:
        return self.add_credit(
            chat_id, Decimal("0.00"), "deficit_accepted", f"deficit:{chat_id}:{on_date.isoformat()}"
        )

    def apply_daily_tax(
        self, chat_id: int, on_date: dt.date, amount: Decimal = Decimal("-1.00")
    ) -> V2CreditLedger:
        return self.add_credit(chat_id, amount, "daily_tax", f"tax:{chat_id}:{on_date.isoformat()}")

    def credit_balance(self, chat_id: int) -> Decimal:
        result = self.session.exec(
            select(func.coalesce(func.sum(V2CreditLedger.amount), 0)).where(
                V2CreditLedger.chat_id == chat_id
            )
        ).one()
        return Decimal(str(result)).quantize(Decimal("0.01"))

    def adjust_lesson(self, chat_id: int, lesson_delta: dict[str, Any]) -> V2Lesson:
        return self._apply_lesson_delta(chat_id, lesson_delta)

    def record_update(self, update_id: int, chat_id: int) -> bool:
        """Claim a new or previously failed update; handled/in-flight updates are duplicates."""
        existing = self.session.get(V2ProcessedUpdate, update_id)
        if existing is not None and existing.status != "failed":
            return False
        if existing is not None:
            existing.status = "received"
            existing.error = None
            existing.handled_at = None
            existing.received_at = utcnow()
            self.session.flush()
            return True
        self.session.add(V2ProcessedUpdate(update_id=update_id, chat_id=chat_id))
        self.session.flush()
        return True

    def mark_update_handled(self, update_id: int, error: str | None = None) -> None:
        update = self.session.get(V2ProcessedUpdate, update_id)
        if update is None:
            raise NotFound("processed update not found")
        update.status = "failed" if error else "handled"
        update.error = error
        update.handled_at = utcnow()
        self.session.flush()

    def create_approval(
        self,
        chat_id: int,
        action: str,
        payload: dict[str, Any],
        summary: str,
        agent_run_id: str | None = None,
    ) -> V2PendingApproval:
        approval = V2PendingApproval(
            id=uuid.uuid4().hex,
            chat_id=chat_id,
            action=action,
            payload_json=json.dumps(payload, sort_keys=True),
            summary=summary,
            agent_run_id=agent_run_id,
        )
        self.session.add(approval)
        self.session.flush()
        return approval

    def resolve_approval(
        self, chat_id: int, approval_id: str, approved: bool, reply_to_message_id: int | None = None
    ) -> V2PendingApproval:
        approval = self.session.get(V2PendingApproval, approval_id)
        if approval is None or approval.chat_id != chat_id:
            raise NotFound("approval not found")
        if reply_to_message_id is not None and approval.approval_message_id != reply_to_message_id:
            raise Conflict("reply does not confirm this approval")
        if approval.status != ApprovalStatus.PENDING:
            raise Conflict("approval is no longer pending")
        if as_utc(approval.expires_at) <= utcnow():
            approval.status = ApprovalStatus.EXPIRED
            approval.resolved_at = utcnow()
            self.session.flush()
            raise ApprovalExpired("approval has expired")
        approval.status = ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED
        approval.resolved_at = utcnow()
        self.session.flush()
        return approval

    def resolve_text_confirmation(
        self, chat_id: int, text: str, reply_to_message_id: int | None
    ) -> V2PendingApproval | None:
        choice = text.strip().lower()
        if choice not in {"yes", "no"}:
            return None
        pending = self.session.exec(
            select(V2PendingApproval).where(
                V2PendingApproval.chat_id == chat_id,
                V2PendingApproval.status == ApprovalStatus.PENDING,
            )
        ).all()
        if reply_to_message_id is not None:
            pending = [item for item in pending if item.approval_message_id == reply_to_message_id]
        elif len(pending) != 1:
            return None
        if len(pending) != 1:
            return None
        return self.resolve_approval(chat_id, pending[0].id, choice == "yes", reply_to_message_id)

    def expire_approvals(self, now: dt.datetime | None = None) -> int:
        current = now or utcnow()
        rows = self.session.exec(
            select(V2PendingApproval).where(
                V2PendingApproval.status == ApprovalStatus.PENDING,
                V2PendingApproval.expires_at <= current,
            )
        ).all()
        for row in rows:
            row.status = ApprovalStatus.EXPIRED
            row.resolved_at = current
        self.session.flush()
        return len(rows)

    def set_bot_state(self, chat_id: int, key: str, value: str) -> V2BotState:
        state = self.session.exec(
            select(V2BotState).where(V2BotState.chat_id == chat_id, V2BotState.key == key)
        ).first()
        if state is None:
            state = V2BotState(chat_id=chat_id, key=key, value=value)
            self.session.add(state)
        else:
            state.value = value
            state.updated_at = utcnow()
        self.session.flush()
        return state

    def _open_review(self, chat_id: int, review_id: int) -> V2PendingReview:
        review = self.session.get(V2PendingReview, review_id)
        if review is None or review.chat_id != chat_id:
            raise NotFound("open review not found")
        if review.status != ReviewStatus.OPEN:
            raise Conflict("review is no longer open")
        return review

    def _apply_lesson_delta(
        self, chat_id: int, lesson_delta: dict[str, Any] | None
    ) -> V2Lesson | None:
        if not lesson_delta:
            return None
        lesson_id = lesson_delta.get("lesson_id")
        if lesson_id:
            lesson = self.session.get(V2Lesson, int(lesson_id))
            if lesson is None or lesson.chat_id != chat_id:
                raise NotFound("lesson not found")
            reinforcement = int(
                lesson_delta.get("reinforcement_delta", lesson_delta.get("reinforce", 0))
            )
            lesson.times_reinforced = max(0, lesson.times_reinforced + reinforcement)
        else:
            title = str(lesson_delta.get("title", "")).strip()
            if not title:
                raise DomainError("a new lesson delta requires title")
            lesson = V2Lesson(
                chat_id=chat_id,
                title=title,
                category=str(lesson_delta.get("category") or "general"),
            )
            self.session.add(lesson)
        graduate = lesson_delta.get("status") == "graduated" or bool(
            lesson_delta.get("graduate")
        )
        if graduate and lesson.times_reinforced >= 5:
            lesson.active = False
        lesson.updated_at = utcnow()
        self.session.flush()
        return lesson
