import asyncio
import datetime as dt
from collections import defaultdict
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine, select

from leetcode_coach.agent.orchestrator import AgentRunOutcome, AgentSettings
from leetcode_coach.application import CoachApplication
from leetcode_coach.db.base import BaseSQLModel
from leetcode_coach.db.models import (
    Difficulty,
    ProposalStatus,
    V2Attempt,
    V2BotState,
    V2CreditLedger,
    V2PendingApproval,
    V2PendingReview,
    V2Problem,
    V2ProposalBatch,
)
from leetcode_coach.domain.exceptions import Conflict
from leetcode_coach.runtime.adapters import (
    PostgresAgentSession,
    SQLCoachDomainAdapter,
)


@pytest.fixture
def v2_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    BaseSQLModel.metadata.create_all(engine)
    return engine


def _problem(slug: str, difficulty: str) -> V2Problem:
    return V2Problem(
        slug=slug,
        title=slug.replace("-", " ").title(),
        url=f"https://leetcode.com/problems/{slug}/",
        difficulty=Difficulty(difficulty),
        tags="array",
    )


@pytest.mark.asyncio
async def test_empty_registry_records_two_yesterday_solves_and_proposes_one_hard(
    v2_engine, monkeypatch
):
    adapter = SQLCoachDomainAdapter(v2_engine)
    yesterday = dt.date.today() - dt.timedelta(days=1)
    lookup_calls = []

    async def unavailable_lookup(value):
        lookup_calls.append(value)
        raise RuntimeError("Browserless unavailable")

    monkeypatch.setattr("leetcode_coach.runtime.adapters.fetch_exact_problem", unavailable_lookup)

    for slug, title, difficulty, tags in (
        (
            "longest-substring-without-repeating-characters",
            "Longest Substring Without Repeating Characters",
            "medium",
            "hash-table,sliding-window",
        ),
        ("coin-change", "Coin Change", "medium", "dynamic-programming"),
    ):
        await adapter.record_problem_attempt(
            chat_id=1,
            problem_slug=slug,
            title=title,
            difficulty=difficulty,
            tags=tags,
            outcome="solved",
            feedback="valid core approach",
            lesson_delta={},
            attempted_on=yesterday.isoformat(),
            operation_key="transcript-message",
        )

    proposal = await adapter.publish_practice_set(
        chat_id=1,
        operation_key="proposal-transcript",
        selections=[
            {
                "slug": "minimum-window-substring",
                "title": "Minimum Window Substring",
                "difficulty": "hard",
                "tags": "hash-table,string,sliding-window",
                "reasoning": "Builds on the demonstrated sliding-window pattern.",
                "coaching_hint": "Track when the window satisfies every required count.",
            }
        ],
    )

    assert [row["slug"] for row in proposal["candidates"]] == ["minimum-window-substring"]
    assert lookup_calls == []
    with Session(v2_engine) as session:
        attempts = session.exec(select(V2Attempt).order_by(V2Attempt.id)).all()
        assert [row.attempted_on for row in attempts] == [yesterday, yesterday]
        ledgers = session.exec(select(V2CreditLedger).order_by(V2CreditLedger.id)).all()
        assert [row.effective_on for row in ledgers] == [yesterday, yesterday]
        assert session.get(V2Problem, "longest-substring-without-repeating-characters").solved
        assert session.get(V2Problem, "minimum-window-substring").difficulty == Difficulty.HARD


@pytest.mark.asyncio
async def test_record_problem_attempt_rejects_future_date_without_mutation(v2_engine):
    future = dt.date.today() + dt.timedelta(days=1)

    with pytest.raises(Exception, match="future"):
        await SQLCoachDomainAdapter(v2_engine).record_problem_attempt(
            chat_id=1,
            problem_slug="coin-change",
            title="Coin Change",
            difficulty="medium",
            tags="dynamic-programming",
            outcome="solved",
            feedback="",
            lesson_delta={},
            attempted_on=future.isoformat(),
            operation_key="future-message",
        )

    with Session(v2_engine) as session:
        assert session.exec(select(V2Attempt)).all() == []
        assert session.get(V2Problem, "coin-change") is None


@pytest.mark.asyncio
async def test_record_problem_attempt_rejects_oversized_title_atomically(v2_engine):
    with pytest.raises(ValueError, match="title"):
        await SQLCoachDomainAdapter(v2_engine).record_problem_attempt(
            chat_id=1,
            problem_slug="coin-change",
            title="T" * 301,
            difficulty="medium",
            tags="dp",
            outcome="solved",
            feedback="",
            lesson_delta={},
            operation_key="oversized-title",
        )
    with Session(v2_engine) as session:
        assert session.get(V2Problem, "coin-change") is None
        assert session.exec(select(V2Attempt)).all() == []
        assert session.exec(select(V2CreditLedger)).all() == []


@pytest.mark.asyncio
async def test_backdated_record_preserves_existing_problem_state_and_latest_attempt_date(v2_engine):
    with Session(v2_engine) as session:
        problem = _problem("coin-change", "medium")
        problem.solved = True
        problem.eligible = True
        problem.times_attempted = 4
        problem.last_attempted = dt.date.today()
        session.add(problem)
        session.commit()

    yesterday = dt.date.today() - dt.timedelta(days=1)
    result = await SQLCoachDomainAdapter(v2_engine).record_problem_attempt(
        chat_id=1,
        problem_slug="https://leetcode.com/problems/coin-change/description/",
        title="Wrong Replacement Title",
        difficulty="hard",
        tags="wrong-tag",
        outcome="reviewed",
        feedback="revisited",
        lesson_delta={},
        attempted_on=yesterday.isoformat(),
        operation_key="backdated-message",
    )

    assert result["problem_slug"] == "coin-change"
    with Session(v2_engine) as session:
        problem = session.get(V2Problem, "coin-change")
        assert (problem.title, problem.difficulty, problem.tags) == (
            "Coin Change",
            Difficulty.MEDIUM,
            "array",
        )
        assert problem.solved is True
        assert problem.eligible is True
        assert problem.times_attempted == 5
        assert problem.last_attempted == dt.date.today()


