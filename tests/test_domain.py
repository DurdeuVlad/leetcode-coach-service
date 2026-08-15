import datetime as dt
from decimal import Decimal

import pytest
from sqlmodel import Session, create_engine, select

from leetcode_coach.db.models import (
    BaseSQLModel,
    Difficulty,
    ProposalStatus,
    ReviewStatus,
    V2Attempt,
    V2CreditLedger,
    V2Lesson,
    V2PendingReview,
    V2Problem,
    V2ProcessedUpdate,
)
from leetcode_coach.domain.exceptions import Conflict, DomainError
from leetcode_coach.domain.schemas import ProposalSelection
from leetcode_coach.domain.services import CoachDomain


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    BaseSQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        db.add_all(
            [
                V2Problem(
                    slug="house-robber",
                    title="House Robber",
                    url="https://lc/198",
                    difficulty=Difficulty.EASY,
                    tags="dp",
                ),
                V2Problem(
                    slug="two-sum",
                    title="Two Sum",
                    url="https://lc/1",
                    difficulty=Difficulty.EASY,
                    tags="array",
                ),
                V2Problem(
                    slug="three-sum",
                    title="3Sum",
                    url="https://lc/15",
                    difficulty=Difficulty.MEDIUM,
                    tags="array",
                ),
            ]
        )
        db.commit()
        yield db


def test_draft_hydrates_canonical_metadata_and_rejects_wrong_house_robber_difficulty(session):
    domain = CoachDomain(session)
    preview = domain.draft_proposal(
        [
            ProposalSelection("house-robber", "Model called it hard", "start at index 0"),
            ProposalSelection("three-sum"),
        ],
        required_mix={"easy": 1, "medium": 1},
    )

    assert preview.candidates[0].title == "House Robber"
    assert preview.candidates[0].difficulty == "easy"
    assert preview.candidates[0].url == "https://lc/198"
    with pytest.raises(DomainError, match="duplicate"):
        domain.draft_proposal([ProposalSelection("two-sum"), ProposalSelection("two-sum")])
    with pytest.raises(DomainError, match="wrong proposal difficulty mix"):
        domain.draft_proposal(
            [ProposalSelection("house-robber"), ProposalSelection("two-sum")],
            required_mix={"hard": 1, "easy": 1},
        )


def test_picks_attempts_credits_and_idempotency(session):
    domain = CoachDomain(session)
    batch, _ = domain.create_proposal(
        100, [ProposalSelection("house-robber"), ProposalSelection("three-sum")]
    )
    reviews = domain.commit_picks(100, batch.id, ["house-robber"])
    attempt = domain.commit_attempt(
        100, reviews[0].id, "solved", "good", {"title": "state transition"}
    )
    again = domain.add_credit(100, Decimal("1"), "solved", f"attempt:{attempt.id}")

    assert again.id is not None
    assert domain.credit_balance(100) == Decimal("1.00")
    assert session.get(V2Problem, "house-robber").solved is True
    with pytest.raises(Conflict):
        domain.commit_picks(100, batch.id, ["three-sum"])


def test_empty_credit_balance_has_stable_two_decimal_representation(session):
    assert str(CoachDomain(session).credit_balance(999)) == "0.00"


def test_canonical_attempt_without_review_rewards_real_work(session):
    domain = CoachDomain(session)

    attempt = domain.commit_canonical_attempt(
        100,
        "two-sum",
        "solved",
        "Correct sliding window",
        {"title": "Track the window boundary", "category": "arrays"},
        operation_key="call-no-review",
    )

    problem = session.get(V2Problem, "two-sum")
    assert attempt.review_id is None
    assert attempt.problem_slug == "two-sum"
    assert problem.times_attempted == 1
    assert problem.solved is True
    assert domain.credit_balance(100) == Decimal("1.00")


def test_canonical_attempt_closes_oldest_matching_open_review_only(session):
    domain = CoachDomain(session)
    first = V2PendingReview(chat_id=100, problem_slug="two-sum")
    second = V2PendingReview(chat_id=100, problem_slug="two-sum")
    other = V2PendingReview(chat_id=100, problem_slug="three-sum")
    session.add_all([first, second, other])
    session.flush()

    attempt = domain.commit_canonical_attempt(
        100, "two-sum", "reviewed", operation_key="call-matching-review"
    )

    assert attempt.review_id == first.id
    assert first.status == ReviewStatus.DONE
    assert second.status == ReviewStatus.OPEN
    assert other.status == ReviewStatus.OPEN
    assert domain.credit_balance(100) == Decimal("0.50")


def test_canonical_attempt_accepts_already_solved_and_ineligible_problem(session):
    problem = session.get(V2Problem, "house-robber")
    problem.solved = True
    problem.eligible = False
    session.flush()

    attempt = CoachDomain(session).commit_canonical_attempt(
        100, "house-robber", "solved", operation_key="call-repeat-solved"
    )

    assert attempt.problem_slug == "house-robber"
    assert problem.times_attempted == 1


def test_canonical_attempt_rejects_unknown_slug_and_invalid_outcome_without_mutation(session):
    domain = CoachDomain(session)
    problem = session.get(V2Problem, "two-sum")

    with pytest.raises(DomainError, match="outcome must be solved or reviewed"):
        domain.commit_canonical_attempt(
            100, "two-sum", "Solved", operation_key="call-invalid-outcome"
        )
    with pytest.raises(Exception, match="canonical problem not found"):
        domain.commit_canonical_attempt(
            100, "not-two-sum", "solved", operation_key="call-unknown-slug"
        )

    assert problem.times_attempted == 0
    assert session.exec(select(V2Attempt)).all() == []
    assert domain.credit_balance(100) == Decimal("0.00")


