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


def test_preview_hydrates_durable_metadata_without_enforcing_model_mix(session):
    domain = CoachDomain(session)
    preview = domain.preview_proposal(
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
        domain.preview_proposal([ProposalSelection("two-sum"), ProposalSelection("two-sum")])
    flexible = domain.preview_proposal(
        [ProposalSelection("house-robber"), ProposalSelection("two-sum")],
        required_mix={"hard": 1, "easy": 1},
    )
    assert [item.difficulty for item in flexible.candidates] == ["easy", "easy"]


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

    attempt = domain.record_problem_attempt(
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


def test_canonical_attempt_preserves_an_explicit_historical_date(session):
    domain = CoachDomain(session)
    yesterday = dt.date.today() - dt.timedelta(days=1)

    attempt = domain.commit_canonical_attempt(
        100,
        "two-sum",
        "solved",
        operation_key="call-yesterday",
        attempted_on=yesterday,
    )

    problem = session.get(V2Problem, "two-sum")
    assert attempt.attempted_on == yesterday
    assert problem.last_attempted == yesterday


def test_historical_canonical_attempt_does_not_move_last_attempted_backward(session):
    domain = CoachDomain(session)
    problem = session.get(V2Problem, "two-sum")
    problem.last_attempted = dt.date.today()
    yesterday = dt.date.today() - dt.timedelta(days=1)

    domain.commit_canonical_attempt(
        100,
        "two-sum",
        "solved",
        operation_key="call-yesterday-after-today",
        attempted_on=yesterday,
    )

    assert problem.last_attempted == dt.date.today()


def test_canonical_attempt_rejects_future_date_without_mutation(session):
    domain = CoachDomain(session)
    tomorrow = dt.date.today() + dt.timedelta(days=1)

    with pytest.raises(DomainError, match="attempt date cannot be in the future"):
        domain.commit_canonical_attempt(
            100,
            "two-sum",
            "solved",
            operation_key="call-tomorrow",
            attempted_on=tomorrow,
        )

    assert session.get(V2Problem, "two-sum").times_attempted == 0
    assert session.exec(select(V2Attempt)).all() == []


def test_canonical_attempt_closes_oldest_matching_open_review_only(session):
    domain = CoachDomain(session)
    first = V2PendingReview(chat_id=100, problem_slug="two-sum")
    second = V2PendingReview(chat_id=100, problem_slug="two-sum")
    other = V2PendingReview(chat_id=100, problem_slug="three-sum")
    session.add_all([first, second, other])
    session.flush()

    attempt = domain.record_problem_attempt(
        100, "two-sum", "reviewed", operation_key="call-matching-review"
    )

    assert attempt.review_id == first.id
    assert first.status == ReviewStatus.DONE
    assert second.status == ReviewStatus.OPEN
    assert other.status == ReviewStatus.OPEN
    assert domain.credit_balance(100) == Decimal("0.50")


def test_correct_attempt_rescoring_replaces_prior_credit_not_adds_to_it(session):
    domain = CoachDomain(session)

    attempt = domain.record_problem_attempt(
        100, "two-sum", "reviewed", operation_key="call-initial-review"
    )
    assert domain.credit_balance(100) == Decimal("0.50")

    corrected = domain.correct_attempt(
        100,
        attempt.id,
        outcome="solved",
        attempted_on=None,
        feedback=None,
        language=None,
        solution_summary=None,
        time_spent_min=None,
        reason="rescored after dispute",
        operation_key="call-dispute-fix",
    )

    assert corrected.outcome == "solved"
    assert len(session.exec(select(V2Attempt)).all()) == 1
    assert domain.credit_balance(100) == Decimal("1.00")


def test_canonical_attempt_accepts_already_solved_and_ineligible_problem(session):
    problem = session.get(V2Problem, "house-robber")
    problem.solved = True
    problem.eligible = False
    session.flush()

    attempt = CoachDomain(session).record_problem_attempt(
        100, "house-robber", "solved", operation_key="call-repeat-solved"
    )

    assert attempt.problem_slug == "house-robber"
    assert problem.times_attempted == 1


def test_canonical_attempt_rejects_unknown_slug_and_invalid_outcome_without_mutation(session):
    domain = CoachDomain(session)
    problem = session.get(V2Problem, "two-sum")

    with pytest.raises(DomainError, match="invalid attempt outcome"):
        domain.record_problem_attempt(
            100, "two-sum", "Solved", operation_key="call-invalid-outcome"
        )
    with pytest.raises(Exception, match="problem not found"):
        domain.record_problem_attempt(
            100, "not-two-sum", "solved", operation_key="call-unknown-slug"
        )

    assert problem.times_attempted == 0
    assert session.exec(select(V2Attempt)).all() == []
    assert domain.credit_balance(100) == Decimal("0.00")


def test_canonical_attempt_operation_key_makes_replay_idempotent(session):
    domain = CoachDomain(session)
    lesson = V2Lesson(chat_id=100, title="Sliding windows", times_reinforced=1)
    session.add(lesson)
    session.flush()
    delta = {"lesson_id": lesson.id, "reinforcement_delta": 1, "status": "active"}

    first = domain.record_problem_attempt(
        100, "two-sum", "solved", lesson_delta=delta, operation_key="call-123"
    )
    replay = domain.record_problem_attempt(
        100, "two-sum", "solved", lesson_delta=delta, operation_key="call-123"
    )
    repeated_work = domain.record_problem_attempt(
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


def test_canonical_attempt_operation_key_includes_slug(session):
    session.add(
        V2Problem(
            slug="coin-change",
            title="Coin Change",
            url="https://leetcode.com/problems/coin-change/",
            difficulty=Difficulty.MEDIUM,
            tags="dp",
        )
    )
    session.flush()
    domain = CoachDomain(session)

    coin = domain.record_problem_attempt(
        100, "coin-change", "solved", operation_key="telegram-message-9"
    )
    two_sum = domain.record_problem_attempt(
        100, "two-sum", "solved", operation_key="telegram-message-9"
    )

    assert coin.problem_slug == "coin-change"
    assert two_sum.problem_slug == "two-sum"
    assert domain.credit_balance(100) == Decimal("2.00")


def test_review_attempt_operation_key_replay_is_idempotent(session):
    domain = CoachDomain(session)
    batch, _ = domain.create_proposal(100, [ProposalSelection("two-sum")])
    review = domain.commit_picks(100, batch.id, ["two-sum"])[0]

    first = domain.commit_attempt(100, review.id, "solved", operation_key="telegram-message-11")
    replay = domain.commit_attempt(100, review.id, "solved", operation_key="telegram-message-11")

    assert first.problem_slug == "two-sum"
    assert replay == {"replayed": True}
    assert len(session.exec(select(V2Attempt)).all()) == 1


def test_repeatable_ordinary_writes_use_message_operation_key(session):
    domain = CoachDomain(session)
    batch, _ = domain.create_proposal(100, [ProposalSelection("two-sum")])

    extended = domain.extend_proposal(100, batch.id, operation_key="telegram-message-21")
    extend_replay = domain.extend_proposal(100, batch.id, operation_key="telegram-message-21")
    lesson = domain.adjust_lesson(
        100,
        {"title": "Sliding windows", "category": "arrays"},
        operation_key="telegram-message-22",
    )
    lesson_replay = domain.adjust_lesson(
        100,
        {"title": "Sliding windows", "category": "arrays"},
        operation_key="telegram-message-22",
    )

    assert extended.id == batch.id
    assert extend_replay == {"replayed": True}
    assert lesson.title == "Sliding windows"
    assert lesson_replay == {"replayed": True}
    assert len(session.exec(select(V2Lesson).where(V2Lesson.chat_id == 100)).all()) == 1


def test_duplicate_update_is_safe(session):
    domain = CoachDomain(session)
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