@pytest.mark.asyncio
async def test_flexible_proposal_and_picks_have_no_behavioral_count_caps(v2_engine):
    with Session(v2_engine) as session:
        session.add_all([_problem(f"problem-{index}", "easy") for index in range(1, 7)])
        session.commit()
    adapter = SQLCoachDomainAdapter(v2_engine)
    slugs = [f"problem-{index}" for index in range(1, 7)]

    proposal = await adapter.publish_practice_set(
        chat_id=1,
        operation_key="proposal-flexible",
        selections=[
            {
                "slug": slug,
                "title": slug.title(),
                "difficulty": "easy",
                "tags": "array",
                "reasoning": "practice",
                "coaching_hint": "think",
            }
            for slug in slugs
        ],
    )
    reviews = await adapter.commit_picks(chat_id=1, batch_id=str(proposal["batch_id"]), slugs=slugs)

    assert len(proposal["candidates"]) == 6
    assert len(reviews) == 6


@pytest.mark.asyncio
async def test_proposal_transport_accepts_20_and_rejects_21_atomically(v2_engine):
    adapter = SQLCoachDomainAdapter(v2_engine)
    selections = [
        {
            "slug": f"transport-{index}",
            "title": f"Transport {index}",
            "difficulty": "medium",
            "tags": "array",
            "reasoning": "practice",
            "coaching_hint": "think",
        }
        for index in range(1, 22)
    ]
    accepted = await adapter.publish_practice_set(
        chat_id=1, selections=selections[:20], operation_key="proposal-20"
    )
    assert len(accepted["candidates"]) == 20
    with pytest.raises(ValueError, match="at most 20"):
        await adapter.publish_practice_set(
            chat_id=2, selections=selections, operation_key="proposal-21"
        )
    with Session(v2_engine) as session:
        assert len(session.exec(select(V2Problem)).all()) == 20
        assert len(session.exec(select(V2ProposalBatch)).all()) == 1


def test_application_context_uses_initial_telegram_message_as_operation_key() -> None:
    application = CoachApplication.__new__(CoachApplication)
    application.domain = object()
    application.advisor = object()

    context = application._context(7, operation_key="telegram-message-91")

    assert context.chat_id == 7
    assert context.operation_key == "telegram-message-91"


@pytest.mark.asyncio
async def test_draft_uses_canonical_difficulty_for_house_robber(v2_engine):
    with Session(v2_engine) as session:
        session.add_all(
            [
                _problem("house-robber", "easy"),
                _problem("medium-a", "medium"),
                _problem("medium-b", "medium"),
                _problem("hard-a", "hard"),
                _problem("hard-b", "hard"),
            ]
        )
        session.commit()
    adapter = SQLCoachDomainAdapter(v2_engine)
    selections = [
        {"slug": slug, "reasoning": "reason", "coaching_hint": "hint"}
        for slug in ["house-robber", "medium-a", "medium-b", "hard-a", "hard-b"]
    ]
    result = await adapter.publish_practice_set(
        chat_id=1, selections=selections, operation_key="proposal-house-robber"
    )
    house_robber = next(item for item in result["candidates"] if item["slug"] == "house-robber")
    assert house_robber["difficulty"] == "easy"


@pytest.mark.asyncio
async def test_draft_accepts_agent_supplied_identity_for_missing_problem(v2_engine):
    adapter = SQLCoachDomainAdapter(v2_engine)
    selections = [
        {
            "slug": "minimum-window-substring",
            "title": "Minimum Window Substring",
            "difficulty": "hard",
            "tags": "sliding-window",
            "reasoning": "x",
            "coaching_hint": "y",
        }
    ]
    result = await adapter.publish_practice_set(
        chat_id=1, selections=selections, operation_key="proposal-missing"
    )
    assert result["candidates"][0]["url"] == (
        "https://leetcode.com/problems/minimum-window-substring/"
    )


@pytest.mark.asyncio
async def test_canonical_attempt_adapter_returns_attempt_and_rolls_back_failures(v2_engine):
    with Session(v2_engine) as session:
        session.add(_problem("two-sum", "easy"))
        session.add_all(
            [
                V2PendingReview(chat_id=1, problem_slug="two-sum"),
                V2PendingReview(chat_id=1, problem_slug="two-sum"),
            ]
        )
        session.commit()
    adapter = SQLCoachDomainAdapter(v2_engine)
    assert (await adapter.get_open_queue(chat_id=1))["reviews"]

    result = await adapter.record_problem_attempt(
        chat_id=1,
        problem_slug="two-sum",
        outcome="solved",
        feedback="passed",
        lesson_delta={},
        operation_key="call-1",
    )
    with pytest.raises(Exception, match="new lesson delta requires title"):
        await adapter.record_problem_attempt(
            chat_id=1,
            problem_slug="two-sum",
            outcome="solved",
            feedback="",
            lesson_delta={"category": "arrays"},
            operation_key="call-2",
        )

    assert result["problem_slug"] == "two-sum"
    with Session(v2_engine) as session:
        attempts = session.exec(select(V2Attempt).order_by(V2Attempt.id)).all()
        assert [attempt.problem_slug for attempt in attempts] == ["two-sum"]
        reviews = session.exec(select(V2PendingReview).order_by(V2PendingReview.id)).all()
        assert result["review_id"] == reviews[0].id
        assert [getattr(review.status, "value", review.status) for review in reviews] == [
            "done",
            "open",
        ]
        assert session.get(V2Problem, "two-sum").times_attempted == 1


