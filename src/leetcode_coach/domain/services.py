from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import func
from sqlmodel import Session, select

from leetcode_coach.clock import local_today
from leetcode_coach.db.models import (
    CandidateStatus,
    ProposalStatus,
    ReviewStatus,
    V2Attempt,
    V2AttemptRevision,
    V2BotState,
    V2CreditLedger,
    V2FollowUp,
    V2Lesson,
    V2PendingReview,
    V2Problem,
    V2ProcessedUpdate,
    V2ProposalBatch,
    V2ProposalCandidate,
    as_utc,
    utcnow,
)
from leetcode_coach.domain.exceptions import Conflict, DomainError, NotFound
from leetcode_coach.domain.proposals import hydrate_proposal
from leetcode_coach.domain.schemas import ProposalPreview, ProposalSelection


class CoachDomain:
    """Atomic business operations backing V2 agent tools."""

    def __init__(self, session: Session):
        self.session = session

    def preview_proposal(
        self, selections: list[ProposalSelection], *, required_mix: dict[str, int] | None = None
    ) -> ProposalPreview:
        return hydrate_proposal(
            self.session,
            selections,
            required_mix=required_mix,
        )

    def create_proposal(
        self,
        chat_id: int,
        selections: list[ProposalSelection],
        *,
        required_mix: dict[str, int] | None = None,
        expires_at: dt.datetime | None = None,
    ) -> tuple[V2ProposalBatch, ProposalPreview]:
        preview = self.preview_proposal(selections, required_mix=required_mix)
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
        """Consume explicitly selected candidates in one transaction."""
        if not slugs or len(set(slugs)) != len(slugs):
            raise DomainError("pick one or more distinct candidate slugs")
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
        language: str | None = None,
        solution_summary: str = "",
        time_spent_min: int | None = None,
        *,
        operation_key: str | None = None,
    ) -> V2Attempt | dict[str, bool]:
        self._validate_outcome(outcome)
        credit_key = None
        if operation_key is not None:
            credit_key = f"review_attempt:{chat_id}:{review_id}:{operation_key}"
            if not operation_key or len(credit_key) > 200:
                raise DomainError("attempt operation key is invalid")
            existing = self.session.exec(
                select(V2CreditLedger).where(V2CreditLedger.idempotency_key == credit_key)
            ).first()
            if existing is not None:
                return {"replayed": True}
        review = self._open_review(chat_id, review_id)
        problem = self.session.get(V2Problem, review.problem_slug)
        assert problem is not None
        return self._persist_attempt(
            chat_id,
            problem,
            outcome,
            feedback,
            lesson_delta,
            review=review,
            credit_idempotency_key=credit_key,
            language=language,
            solution_summary=solution_summary,
            time_spent_min=time_spent_min,
        )

    def record_problem_attempt(
        self,
        chat_id: int,
        problem_slug: str,
        outcome: str,
        feedback: str = "",
        lesson_delta: dict[str, Any] | None = None,
        *,
        operation_key: str,
        attempted_on: dt.date | None = None,
        language: str | None = None,
        solution_summary: str = "",
        time_spent_min: int | None = None,
    ) -> V2Attempt | dict[str, bool]:
        """Persist user-reported work for an exact normalized problem identity."""
        self._validate_outcome(outcome)
        problem = self.session.get(V2Problem, problem_slug)
        if problem is None:
            raise NotFound("problem not found")
        effective_on = attempted_on or local_today()
        if effective_on > local_today():
            raise DomainError("attempted_on cannot be in the future")
        idempotency_key = f"canonical_attempt:{chat_id}:{problem_slug}:{operation_key}"
        if not operation_key or len(idempotency_key) > 200:
            raise DomainError("problem attempt operation key is invalid")
        existing = self.session.exec(
            select(V2CreditLedger).where(V2CreditLedger.idempotency_key == idempotency_key)
        ).first()
        if existing is not None:
            return {"replayed": True}
        review = self.session.exec(
            select(V2PendingReview)
            .where(
                V2PendingReview.chat_id == chat_id,
                V2PendingReview.problem_slug == problem_slug,
                V2PendingReview.status == ReviewStatus.OPEN,
            )
            .order_by(V2PendingReview.id)
        ).first()
        return self._persist_attempt(
            chat_id,
            problem,
            outcome,
            feedback,
            lesson_delta,
            review=review,
            credit_idempotency_key=idempotency_key,
            attempted_on=effective_on,
            language=language,
            solution_summary=solution_summary,
            time_spent_min=time_spent_min,
        )

    def _persist_attempt(
        self,
        chat_id: int,
        problem: V2Problem,
        outcome: str,
        feedback: str,
        lesson_delta: dict[str, Any] | None,
        *,
        review: V2PendingReview | None,
        credit_idempotency_key: str | None = None,
        attempted_on: dt.date | None = None,
        language: str | None = None,
        solution_summary: str = "",
        time_spent_min: int | None = None,
    ) -> V2Attempt:
        """Apply the shared attempt, lesson, and credit transaction."""
        if review is not None and outcome != "attempted":
            review.status = self._review_status(outcome)
            review.updated_at = utcnow()
        effective_on = attempted_on or local_today()
        problem.times_attempted += 1
        if problem.last_attempted is None or effective_on > problem.last_attempted:
            problem.last_attempted = effective_on
        if outcome == "solved":
            problem.solved = True
        attempt = V2Attempt(
            chat_id=chat_id,
            review_id=review.id if review is not None else None,
            problem_slug=problem.slug,
            outcome=outcome,
            feedback=feedback,
            attempted_on=effective_on,
            language=language,
            solution_summary=solution_summary,
            time_spent_min=time_spent_min,
        )
        self.session.add(attempt)
        self.session.flush()
        self._apply_lesson_delta(chat_id, lesson_delta)
        credit = self.add_credit(
            chat_id,
            self._outcome_credit(outcome),
            outcome,
            credit_idempotency_key or f"attempt:{attempt.id}",
            review.id if review is not None else None,
            effective_on=effective_on,
        )
        credit.attempt_id = attempt.id
        return attempt

    @staticmethod
    def _validate_outcome(outcome: str) -> None:
        if outcome not in {"solved", "reviewed", "saw_solution", "attempted", "skipped"}:
            raise DomainError("invalid attempt outcome")

    @staticmethod
    def _outcome_credit(outcome: str) -> Decimal:
        return {
            "solved": Decimal("1.00"),
            "reviewed": Decimal("0.50"),
            "saw_solution": Decimal("0.25"),
            "attempted": Decimal("0.00"),
            "skipped": Decimal("0.00"),
        }[outcome]

    def correct_attempt(
        self,
        chat_id: int,
        attempt_id: int,
        *,
        outcome: str | None,
        attempted_on: dt.date | None,
        feedback: str | None,
        language: str | None,
        clear_language: bool = False,
        solution_summary: str | None,
        time_spent_min: int | None,
        clear_time_spent: bool = False,
        reason: str,
        operation_key: str,
    ) -> V2Attempt | dict[str, bool]:
        if outcome is not None:
            self._validate_outcome(outcome)
        if attempted_on is not None and attempted_on > local_today():
            raise DomainError("attempted_on cannot be in the future")
        if clear_language and language is not None:
            raise DomainError("language cannot be set and cleared together")
        if clear_time_spent and time_spent_min is not None:
            raise DomainError("time_spent_min cannot be set and cleared together")
        revision_key = f"attempt_revision:{chat_id}:{attempt_id}:{operation_key}"
        if (
            self.session.exec(
                select(V2AttemptRevision).where(V2AttemptRevision.operation_key == revision_key)
            ).first()
            is not None
        ):
            return {"replayed": True}
        attempt = self.session.get(V2Attempt, attempt_id)
        if attempt is None or attempt.chat_id != chat_id or attempt.reversed_at is not None:
            raise NotFound("active attempt not found")
        before = self._attempt_snapshot(attempt)
        old_outcome = attempt.outcome
        old_date = attempt.attempted_on
        old_credit = self._outcome_credit(old_outcome)
        if outcome is not None:
            attempt.outcome = outcome
        if attempted_on is not None:
            attempt.attempted_on = attempted_on
        if feedback is not None:
            attempt.feedback = feedback
        if clear_language:
            attempt.language = None
        elif language is not None:
            attempt.language = language
        if solution_summary is not None:
            attempt.solution_summary = solution_summary
        if clear_time_spent:
            attempt.time_spent_min = None
        elif time_spent_min is not None:
            attempt.time_spent_min = time_spent_min
        attempt.updated_at = utcnow()
        if attempt.review_id is not None:
            review = self.session.get(V2PendingReview, attempt.review_id)
            if review is not None:
                review.status = self._review_status(attempt.outcome)
                review.updated_at = attempt.updated_at
        self.add_credit(
            chat_id,
            -old_credit,
            "attempt_correction_remove",
            f"attempt_correction_remove:{chat_id}:{attempt_id}:{operation_key}",
            attempt.review_id,
            effective_on=old_date,
            attempt_id=attempt.id,
        )
        self.add_credit(
            chat_id,
            self._outcome_credit(attempt.outcome),
            "attempt_correction_replace",
            f"attempt_correction_replace:{chat_id}:{attempt_id}:{operation_key}",
            attempt.review_id,
            effective_on=attempt.attempted_on,
            attempt_id=attempt.id,
        )
        self.session.add(
            V2AttemptRevision(
                chat_id=chat_id,
                attempt_id=attempt_id,
                operation_key=revision_key,
                action="correct",
                before_json=json.dumps(before, sort_keys=True),
                after_json=json.dumps(self._attempt_snapshot(attempt), sort_keys=True),
                reason=reason,
            )
        )
        self._recompute_problem(attempt.problem_slug)
        self.session.flush()
        return attempt

    def reverse_attempt(
        self, chat_id: int, attempt_id: int, *, reason: str, operation_key: str
    ) -> V2Attempt | dict[str, bool]:
        revision_key = f"attempt_revision:{chat_id}:{attempt_id}:{operation_key}"
        if (
            self.session.exec(
                select(V2AttemptRevision).where(V2AttemptRevision.operation_key == revision_key)
            ).first()
            is not None
        ):
            return {"replayed": True}
        attempt = self.session.get(V2Attempt, attempt_id)
        if attempt is None or attempt.chat_id != chat_id:
            raise NotFound("attempt not found")
        if attempt.reversed_at is not None:
            raise Conflict("attempt is already reversed")
        before = self._attempt_snapshot(attempt)
        attempt.reversed_at = utcnow()
        attempt.updated_at = attempt.reversed_at
        if attempt.review_id is not None:
            review = self.session.get(V2PendingReview, attempt.review_id)
            if review is not None:
                review.status = ReviewStatus.OPEN
                review.updated_at = attempt.updated_at
        self.add_credit(
            chat_id,
            -self._outcome_credit(attempt.outcome),
            "attempt_reversal",
            f"attempt_reversal_credit:{chat_id}:{attempt_id}:{operation_key}",
            attempt.review_id,
            effective_on=attempt.attempted_on,
            attempt_id=attempt.id,
        )
        self.session.add(
            V2AttemptRevision(
                chat_id=chat_id,
                attempt_id=attempt_id,
                operation_key=revision_key,
                action="reverse",
                before_json=json.dumps(before, sort_keys=True),
                after_json=json.dumps(self._attempt_snapshot(attempt), sort_keys=True),
                reason=reason,
            )
        )
        self._recompute_problem(attempt.problem_slug)
        self.session.flush()
        return attempt

    @staticmethod
    def _attempt_snapshot(attempt: V2Attempt) -> dict[str, Any]:
        return {
            "outcome": attempt.outcome,
            "attempted_on": attempt.attempted_on.isoformat(),
            "feedback": attempt.feedback,
            "language": attempt.language,
            "solution_summary": attempt.solution_summary,
            "time_spent_min": attempt.time_spent_min,
            "reversed_at": attempt.reversed_at.isoformat() if attempt.reversed_at else None,
        }

    def _recompute_problem(self, slug: str) -> None:
        problem = self.session.get(V2Problem, slug)
        assert problem is not None
        attempts = self.session.exec(
            select(V2Attempt).where(
                V2Attempt.problem_slug == slug,
                V2Attempt.reversed_at == None,  # noqa: E711
            )
        ).all()
        problem.times_attempted = problem.attempt_baseline_count + len(attempts)
        dates = [row.attempted_on for row in attempts]
        if problem.attempt_baseline_last is not None:
            dates.append(problem.attempt_baseline_last)
        problem.last_attempted = max(dates) if dates else None
        problem.solved = problem.verified_solved or any(row.outcome == "solved" for row in attempts)
        problem.updated_at = utcnow()

    @staticmethod
    def _review_status(outcome: str) -> ReviewStatus:
        return {
            "solved": ReviewStatus.DONE,
            "reviewed": ReviewStatus.DONE,
            "saw_solution": ReviewStatus.SAW_SOLUTION,
            "attempted": ReviewStatus.OPEN,
            "skipped": ReviewStatus.SKIPPED,
        }[outcome]

    def skip_problem(self, chat_id: int, review_id: int) -> V2PendingReview:
        review = self._open_review(chat_id, review_id)
        problem = self.session.get(V2Problem, review.problem_slug)
        assert problem is not None
        self._persist_attempt(
            chat_id,
            problem,
            "skipped",
            "",
            None,
            review=review,
            credit_idempotency_key=f"skip:{review.id}",
        )
        return review

    def mark_solution_viewed(self, chat_id: int, review_id: int) -> V2PendingReview:
        review = self._open_review(chat_id, review_id)
        problem = self.session.get(V2Problem, review.problem_slug)
        assert problem is not None
        self._persist_attempt(
            chat_id,
            problem,
            "saw_solution",
            "",
            None,
            review=review,
            credit_idempotency_key=f"solution:{review.id}",
        )
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

    def extend_proposal(
        self,
        chat_id: int,
        batch_id: int,
        hours: int = 24,
        *,
        operation_key: str | None = None,
    ) -> V2ProposalBatch | dict[str, bool]:
        if not 1 <= hours <= 72:
            raise DomainError("extension must be between 1 and 72 hours")
        if self._operation_replayed(chat_id, "extend", str(batch_id), operation_key):
            return {"replayed": True}
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
        *,
        effective_on: dt.date | None = None,
        attempt_id: int | None = None,
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
            attempt_id=attempt_id,
            effective_on=effective_on or local_today(),
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

    def adjust_lesson(
        self,
        chat_id: int,
        lesson_delta: dict[str, Any],
        *,
        operation_key: str | None = None,
    ) -> V2Lesson | dict[str, bool]:
        identity = str(lesson_delta.get("lesson_id") or lesson_delta.get("title") or "new")
        if self._operation_replayed(chat_id, "lesson", identity, operation_key):
            return {"replayed": True}
        return self._apply_lesson_delta(chat_id, lesson_delta)

    def _operation_replayed(
        self,
        chat_id: int,
        action: str,
        identity: str,
        operation_key: str | None,
    ) -> bool:
        """Claim one repeatable message-scoped mutation without a schema change."""
        if operation_key is None:
            return False
        if not operation_key:
            raise DomainError("operation key is invalid")
        digest = hashlib.sha256(f"{action}:{identity}:{operation_key}".encode()).hexdigest()
        key = f"operation:{digest}"
        existing = self.session.exec(
            select(V2BotState).where(V2BotState.chat_id == chat_id, V2BotState.key == key)
        ).first()
        if existing is not None:
            return True
        self.session.add(V2BotState(chat_id=chat_id, key=key, value=action))
        self.session.flush()
        return False

    def record_update(self, update_id: int, chat_id: int) -> bool:
        """Claim a new, failed, or abandoned update.

        A recent ``received`` row is still being handled and must not run twice.
        An old row is reclaimable after a worker crash so Telegram retries cannot
        strand an update forever.
        """
        existing = self.session.get(V2ProcessedUpdate, update_id)
        reclaim_before = utcnow() - dt.timedelta(minutes=15)
        if existing is not None:
            reclaimable = existing.status == "failed" or (
                existing.status == "received" and as_utc(existing.received_at) <= reclaim_before
            )
            if not reclaimable:
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

    def processed_update_status(self, update_id: int) -> str | None:
        update = self.session.get(V2ProcessedUpdate, update_id)
        return update.status if update is not None else None

    def mark_update_handled(self, update_id: int, error: str | None = None) -> None:
        update = self.session.get(V2ProcessedUpdate, update_id)
        if update is None:
            raise NotFound("processed update not found")
        update.status = "failed" if error else "handled"
        update.error = error
        update.handled_at = utcnow()
        self.session.flush()

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

    def schedule_follow_up(
        self, chat_id: int, due_at: dt.datetime, message: str, operation_key: str
    ) -> V2FollowUp:
        identity = hashlib.sha256(f"{due_at.isoformat()}:{message}".encode()).hexdigest()[:20]
        key = f"follow_up:{chat_id}:{identity}:{operation_key}"
        existing = self.session.exec(
            select(V2FollowUp).where(V2FollowUp.idempotency_key == key)
        ).first()
        if existing is not None:
            return existing
        row = V2FollowUp(
            id=uuid.uuid4().hex,
            chat_id=chat_id,
            due_at=due_at,
            message=message,
            idempotency_key=key,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def cancel_follow_up(self, chat_id: int, follow_up_id: str, operation_key: str) -> V2FollowUp:
        row = self.session.get(V2FollowUp, follow_up_id)
        if row is None or row.chat_id != chat_id:
            raise NotFound("follow-up not found")
        if self._operation_replayed(chat_id, "cancel_follow_up", follow_up_id, operation_key):
            return row
        if row.status in {"delivered", "delivering"}:
            raise Conflict(f"{row.status} follow-up cannot be cancelled")
        row.status = "cancelled"
        row.cancelled_at = utcnow()
        row.updated_at = row.cancelled_at
        self.session.flush()
        return row

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
        graduate = lesson_delta.get("status") == "graduated" or bool(lesson_delta.get("graduate"))
        if graduate and lesson.times_reinforced >= 5:
            lesson.active = False
        lesson.updated_at = utcnow()
        self.session.flush()
        return lesson