def test_canonical_attempt_operation_key_makes_approval_replay_idempotent(session):
    domain = CoachDomain(session)
    lesson = V2Lesson(chat_id=100, title="Sliding windows", times_reinforced=1)
    session.add(lesson)
    session.flush()
    delta = {"lesson_id": lesson.id, "reinforcement_delta": 1, "status": "active"}

    first = domain.commit_canonical_attempt(
        100, "two-sum", "solved", lesson_delta=delta, operation_key="call-123"
    )
    replay = domain.commit_canonical_attempt(
        100, "two-sum", "solved", lesson_delta=delta, operation_key="call-123"
    )
    repeated_work = domain.commit_canonical_attempt(
        100, "two-sum", "solved", lesson_delta=delta, operation_key="call-456"
    )

    assert first.id is not None
    assert replay == {"replayed": True}
    assert repeated_work.id != first.id
    assert len(session.exec(select(V2Attempt)).all()) == 2
    assert len(session.exec(select(V2CreditLedger)).all()) == 2
    assert session.get(V2Problem, "two-sum").times_attempted == 2
    assert lesson.times_reinforced == 3
    assert domain.credit_balance(100) == Decimal("2.00")


def test_confirmation_requires_matching_reply_or_exactly_one_pending(session):
    domain = CoachDomain(session)
    first = domain.create_approval(100, "skip_problem", {"review_id": 1}, "Skip it")
    assert domain.resolve_text_confirmation(100, "yes", None).id == first.id
    second = domain.create_approval(100, "skip_problem", {"review_id": 2}, "Skip it")
    third = domain.create_approval(100, "skip_problem", {"review_id": 3}, "Skip it")
    assert domain.resolve_text_confirmation(100, "yes", None) is None
    assert domain.resolve_text_confirmation(100, "yes", 999) is None
    second.approval_message_id = 55
    session.flush()
    assert domain.resolve_text_confirmation(100, "no", 55).id == second.id
    assert third.status.value == "pending"


def test_expired_approval_and_duplicate_update_are_safe(session):
    domain = CoachDomain(session)
    approval = domain.create_approval(100, "pick", {}, "Pick")
    approval.expires_at = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=1)
    session.flush()
    assert domain.expire_approvals() == 1
    assert domain.record_update(123, 100) is True
    assert domain.record_update(123, 100) is False


def test_failed_update_can_retry_but_handled_update_cannot(session):
    domain = CoachDomain(session)
    assert domain.record_update(200, 100) is True
    domain.mark_update_handled(200, "telegram unavailable")
    assert domain.record_update(200, 100) is True
    domain.mark_update_handled(200)
    assert domain.record_update(200, 100) is False


def test_inflight_update_retries_with_lease_and_recovers_after_crash(session):
    domain = CoachDomain(session)
    assert domain.record_update(201, 100) is True
    assert domain.processed_update_status(201) == "received"
    assert domain.record_update(201, 100) is False

    row = session.get(V2ProcessedUpdate, 201)
    row.received_at = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=16)
    session.flush()

    assert domain.record_update(201, 100) is True
    assert row.received_at > dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1)


def test_lesson_delta_matches_agent_schema_and_keeps_double_gate(session):
    domain = CoachDomain(session)
    lesson = V2Lesson(chat_id=100, title="State transitions", times_reinforced=3, active=True)
    session.add(lesson)
    session.flush()

    domain.adjust_lesson(
        100,
        {"lesson_id": lesson.id, "reinforcement_delta": 1, "status": "graduated"},
    )
    assert lesson.times_reinforced == 4
    assert lesson.active is True

    domain.adjust_lesson(
        100,
        {"lesson_id": lesson.id, "reinforcement_delta": 1, "status": "graduated"},
    )
    assert lesson.times_reinforced == 5
    assert lesson.active is False

    created = domain.adjust_lesson(
        100,
        {"title": "Boundary checks", "category": "correctness", "status": "active"},
    )
    assert created.title == "Boundary checks"
    assert created.category == "correctness"


def test_review_lifecycle_skip_solution_reattempt_extend_and_tax(session):
    domain = CoachDomain(session)
    batch, _ = domain.create_proposal(
        100, [ProposalSelection("house-robber"), ProposalSelection("three-sum")]
    )
    reviews = domain.commit_picks(100, batch.id, ["house-robber", "three-sum"])
    skipped = domain.skip_problem(100, reviews[0].id)
    viewed = domain.mark_solution_viewed(100, reviews[1].id)
    reattempt = domain.reattempt_problem(100, skipped.id)
    domain.commit_attempt(
        100,
        reattempt.id,
        "solved",
        "Good recovery",
        {"title": "Re-check transitions", "category": "dp"},
    )
    batch.status = ProposalStatus.EXPIRED
    extended = domain.extend_proposal(100, batch.id)
    domain.apply_daily_tax(100, dt.date.today())
    domain.apply_daily_tax(100, dt.date.today())

    assert skipped.status.value == "skipped"
    assert viewed.status.value == "saw_solution"
    assert reattempt.status.value == "done"
    assert extended.status.value == "open"
    assert extended.extended_until is not None
    assert domain.credit_balance(100) == Decimal("0.25")


def test_reloaded_naive_expiry_timestamp_does_not_break_pick(session):
    domain = CoachDomain(session)
    batch, _ = domain.create_proposal(100, [ProposalSelection("house-robber")])
    session.commit()
    session.expire_all()

    reviews = domain.commit_picks(100, batch.id, ["house-robber"])

    assert len(reviews) == 1