@pytest.mark.asyncio
async def test_empty_queue_canonical_attempt_adapter_replay_is_a_no_op(v2_engine):
    with Session(v2_engine) as session:
        session.add(_problem("longest-substring-without-repeating-characters", "medium"))
        session.commit()
    adapter = SQLCoachDomainAdapter(v2_engine)

    assert (await adapter.get_open_queue(chat_id=1))["reviews"] == []
    assert (
        await adapter.get_problem(chat_id=1, slug="longest-substring-without-repeating-characters")
    )["slug"] == "longest-substring-without-repeating-characters"
    first = await adapter.record_problem_attempt(
        chat_id=1,
        problem_slug="longest-substring-without-repeating-characters",
        outcome="solved",
        feedback="sliding window passed",
        lesson_delta={},
        operation_key="approved-call-1",
    )
    replay = await adapter.record_problem_attempt(
        chat_id=1,
        problem_slug="longest-substring-without-repeating-characters",
        outcome="solved",
        feedback="sliding window passed",
        lesson_delta={},
        operation_key="approved-call-1",
    )

    assert first["review_id"] is None
    assert replay["replayed"] is True
    with Session(v2_engine) as session:
        assert len(session.exec(select(V2Attempt)).all()) == 1
        assert (
            session.get(V2Problem, "longest-substring-without-repeating-characters").times_attempted
            == 1
        )


@pytest.mark.asyncio
async def test_queue_less_solved_attempt_returns_authoritative_receipt(v2_engine):
    with Session(v2_engine) as session:
        session.add(_problem("coin-change", "medium"))
        session.commit()

    result = await SQLCoachDomainAdapter(v2_engine).record_problem_attempt(
        chat_id=1,
        problem_slug="coin-change",
        outcome="solved",
        feedback="passed",
        lesson_delta={},
        operation_key="message-1",
    )

    assert result["receipt"] == {
        "title": "Coin Change",
        "result": "Solved",
        "credit": "+1.00",
        "balance": "0.00 → 1.00",
        "path": "Direct attempt (no queue needed)",
        "replayed": False,
    }


@pytest.mark.asyncio
async def test_matched_reviewed_attempt_returns_open_queue_receipt(v2_engine):
    with Session(v2_engine) as session:
        session.add(_problem("two-sum", "easy"))
        review = V2PendingReview(chat_id=1, problem_slug="two-sum")
        session.add(review)
        session.commit()
        review_id = review.id

    result = await SQLCoachDomainAdapter(v2_engine).commit_attempt(
        chat_id=1,
        review_id=str(review_id),
        outcome="reviewed",
        feedback="off by one",
        lesson_delta={},
        operation_key="message-2",
    )

    assert result["receipt"] == {
        "title": "Two Sum",
        "result": "Reviewed",
        "credit": "+0.50",
        "balance": "0.00 → 0.50",
        "path": "Open queue",
        "replayed": False,
    }


@pytest.mark.asyncio
async def test_attempt_replay_reports_no_credit_at_current_balance(v2_engine):
    with Session(v2_engine) as session:
        session.add(_problem("coin-change", "medium"))
        session.commit()
    adapter = SQLCoachDomainAdapter(v2_engine)
    await adapter.record_problem_attempt(
        chat_id=1,
        problem_slug="coin-change",
        outcome="solved",
        feedback="passed",
        lesson_delta={},
        operation_key="message-3",
    )
    with Session(v2_engine) as session:
        session.add(
            V2CreditLedger(
                chat_id=1,
                amount=Decimal("-0.25"),
                reason="test_adjustment",
                idempotency_key="unrelated-balance-change",
            )
        )
        session.commit()

    replay = await adapter.record_problem_attempt(
        chat_id=1,
        problem_slug="coin-change",
        outcome="reviewed",
        feedback="different replay payload",
        lesson_delta={},
        operation_key="message-3",
    )

    assert replay["receipt"] == {
        "title": "Coin Change",
        "result": "Solved",
        "credit": "+0.00",
        "balance": "0.75 → 0.75",
        "path": "Direct attempt (no queue needed)",
        "replayed": True,
    }


@pytest.mark.asyncio
async def test_get_problem_read_through_caches_only_exact_server_metadata(v2_engine, monkeypatch):
    calls = []

    async def fake_fetch(value):
        calls.append(value)
        from leetcode_coach.integrations.leetcode import ProblemRecord

        return ProblemRecord("coin-change", "Coin Change", "medium", "dynamic-programming")

    monkeypatch.setattr("leetcode_coach.runtime.adapters.fetch_exact_problem", fake_fetch)
    adapter = SQLCoachDomainAdapter(v2_engine)

    first = await adapter.get_problem(
        chat_id=1, slug="https://leetcode.com/problems/coin-change/description/"
    )
    second = await adapter.get_problem(chat_id=1, slug="coin-change")

    assert first == second
    assert first["url"] == "https://leetcode.com/problems/coin-change/"
    assert first["solved"] is False
    assert first["eligible"] is False
    assert calls == ["https://leetcode.com/problems/coin-change/description/"]
    assert (
        await adapter.search_problem_catalog(
            chat_id=1, mode="eligible_unsolved", filters={}, limit=20
        )
        == []
    )


