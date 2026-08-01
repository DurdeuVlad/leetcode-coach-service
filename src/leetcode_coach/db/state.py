"""Pure lifecycle rules used by callback and command handlers.

Persistence and row locking deliberately stay in the caller: a handler must
lock its row, call these helpers, then commit the transition atomically.
"""

from __future__ import annotations

from leetcode_coach.db.models import ProposalBatchStatus, ReviewStatus


class InvalidTransitionError(ValueError):
    """Raised when a replayed or stale interaction cannot change state."""


_REVIEW_TRANSITIONS: dict[ReviewStatus, frozenset[ReviewStatus]] = {
    ReviewStatus.OPEN: frozenset(
        {
            ReviewStatus.COACHING,
            ReviewStatus.SKIPPED,
            ReviewStatus.SAW_SOLUTION,
            ReviewStatus.EXPIRED,
        }
    ),
    ReviewStatus.COACHING: frozenset(
        {ReviewStatus.DONE, ReviewStatus.SKIPPED, ReviewStatus.SAW_SOLUTION}
    ),
    ReviewStatus.EXPIRED: frozenset({ReviewStatus.OPEN}),
    ReviewStatus.DONE: frozenset(),
    ReviewStatus.SKIPPED: frozenset(),
    ReviewStatus.SAW_SOLUTION: frozenset(),
}

_BATCH_TRANSITIONS: dict[ProposalBatchStatus, frozenset[ProposalBatchStatus]] = {
    ProposalBatchStatus.CREATED: frozenset(
        {ProposalBatchStatus.ACTIVE, ProposalBatchStatus.CANCELLED}
    ),
    ProposalBatchStatus.ACTIVE: frozenset(
        {
            ProposalBatchStatus.PICKED,
            ProposalBatchStatus.CANCELLED,
            ProposalBatchStatus.EXPIRED,
        }
    ),
    ProposalBatchStatus.PICKED: frozenset({ProposalBatchStatus.EXPIRED}),
    ProposalBatchStatus.CANCELLED: frozenset(),
    ProposalBatchStatus.EXPIRED: frozenset({ProposalBatchStatus.ACTIVE}),
}


def transition_review(current: ReviewStatus | str, target: ReviewStatus | str) -> ReviewStatus:
    """Validate and return a review status transition.

    The sole reopening route is ``expired -> open``. Terminal states cannot
    be replayed or changed by stale Telegram buttons.
    """

    source = ReviewStatus(current)
    destination = ReviewStatus(target)
    if destination not in _REVIEW_TRANSITIONS[source]:
        raise InvalidTransitionError(f"Cannot transition review from {source.value} to {destination.value}")
    return destination


def transition_batch(
    current: ProposalBatchStatus | str, target: ProposalBatchStatus | str
) -> ProposalBatchStatus:
    """Validate and return a proposal-batch state transition."""

    source = ProposalBatchStatus(current)
    destination = ProposalBatchStatus(target)
    if destination not in _BATCH_TRANSITIONS[source]:
        raise InvalidTransitionError(f"Cannot transition batch from {source.value} to {destination.value}")
    return destination
