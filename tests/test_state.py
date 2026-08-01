"""Focused tests for the persisted workflow lifecycle rules."""

from __future__ import annotations

import pytest

from leetcode_coach.db.models import ProposalBatchStatus, ReviewStatus
from leetcode_coach.db.state import InvalidTransitionError, transition_batch, transition_review


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (ReviewStatus.OPEN, ReviewStatus.COACHING),
        (ReviewStatus.OPEN, ReviewStatus.SKIPPED),
        (ReviewStatus.COACHING, ReviewStatus.DONE),
        (ReviewStatus.EXPIRED, ReviewStatus.OPEN),
    ],
)
def test_review_transition_allows_authorized_edges(source, target):
    assert transition_review(source, target) is target


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (ReviewStatus.OPEN, ReviewStatus.DONE),
        (ReviewStatus.DONE, ReviewStatus.OPEN),
        (ReviewStatus.SKIPPED, ReviewStatus.COACHING),
        (ReviewStatus.EXPIRED, ReviewStatus.DONE),
    ],
)
def test_review_transition_rejects_stale_or_terminal_edges(source, target):
    with pytest.raises(InvalidTransitionError):
        transition_review(source, target)


def test_batch_transition_rejects_cancelled_replay():
    assert transition_batch("created", "active") is ProposalBatchStatus.ACTIVE
    with pytest.raises(InvalidTransitionError):
        transition_batch(ProposalBatchStatus.CANCELLED, ProposalBatchStatus.ACTIVE)