@pytest.mark.asyncio
async def test_get_problem_recovers_when_concurrent_insert_wins(v2_engine, monkeypatch):
    from sqlalchemy.exc import IntegrityError

    async def fake_fetch(value):
        del value
        from leetcode_coach.integrations.leetcode import ProblemRecord

        return ProblemRecord("coin-change", "Coin Change", "medium", "dp")

    adapter = SQLCoachDomainAdapter(v2_engine)
    original_write = adapter._write

    async def race_write(operation):
        del operation
        with Session(v2_engine) as session:
            problem = _problem("coin-change", "medium")
            problem.eligible = False
            session.add(problem)
            session.commit()
        raise IntegrityError("concurrent canonical cache insert", {}, Exception("unique"))

    monkeypatch.setattr("leetcode_coach.runtime.adapters.fetch_exact_problem", fake_fetch)
    monkeypatch.setattr(adapter, "_write", race_write)

    result = await adapter.get_problem(chat_id=1, slug="coin-change")

    assert result["slug"] == "coin-change"
    assert result["eligible"] is False
    monkeypatch.setattr(adapter, "_write", original_write)


@pytest.mark.asyncio
async def test_direct_attempt_replay_uses_message_and_problem_identity(v2_engine):
    with Session(v2_engine) as session:
        session.add_all([_problem("coin-change", "medium"), _problem("two-sum", "easy")])
        session.commit()
    adapter = SQLCoachDomainAdapter(v2_engine)

    first = await adapter.record_problem_attempt(
        chat_id=1,
        problem_slug="coin-change",
        outcome="solved",
        feedback="passed",
        lesson_delta={},
        operation_key="telegram-message-42",
    )
    replay = await adapter.record_problem_attempt(
        chat_id=1,
        problem_slug="coin-change",
        outcome="solved",
        feedback="passed",
        lesson_delta={},
        operation_key="telegram-message-42",
    )
    second_problem = await adapter.record_problem_attempt(
        chat_id=1,
        problem_slug="two-sum",
        outcome="solved",
        feedback="passed",
        lesson_delta={},
        operation_key="telegram-message-42",
    )

    assert first["problem_slug"] == "coin-change"
    assert replay["replayed"] is True
    assert second_problem["problem_slug"] == "two-sum"
    with Session(v2_engine) as session:
        assert len(session.exec(select(V2Attempt)).all()) == 2


@pytest.mark.asyncio
async def test_problem_pool_honors_typed_filter_shape(v2_engine):
    with Session(v2_engine) as session:
        session.add_all(
            [
                V2Problem(
                    slug="graph-medium",
                    title="Graph Medium",
                    url="https://lc/graph-medium",
                    difficulty=Difficulty.MEDIUM,
                    tags="graph,bfs",
                ),
                V2Problem(
                    slug="array-hard",
                    title="Array Hard",
                    url="https://lc/array-hard",
                    difficulty=Difficulty.HARD,
                    tags="array,sorting",
                ),
            ]
        )
        session.commit()
    adapter = SQLCoachDomainAdapter(v2_engine)
    rows = await adapter.search_problem_catalog(
        chat_id=1,
        mode="eligible_unsolved",
        filters={
            "difficulty": ["medium", "hard"],
            "include_tags": ["graph"],
            "exclude_tags": ["sorting"],
            "topic": "bfs",
        },
        limit=10,
    )
    assert [row["slug"] for row in rows] == ["graph-medium"]


@pytest.mark.asyncio
async def test_learning_profile_bounds_attempt_feedback(v2_engine):
    with Session(v2_engine) as session:
        session.add(_problem("medium-a", "medium"))
        session.add(
            V2Attempt(
                chat_id=1,
                problem_slug="medium-a",
                outcome="solved",
                feedback="x" * 4_000,
            )
        )
        session.commit()

    result = await SQLCoachDomainAdapter(v2_engine).get_learning_profile(chat_id=1)
    assert len(result["recent_attempts"][0]["feedback"]) == 500
    assert result["recent_attempts"][0]["feedback"].endswith("…")


@pytest.mark.asyncio
async def test_unsent_proposal_does_not_preempt_explicit_solve_message(v2_engine):
    class FakeRepository:
        async def pending_rows(self, chat_id):
            return []

        async def abandon(self, *, chat_id):
            return None

    class FakeRunner:
        def __init__(self):
            self.messages = []

        async def run(self, **kwargs):
            self.messages.append(kwargs["message"])
            return AgentRunOutcome("completed", "credited", {})

    application = CoachApplication.__new__(CoachApplication)
    application.engine = v2_engine
    application.domain = object()
    application.advisor = object()
    application.repository = FakeRepository()
    application.runner = FakeRunner()
    application._locks = defaultdict(asyncio.Lock)
    proposal_checks = []

    async def fake_unsent(chat_id):
        proposal_checks.append(chat_id)
        return True

    async def fake_deliver(*args, **kwargs):
        return None

    application._send_unsent_proposal = fake_unsent
    application._deliver_outcome = fake_deliver

    await application.handle_text(
        chat_id=1,
        text="I solved coin-change; record it",
        message_id=12,
        reply_to_message_id=None,
    )

    assert application.runner.messages == ["I solved coin-change; record it"]
    assert proposal_checks == []


@pytest.mark.asyncio
async def test_stale_unsent_proposal_does_not_hide_fresh_result(v2_engine, monkeypatch):
    from leetcode_coach import application as application_module

    sent = []

    async def fake_send(chat_id, text, **kwargs):
        sent.append((text, kwargs))
        return 1

    application = CoachApplication.__new__(CoachApplication)
    application.engine = v2_engine
    application.repository = SimpleNamespace()
    application._persist_metrics = lambda *args: None
    application._send_unsent_proposal = lambda chat_id: _async_value(True)
    application._send_unsent_reviews = lambda chat_id: _async_value(None)
    monkeypatch.setattr(application_module, "send_message", fake_send)

    await application._deliver_outcome(
        1,
        AgentRunOutcome("completed", "Both solved attempts were recorded.", {}),
        reply_to_message_id=12,
    )

    assert [item[0] for item in sent] == ["Both solved attempts were recorded."]


@pytest.mark.asyncio
async def test_receipts_are_delivered_in_attempt_order_before_coaching(v2_engine, monkeypatch):
    from leetcode_coach import application as application_module

    sent = []

    async def fake_send(chat_id, text, **kwargs):
        sent.append((text, kwargs))
        return len(sent)

    application = CoachApplication.__new__(CoachApplication)
    application.engine = v2_engine
    application.repository = SimpleNamespace()
    application._persist_metrics = lambda *args: None
    application._send_unsent_proposal = lambda chat_id: _async_value(False)
    application._send_unsent_reviews = lambda chat_id: _async_value(None)
    monkeypatch.setattr(application_module, "send_message", fake_send)
    receipts = [
        {
            "title": "Coin Change",
            "result": "Solved",
            "credit": "+1.00",
            "balance": "0.00 → 1.00",
            "path": "Direct attempt (no queue needed)",
            "replayed": False,
        },
        {
            "title": "Two Sum",
            "result": "Reviewed",
            "credit": "+0.50",
            "balance": "1.00 → 1.50",
            "path": "Open queue",
            "replayed": False,
        },
    ]

    await application._deliver_outcome(
        1,
        AgentRunOutcome("completed", "Try a boundary-case test next.", {}, receipts=receipts),
        reply_to_message_id=12,
    )

    assert [message for message, _ in sent] == [
        "Your work counts\n\nProblem: Coin Change\nResult: Solved\nCredit: +1.00\n"
        "Balance: 0.00 → 1.00\nPath: Direct attempt (no queue needed)",
        "Your work counts\n\nProblem: Two Sum\nResult: Reviewed\nCredit: +0.50\n"
        "Balance: 1.00 → 1.50\nPath: Open queue",
        "Try a boundary-case test next.",
    ]
    assert sent[0][0].splitlines()[0] != "Recorded"
    assert all(kwargs == {"reply_to_message_id": 12} for _, kwargs in sent)


@pytest.mark.asyncio
async def test_replayed_receipt_says_already_recorded_and_no_credit(v2_engine, monkeypatch):
    from leetcode_coach import application as application_module

    sent = []

    async def fake_send(chat_id, text, **kwargs):
        sent.append(text)
        return 1

    application = CoachApplication.__new__(CoachApplication)
    application.engine = v2_engine
    application.repository = SimpleNamespace()
    application._persist_metrics = lambda *args: None
    application._send_unsent_proposal = lambda chat_id: _async_value(False)
    application._send_unsent_reviews = lambda chat_id: _async_value(None)
    monkeypatch.setattr(application_module, "send_message", fake_send)

    await application._deliver_outcome(
        1,
        AgentRunOutcome(
            "completed",
            None,
            {},
            receipts=[
                {
                    "title": "Coin Change",
                    "result": "Solved",
                    "credit": "+0.00",
                    "balance": "0.75 → 0.75",
                    "path": "Direct attempt (no queue needed)",
                    "replayed": True,
                }
            ],
        ),
        reply_to_message_id=12,
    )

    assert sent == [
        "Already recorded\n\nProblem: Coin Change\nResult: Solved\nCredit: +0.00\n"
        "Balance: 0.75 → 0.75\nPath: Direct attempt (no queue needed)"
    ]


async def _async_value(value):
    return value


@pytest.mark.asyncio
async def test_one_message_records_two_canonical_attempts_once_without_approval_ui(
    v2_engine, monkeypatch
):
    from leetcode_coach import application as application_module

    with Session(v2_engine) as session:
        session.add_all([_problem("coin-change", "medium"), _problem("two-sum", "easy")])
        session.commit()

    class FakeRunner:
        async def run(self, **kwargs):
            context = kwargs["context"]
            for slug in ("coin-change", "two-sum", "coin-change", "two-sum"):
                await context.domain.record_problem_attempt(
                    chat_id=context.chat_id,
                    problem_slug=slug,
                    outcome="solved",
                    feedback="passed",
                    lesson_delta={},
                    operation_key=context.operation_key,
                )
            return AgentRunOutcome("completed", "Recorded both solved problems.", {})

    sent = []

    async def fake_send(chat_id, text, **kwargs):
        sent.append((text, kwargs))
        return len(sent)

    monkeypatch.setattr(application_module, "send_message", fake_send)
    application = CoachApplication.__new__(CoachApplication)
    application.engine = v2_engine
    application.domain = SQLCoachDomainAdapter(v2_engine)
    application.advisor = object()
    application.runner = FakeRunner()
    application.agent_settings = AgentSettings()
    application._locks = defaultdict(asyncio.Lock)

    await application.handle_text(
        chat_id=1,
        text="I solved coin-change and two-sum; record both",
        message_id=44,
        reply_to_message_id=None,
    )

    with Session(v2_engine) as session:
        attempts = session.exec(select(V2Attempt).order_by(V2Attempt.id)).all()
        assert [row.problem_slug for row in attempts] == ["coin-change", "two-sum"]
        assert session.exec(select(V2PendingApproval)).all() == []
    assert sent == [("Recorded both solved problems.", {"reply_to_message_id": 44})]


@pytest.mark.asyncio
async def test_stale_review_callback_is_acknowledged_without_retry(v2_engine, monkeypatch):
    from leetcode_coach import application as application_module

    class FakeDomain:
        async def skip_problem(self, **kwargs):
            raise Conflict("stale")

    acknowledgements = []
    sent = []

    async def fake_ack(callback_id, text=None):
        acknowledgements.append((callback_id, text))

    async def fake_send(chat_id, text, **kwargs):
        sent.append(text)
        return 1

    monkeypatch.setattr(application_module, "answer_callback", fake_ack)
    monkeypatch.setattr(application_module, "send_message", fake_send)
    application = CoachApplication.__new__(CoachApplication)
    application.engine = v2_engine
    application.domain = FakeDomain()
    application._locks = defaultdict(asyncio.Lock)

    await application.handle_callback(chat_id=1, callback_id="old", data="v2r:skip:999")

    assert acknowledgements == [("old", None)]
    assert sent == ["That review action is no longer active."]


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [1, 3])
async def test_proposal_callbacks_toggle_flexible_picks_then_done_once(
    v2_engine, monkeypatch, count
):
    from leetcode_coach import application as application_module
    from leetcode_coach.domain.schemas import ProposalSelection
    from leetcode_coach.domain.services import CoachDomain

    with Session(v2_engine) as session:
        session.add_all([_problem(f"pick-{index}", "medium") for index in range(1, count + 1)])
        session.commit()
        batch, _ = CoachDomain(session).create_proposal(
            1, [ProposalSelection(f"pick-{index}") for index in range(1, count + 1)]
        )
        session.commit()
        batch_id = batch.id

    sent = []
    acknowledged = []

    async def fake_send(chat_id, text, **kwargs):
        sent.append(text)
        return len(sent)

    async def fake_ack(callback_id, text=None):
        acknowledged.append((callback_id, text))

    monkeypatch.setattr(application_module, "send_message", fake_send)
    monkeypatch.setattr(application_module, "answer_callback", fake_ack)
    application = CoachApplication.__new__(CoachApplication)
    application.engine = v2_engine
    application.domain = SQLCoachDomainAdapter(v2_engine)
    application._locks = defaultdict(asyncio.Lock)

    for position in range(1, count + 1):
        await application.handle_callback(
            chat_id=1,
            callback_id=f"pick-{position}",
            data=f"v2p:{batch_id}:{position}",
        )
    await application.handle_callback(chat_id=1, callback_id="done", data=f"v2pd:{batch_id}")
    sent_after_done = list(sent)
    await application.handle_callback(chat_id=1, callback_id="done-replay", data=f"v2pd:{batch_id}")

    with Session(v2_engine) as session:
        reviews = session.exec(select(V2PendingReview).order_by(V2PendingReview.id)).all()
        assert len(reviews) == count
        assert (
            session.exec(
                select(V2BotState).where(
                    V2BotState.chat_id == 1, V2BotState.key == f"pick:{batch_id}"
                )
            ).first()
            is None
        )
    assert sum("Selected" in message for message in sent) == count
    assert sent == sent_after_done
    assert len(acknowledged) == count + 2


@pytest.mark.asyncio
async def test_pick_callback_replay_after_send_failure_does_not_toggle_twice(
    v2_engine, monkeypatch
):
    from leetcode_coach import application as application_module
    from leetcode_coach.domain.schemas import ProposalSelection
    from leetcode_coach.domain.services import CoachDomain

    with Session(v2_engine) as session:
        session.add(_problem("replay-pick", "medium"))
        session.commit()
        batch, _ = CoachDomain(session).create_proposal(1, [ProposalSelection("replay-pick")])
        session.commit()
        batch_id = batch.id

    attempts = 0

    async def flaky_send(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("send failed after mutation")
        return attempts

    async def fake_ack(*args, **kwargs):
        return None

    monkeypatch.setattr(application_module, "send_message", flaky_send)
    monkeypatch.setattr(application_module, "answer_callback", fake_ack)
    application = CoachApplication.__new__(CoachApplication)
    application.engine = v2_engine
    application.domain = SQLCoachDomainAdapter(v2_engine)
    application._locks = defaultdict(asyncio.Lock)

    with pytest.raises(RuntimeError, match="send failed"):
        await application.handle_callback(
            chat_id=1, callback_id="same-callback", data=f"v2p:{batch_id}:1"
        )
    await application.handle_callback(
        chat_id=1, callback_id="same-callback", data=f"v2p:{batch_id}:1"
    )
    await application.handle_callback(chat_id=1, callback_id="done", data=f"v2pd:{batch_id}")

    with Session(v2_engine) as session:
        assert len(session.exec(select(V2PendingReview)).all()) == 1


@pytest.mark.asyncio
async def test_done_replay_delivers_only_missing_review_threads(v2_engine, monkeypatch):
    from leetcode_coach import application as application_module
    from leetcode_coach.domain.schemas import ProposalSelection
    from leetcode_coach.domain.services import CoachDomain

    with Session(v2_engine) as session:
        session.add_all([_problem(f"resume-{index}", "hard") for index in range(1, 4)])
        session.commit()
        batch, _ = CoachDomain(session).create_proposal(
            1, [ProposalSelection(f"resume-{index}") for index in range(1, 4)]
        )
        session.commit()
        batch_id = batch.id

    delivered = []
    review_attempts = 0

    async def flaky_send(chat_id, text, **kwargs):
        nonlocal review_attempts
        if "Reply with your code" in text:
            review_attempts += 1
            if review_attempts == 2:
                raise RuntimeError("second review failed")
            delivered.append(text.splitlines()[0])
        return review_attempts + 10

    async def fake_ack(*args, **kwargs):
        return None

    monkeypatch.setattr(application_module, "send_message", flaky_send)
    monkeypatch.setattr(application_module, "answer_callback", fake_ack)
    application = CoachApplication.__new__(CoachApplication)
    application.engine = v2_engine
    application.domain = SQLCoachDomainAdapter(v2_engine)
    application._locks = defaultdict(asyncio.Lock)
    for position in range(1, 4):
        await application.handle_callback(
            chat_id=1, callback_id=f"toggle-{position}", data=f"v2p:{batch_id}:{position}"
        )

    with pytest.raises(RuntimeError, match="second review failed"):
        await application.handle_callback(
            chat_id=1, callback_id="done-first", data=f"v2pd:{batch_id}"
        )
    await application.handle_callback(chat_id=1, callback_id="done-retry", data=f"v2pd:{batch_id}")

    assert sorted(delivered) == ["Resume 1", "Resume 2", "Resume 3"]


@pytest.mark.asyncio
async def test_unsent_large_proposal_delivers_telegram_safe_pages(v2_engine, monkeypatch):
    from leetcode_coach import application as application_module
    from leetcode_coach.domain.schemas import ProposalSelection
    from leetcode_coach.domain.services import CoachDomain
    from leetcode_coach.integrations.telegram import _message_text

    with Session(v2_engine) as session:
        problems = [_problem(f"large-{index}", "hard") for index in range(1, 13)]
        for problem in problems:
            problem.title += " T" * 100
            problem.tags = "tag" * 100
        session.add_all(problems)
        session.commit()
        batch, _ = CoachDomain(session).create_proposal(
            1,
            [
                ProposalSelection(
                    f"large-{index}",
                    "R" * 500,
                    "H" * 500,
                )
                for index in range(1, 13)
            ],
        )
        session.commit()
        batch_id = batch.id

    sent = []
    send_attempts = 0
    fail_once = True

    async def fake_send(chat_id, text, **kwargs):
        nonlocal send_attempts, fail_once
        send_attempts += 1
        assert _message_text(text, kwargs.get("parse_mode")) == text
        if send_attempts == 2 and fail_once:
            fail_once = False
            raise RuntimeError("transient page delivery failure")
        sent.append((text, kwargs))
        return len(sent)

    monkeypatch.setattr(application_module, "send_message", fake_send)
    application = CoachApplication.__new__(CoachApplication)
    application.engine = v2_engine

    with pytest.raises(RuntimeError, match="transient page delivery failure"):
        await application._send_unsent_proposal(1)
    assert await application._send_unsent_proposal(1) is True
    assert len(sent) > 1
    assert len({text for text, _ in sent}) == len(sent)
    controller = sent[-1]
    informational = sent[:-1]
    assert all("reply_markup" not in kwargs for _, kwargs in informational)
    positions = [
        int(button["text"].removeprefix("Pick "))
        for _, kwargs in [controller]
        for row in kwargs["reply_markup"]["inline_keyboard"]
        for button in row
        if button["callback_data"].startswith("v2p:")
    ]
    assert positions == list(range(1, 13))
    assert any(
        button["callback_data"] == f"v2pd:{batch_id}"
        for button in controller[1]["reply_markup"]["inline_keyboard"][-1]
    )
    with Session(v2_engine) as session:
        assert session.get(V2ProposalBatch, batch_id).telegram_message_id == len(sent)
        assert (
            session.exec(
                select(V2BotState).where(
                    V2BotState.chat_id == 1,
                    V2BotState.key == f"proposal_delivery:{batch_id}",
                )
            ).first()
            is None
        )


@pytest.mark.asyncio
async def test_newer_closed_unsent_batch_does_not_poison_older_open_delivery(
    v2_engine, monkeypatch
):
    from leetcode_coach import application as application_module
    from leetcode_coach.domain.schemas import ProposalSelection
    from leetcode_coach.domain.services import CoachDomain

    with Session(v2_engine) as session:
        session.add_all([_problem("older-open", "medium"), _problem("newer-closed", "hard")])
        session.commit()
        older, _ = CoachDomain(session).create_proposal(1, [ProposalSelection("older-open")])
        newer, _ = CoachDomain(session).create_proposal(1, [ProposalSelection("newer-closed")])
        newer.status = ProposalStatus.PICKED
        session.commit()
        older_id = older.id

    sent = []

    async def fake_send(chat_id, text, **kwargs):
        sent.append(text)
        return len(sent)

    monkeypatch.setattr(application_module, "send_message", fake_send)
    application = CoachApplication.__new__(CoachApplication)
    application.engine = v2_engine

    assert await application._send_unsent_proposal(1) is True
    assert any("Older Open" in text for text in sent)
    assert all("Newer Closed" not in text for text in sent)
    with Session(v2_engine) as session:
        assert session.get(V2ProposalBatch, older_id).telegram_message_id is not None


@pytest.mark.asyncio
async def test_natural_language_pick_reviews_get_delivery_threads(v2_engine, monkeypatch):
    from leetcode_coach import application as application_module
    from leetcode_coach.domain.schemas import ProposalSelection
    from leetcode_coach.domain.services import CoachDomain

    with Session(v2_engine) as session:
        session.add(_problem("medium-a", "medium"))
        session.commit()
        batch, _ = CoachDomain(session).create_proposal(
            1, [ProposalSelection("medium-a", "reason", "hint")]
        )
        CoachDomain(session).commit_picks(1, batch.id, ["medium-a"])
        session.commit()

    sent = []

    async def fake_send_message(chat_id, text, **kwargs):
        sent.append((chat_id, text, kwargs))
        return 77

    monkeypatch.setattr(application_module, "send_message", fake_send_message)
    application = CoachApplication.__new__(CoachApplication)
    application.engine = v2_engine

    await application._send_unsent_reviews(1)
    await application._send_unsent_reviews(1)

    assert len(sent) == 1
    assert "Medium A" in sent[0][1]


@pytest.mark.asyncio
async def test_write_adapter_returns_generated_id_and_reattempt_is_idempotent(v2_engine):
    from leetcode_coach.domain.schemas import ProposalSelection
    from leetcode_coach.domain.services import CoachDomain

    with Session(v2_engine) as session:
        session.add(_problem("medium-a", "medium"))
        session.commit()
        batch, _ = CoachDomain(session).create_proposal(
            1, [ProposalSelection("medium-a", "reason", "hint")]
        )
        source = CoachDomain(session).commit_picks(1, batch.id, ["medium-a"])[0]
        CoachDomain(session).skip_problem(1, source.id)
        session.commit()
        source_id = source.id

    adapter = SQLCoachDomainAdapter(v2_engine)
    first = await adapter.reattempt_problem(chat_id=1, review_id=str(source_id))
    second = await adapter.reattempt_problem(chat_id=1, review_id=str(source_id))

    assert isinstance(first["id"], int)
    assert second["id"] == first["id"]


@pytest.mark.asyncio
async def test_solve_now_renders_titles_instead_of_internal_rows(v2_engine, monkeypatch):
    import asyncio
    from collections import defaultdict

    from leetcode_coach import application as application_module

    class FakeDomain:
        async def get_open_queue(self, *, chat_id):
            return {"reviews": [{"problem_slug": "number-of-islands", "chat_id": chat_id}]}

        async def get_problem(self, *, chat_id, slug):
            return {"slug": slug, "title": "Number of Islands", "chat_id": chat_id}

    sent = []

    async def fake_send(chat_id, text, **kwargs):
        sent.append(text)
        return 1

    async def fake_ack(*args, **kwargs):
        return None

    monkeypatch.setattr(application_module, "send_message", fake_send)
    monkeypatch.setattr(application_module, "answer_callback", fake_ack)
    application = CoachApplication.__new__(CoachApplication)
    application.engine = v2_engine
    application.domain = FakeDomain()
    application._locks = defaultdict(asyncio.Lock)

    await application.handle_callback(chat_id=1, callback_id="solve", data="v2n:solve")

    assert sent == ["Open queue:\n- Number of Islands"]
    assert "chat_id" not in sent[0]


def test_postgres_chat_lock_is_explicitly_unlocked_before_pool_return() -> None:
    executed = []

    class FakeCursor:
        def execute(self, statement, parameters):
            executed.append((statement, parameters))

        def close(self):
            executed.append(("cursor_closed", None))

    class FakeConnection:
        def cursor(self):
            return FakeCursor()

        def close(self):
            executed.append(("connection_closed", None))

    CoachApplication._release_database_chat_lock(FakeConnection(), 8_131_572_669)

    assert executed[0] == (
        "SELECT pg_advisory_unlock(%s)",
        (17_131_572_669,),
    )
    assert executed[-1] == ("connection_closed", None)


@pytest.mark.asyncio
async def test_conversation_session_persists_and_clears_items(v2_engine):
    store = PostgresAgentSession(v2_engine, 9)
    await store.add_items(
        [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}]
    )
    assert [item["role"] for item in await store.get_items()] == ["user", "assistant"]
    assert (await store.pop_item())["role"] == "assistant"
    await store.clear_session()
    assert await store.get_items() == []


@pytest.mark.asyncio
async def test_conversation_session_does_not_implicitly_split_long_tool_history(v2_engine):
    store = PostgresAgentSession(v2_engine, 10)
    items = [{"type": "message", "content": str(index)} for index in range(45)]
    items.extend(
        [
            {"type": "function_call", "call_id": "call-final", "name": "get_problem"},
            {"type": "function_call_output", "call_id": "call-final", "output": {}},
        ]
    )
    await store.add_items(items)

    loaded = await store.get_items()

    assert len(loaded) == 47
    assert loaded[-2]["call_id"] == loaded[-1]["call_id"] == "call-final"


@pytest.mark.asyncio
async def test_conversation_session_accepts_large_opaque_sdk_item(v2_engine):
    store = PostgresAgentSession(v2_engine, 11)
    await store.add_items([{"type": "function_call_output", "output": "x" * 20_000}])

    assert len((await store.get_items())[0]["output"]) == 20_000
