import asyncio
import datetime as dt
import json
from decimal import Decimal

import pytest
from agents.tool_context import ToolContext
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine, select

from leetcode_coach.agent.orchestrator import (
    AgentRunOutcome,
    AgentRuntimeContext,
    create_terra_agent,
)
from leetcode_coach.clock import local_wall_to_utc
from leetcode_coach.db.base import BaseSQLModel
from leetcode_coach.db.models import (
    Difficulty,
    ReviewStatus,
    V2Attempt,
    V2AttemptRevision,
    V2BotState,
    V2CreditLedger,
    V2FollowUp,
    V2PendingReview,
    V2Problem,
    V2ProposalBatch,
)
from leetcode_coach.domain.exceptions import Conflict
from leetcode_coach.domain.schemas import ProposalSelection
from leetcode_coach.domain.services import CoachDomain
from leetcode_coach.runtime.adapters import SQLCoachDomainAdapter


@pytest.fixture
def engine():
    value = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    BaseSQLModel.metadata.create_all(value)
    return value


@pytest.mark.asyncio
async def test_start_problem_upserts_and_replays_open_review(engine):
    adapter = SQLCoachDomainAdapter(engine)
    first = await adapter.start_problem(
        chat_id=9,
        problem_slug="https://leetcode.com/problems/two-sum/",
        title="Two Sum",
        difficulty="easy",
        tags="array,hash-table",
    )
    again = await adapter.start_problem(
        chat_id=9,
        problem_slug="two-sum",
        title=None,
        difficulty=None,
        tags="",
    )

    assert first["id"] == again["id"]
    with Session(engine) as session:
        assert len(session.exec(select(V2PendingReview)).all()) == 1


@pytest.mark.asyncio
async def test_memory_is_versioned_bounded_and_merged(engine):
    adapter = SQLCoachDomainAdapter(engine)
    await adapter.update_coaching_memory(
        chat_id=4,
        updates={"goals": ["interviews"], "preferences": ["language=python"]},
    )
    await adapter.update_coaching_memory(chat_id=4, updates={"notes": "Avoid spoilers"})
    memory = await adapter.get_coaching_memory(chat_id=4)

    assert memory["version"] == 1
    assert memory["goals"] == ["interviews"]
    assert memory["preferences"] == ["language=python"]
    assert memory["notes"] == "Avoid spoilers"
    with pytest.raises(ValueError, match="unsupported memory key"):
        await adapter.update_coaching_memory(chat_id=4, updates={"secret": "no"})


@pytest.mark.asyncio
async def test_rich_attempt_correction_and_reversal_are_compensating_and_idempotent(engine):
    adapter = SQLCoachDomainAdapter(engine)
    recorded = await adapter.record_problem_attempt(
        chat_id=1,
        problem_slug="coin-change",
        title="Coin Change",
        difficulty="medium",
        tags="dp",
        outcome="solved",
        feedback="ok",
        lesson_delta={},
        attempted_on="2026-08-15",
        operation_key="record-1",
        language="python",
        solution_summary="bottom-up dp",
        time_spent_min=25,
    )
    attempt_id = recorded["id"]
    corrected = await adapter.correct_attempt(
        chat_id=1,
        attempt_id=str(attempt_id),
        outcome="saw_solution",
        attempted_on="2026-08-14",
        feedback="needed help",
        language="python",
        solution_summary="read editorial",
        time_spent_min=35,
        reason="I misreported it",
        operation_key="correct-1",
    )
    replay = await adapter.correct_attempt(
        chat_id=1,
        attempt_id=str(attempt_id),
        outcome="saw_solution",
        attempted_on="2026-08-14",
        feedback="needed help",
        language="python",
        solution_summary="read editorial",
        time_spent_min=35,
        reason="I misreported it",
        operation_key="correct-1",
    )
    reversed_result = await adapter.reverse_attempt(
        chat_id=1,
        attempt_id=str(attempt_id),
        reason="duplicate",
        operation_key="reverse-1",
    )

    assert corrected["outcome"] == "saw_solution"
    assert replay["replayed"] is True
    assert reversed_result["reversed_at"] is not None
    assert (await adapter.get_learning_profile(chat_id=1))["recent_attempts"] == []
    with Session(engine) as session:
        assert session.get(V2Problem, "coin-change").times_attempted == 0
        assert session.get(V2Problem, "coin-change").solved is False
        assert len(session.exec(select(V2AttemptRevision)).all()) == 2
        ledger = session.exec(select(V2CreditLedger).order_by(V2CreditLedger.id)).all()
        assert sum(row.amount for row in ledger) == Decimal("0")
        assert [(row.amount, row.effective_on) for row in ledger[:3]] == [
            (Decimal("1.00"), dt.date(2026, 8, 15)),
            (Decimal("-1.00"), dt.date(2026, 8, 15)),
            (Decimal("0.25"), dt.date(2026, 8, 14)),
        ]


@pytest.mark.asyncio
async def test_attempted_outcome_keeps_review_open_and_history_excludes_reversed(engine):
    adapter = SQLCoachDomainAdapter(engine)
    review = await adapter.start_problem(
        chat_id=1,
        problem_slug="two-sum",
        title="Two Sum",
        difficulty="easy",
        tags="array",
    )
    result = await adapter.commit_attempt(
        chat_id=1,
        review_id=str(review["id"]),
        outcome="attempted",
        feedback="partial",
        lesson_delta={},
        operation_key="try-1",
        language="python",
        solution_summary="brute force",
        time_spent_min=10,
    )
    history = await adapter.search_attempt_history(chat_id=1, filters={}, limit=10)

    assert result["outcome"] == "attempted"
    with Session(engine) as session:
        assert session.get(V2PendingReview, review["id"]).status == ReviewStatus.OPEN
    assert [row["id"] for row in history] == [result["id"]]


@pytest.mark.asyncio
async def test_followup_schedule_list_cancel_is_idempotent_and_utc(engine):
    adapter = SQLCoachDomainAdapter(engine)
    first = await adapter.schedule_follow_up(
        chat_id=7,
        due_at="2026-10-01T09:00:00",
        message="Check the DP retry",
        operation_key="follow-1",
    )
    again = await adapter.schedule_follow_up(
        chat_id=7,
        due_at="2026-10-01T09:00:00",
        message="Check the DP retry",
        operation_key="follow-1",
    )
    assert first["id"] == again["id"]
    assert dt.datetime.fromisoformat(
        first["due_at"].replace("Z", "+00:00")
    ).utcoffset() == dt.timedelta(0)
    assert len(await adapter.list_follow_ups(chat_id=7, status="scheduled", limit=10)) == 1
    cancelled = await adapter.cancel_follow_up(
        chat_id=7, follow_up_id=first["id"], operation_key="cancel-1"
    )
    assert cancelled["status"] == "cancelled"
    with Session(engine) as session:
        assert len(session.exec(select(V2FollowUp)).all()) == 1


@pytest.mark.asyncio
async def test_correction_patch_preserves_omitted_fields(engine):
    adapter = SQLCoachDomainAdapter(engine)
    original = await adapter.record_problem_attempt(
        chat_id=1,
        problem_slug="two-sum",
        title="Two Sum",
        difficulty="easy",
        outcome="reviewed",
        feedback="keep this",
        lesson_delta=None,
        operation_key="record",
        language="python",
        solution_summary="keep summary",
        time_spent_min=12,
    )
    corrected = await adapter.correct_attempt(
        chat_id=1,
        attempt_id=str(original["id"]),
        outcome="solved",
        attempted_on=None,
        feedback=None,
        language=None,
        solution_summary=None,
        time_spent_min=None,
        reason="passed after retry",
        operation_key="correct",
    )
    assert corrected["feedback"] == "keep this"
    assert corrected["language"] == "python"
    assert corrected["solution_summary"] == "keep summary"
    assert corrected["time_spent_min"] == 12

    cleared = await adapter.correct_attempt(
        chat_id=1,
        attempt_id=str(original["id"]),
        outcome=None,
        attempted_on=None,
        feedback=None,
        language=None,
        clear_language=True,
        solution_summary=None,
        time_spent_min=None,
        clear_time_spent=True,
        reason="clear nullable metadata",
        operation_key="clear",
    )
    assert cleared["language"] is None
    assert cleared["time_spent_min"] is None


@pytest.mark.asyncio
async def test_due_followup_delivery_retries_failure_then_marks_delivered(engine, monkeypatch):
    from leetcode_coach import jobs

    with Session(engine) as session:
        session.add(
            V2FollowUp(
                id="due-1",
                chat_id=7,
                due_at=dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1),
                message="Retry coin change",
                idempotency_key="due-1",
            )
        )
        session.commit()
    calls = []

    async def fail_once(chat_id, message, **kwargs):
        calls.append((chat_id, message))
        if len(calls) == 1:
            raise RuntimeError("transport down")
        return 1

    monkeypatch.setattr(jobs, "engine", engine)
    monkeypatch.setattr(jobs, "send_message", fail_once)
    await jobs.deliver_due_follow_ups()
    with Session(engine) as session:
        row = session.get(V2FollowUp, "due-1")
        assert row.status == "scheduled"
        assert row.attempt_count == 1
    await jobs.deliver_due_follow_ups()
    with Session(engine) as session:
        row = session.get(V2FollowUp, "due-1")
        assert row.status == "delivered"
        assert row.attempt_count == 2
    assert calls == [(7, "Retry coin change"), (7, "Retry coin change")]


@pytest.mark.asyncio
async def test_delivering_followup_cannot_be_cancelled(engine):
    with Session(engine) as session:
        row = V2FollowUp(
            id="claimed",
            chat_id=7,
            due_at=dt.datetime.now(dt.UTC),
            message="claimed",
            status="delivering",
            idempotency_key="claimed",
        )
        session.add(row)
        session.commit()
    with pytest.raises(Conflict, match="delivering"):
        await SQLCoachDomainAdapter(engine).cancel_follow_up(
            chat_id=7, follow_up_id="claimed", operation_key="cancel"
        )


@pytest.mark.asyncio
async def test_stale_delivering_followup_is_reclaimed_once_after_claim_crash(engine, monkeypatch):
    from leetcode_coach import jobs

    now = dt.datetime.now(dt.UTC)
    with Session(engine) as session:
        session.add(
            V2FollowUp(
                id="crashed-claim",
                chat_id=7,
                due_at=now - dt.timedelta(minutes=10),
                message="Resume me",
                status="delivering",
                idempotency_key="crashed-claim",
                updated_at=now - dt.timedelta(minutes=10),
            )
        )
        session.commit()
    calls = []

    async def send_once(chat_id, message, **kwargs):
        calls.append((chat_id, message))
        return 1

    monkeypatch.setattr(jobs, "engine", engine)
    monkeypatch.setattr(jobs, "send_message", send_once)
    await jobs.deliver_due_follow_ups()
    await jobs.deliver_due_follow_ups()

    with Session(engine) as session:
        row = session.get(V2FollowUp, "crashed-claim")
        assert row.status == "delivered"
        assert row.attempt_count == 1
    assert calls == [(7, "Resume me")]


@pytest.mark.asyncio
async def test_runtime_serializes_writes_and_keeps_sol_available_after_write():
    context = AgentRuntimeContext(chat_id=1, domain=object(), sol_advisor=object())
    active = 0
    maximum = 0

    async def mutation():
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.01)
        active -= 1
        return {"ok": True}

    await asyncio.gather(context.write(mutation), context.write(mutation))
    assert maximum == 1
    assert context.write_started is True
    assert context.sol_allowed() is True


@pytest.mark.asyncio
async def test_runtime_allows_parallel_reads_and_only_one_concurrent_sol():
    class Advisor:
        calls = 0

        async def advise(self, **kwargs):
            from leetcode_coach.agent.advisor import SolAdvice

            self.calls += 1
            await asyncio.sleep(0.01)
            return SolAdvice("ok", [], [], "next")

    context = AgentRuntimeContext(chat_id=1, domain=object(), sol_advisor=Advisor())
    active = 0
    maximum = 0

    async def reading():
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.01)
        active -= 1
        return {}

    await asyncio.gather(context.read(reading), context.read(reading))
    assert maximum == 2
    tool = next(tool for tool in create_terra_agent().tools if tool.name == "ask_sol_advisor")
    arguments = json.dumps(
        {
            "request": {
                "objective": "help",
                "evidence": [],
                "constraints": "none",
                "uncertainty": "hard",
            }
        }
    )
    await asyncio.gather(
        *[
            tool.on_invoke_tool(
                ToolContext(
                    context,
                    tool_name=tool.name,
                    tool_call_id=f"sol-{index}",
                    tool_arguments=arguments,
                ),
                arguments,
            )
            for index in range(2)
        ]
    )
    assert context.sol_advisor.calls == 1


@pytest.mark.asyncio
async def test_tool_path_accepts_no_lesson_and_structured_memory(engine):
    adapter = SQLCoachDomainAdapter(engine)
    context = AgentRuntimeContext(
        chat_id=1, domain=adapter, sol_advisor=object(), operation_key="tool-message"
    )
    tools = {tool.name: tool for tool in create_terra_agent().tools}
    attempt_args = json.dumps(
        {
            "problem_slug": "two-sum",
            "title": "Two Sum",
            "difficulty": "easy",
            "outcome": "solved",
        }
    )
    await tools["record_problem_attempt"].on_invoke_tool(
        ToolContext(
            context,
            tool_name="record_problem_attempt",
            tool_call_id="a",
            tool_arguments=attempt_args,
        ),
        attempt_args,
    )
    memory_args = json.dumps({"updates": {"preferences": ["language=python", "hints=progressive"]}})
    await tools["update_coaching_memory"].on_invoke_tool(
        ToolContext(
            context,
            tool_name="update_coaching_memory",
            tool_call_id="m",
            tool_arguments=memory_args,
        ),
        memory_args,
    )
    with Session(engine) as session:
        assert session.exec(select(V2Attempt)).one().feedback == ""
        assert session.exec(select(V2BotState).where(V2BotState.key == "coaching_memory")).one()
        from leetcode_coach.db.models import V2Lesson

        assert session.exec(select(V2Lesson)).all() == []


@pytest.mark.asyncio
async def test_proposal_publication_replays_same_batch(engine):
    adapter = SQLCoachDomainAdapter(engine)
    selection = [
        {
            "slug": "two-sum",
            "title": "Two Sum",
            "difficulty": "easy",
            "reasoning": "x",
            "coaching_hint": "y",
        }
    ]
    first = await adapter.publish_practice_set(
        chat_id=1, selections=selection, operation_key="same"
    )
    replay = await adapter.publish_practice_set(
        chat_id=1, selections=selection, operation_key="same"
    )
    assert replay == {"batch_id": first["batch_id"], "replayed": True}
    with Session(engine) as session:
        assert len(session.exec(select(V2ProposalBatch)).all()) == 1


@pytest.mark.asyncio
async def test_catalog_modes_and_45_day_streak(engine, monkeypatch):
    today = dt.date(2026, 8, 16)
    monkeypatch.setattr("leetcode_coach.runtime.adapters.local_today", lambda: today)
    with Session(engine) as session:
        session.add_all(
            [
                V2Problem(slug="open", title="Open", url="u", difficulty=Difficulty.EASY),
                V2Problem(
                    slug="solved",
                    title="Solved",
                    url="u",
                    difficulty=Difficulty.MEDIUM,
                    solved=True,
                ),
                V2Problem(
                    slug="blocked",
                    title="Blocked",
                    url="u",
                    difficulty=Difficulty.HARD,
                    eligible=False,
                ),
            ]
        )
        session.add_all(
            [
                V2Attempt(
                    chat_id=1,
                    problem_slug="open",
                    attempted_on=today - dt.timedelta(days=index),
                    outcome="attempted",
                )
                for index in range(45)
            ]
        )
        session.commit()
    adapter = SQLCoachDomainAdapter(engine)
    assert [
        row["slug"]
        for row in await adapter.search_problem_catalog(
            chat_id=1, mode="solved", filters={}, limit=20
        )
    ] == ["solved"]
    assert [
        row["slug"]
        for row in await adapter.search_problem_catalog(
            chat_id=1, mode="ineligible", filters={}, limit=20
        )
    ] == ["blocked"]
    assert (
        len(await adapter.search_problem_catalog(chat_id=1, mode="all", filters={}, limit=20)) == 3
    )
    assert (await adapter.get_progress(chat_id=1))["streak_days"] == 45


def test_bucharest_dst_rejects_gap_and_requires_fold_for_ambiguity():
    with pytest.raises(ValueError, match="nonexistent"):
        local_wall_to_utc("2026-03-29T03:30:00")
    with pytest.raises(ValueError, match="ambiguous"):
        local_wall_to_utc("2026-10-25T03:30:00")
    assert local_wall_to_utc("2026-10-25T03:30:00", fold=0) != local_wall_to_utc(
        "2026-10-25T03:30:00", fold=1
    )


@pytest.mark.asyncio
async def test_hint_callback_progresses_once_per_callback_identity(engine, monkeypatch):
    from leetcode_coach import application as application_module
    from leetcode_coach.application import CoachApplication

    with Session(engine) as session:
        session.add(
            V2Problem(
                slug="two-sum",
                title="Two Sum",
                url="https://leetcode.com/problems/two-sum/",
                difficulty=Difficulty.EASY,
                tags="array",
            )
        )
        session.flush()
        batch, _ = CoachDomain(session).create_proposal(
            1, [ProposalSelection("two-sum", "practice hashes", "start with complements")]
        )
        review = CoachDomain(session).commit_picks(1, batch.id, ["two-sum"])[0]
        review_id = review.id
        session.commit()

    prompts = []

    class FakeRunner:
        async def run(self, **kwargs):
            prompts.append(kwargs["message"])
            return AgentRunOutcome("completed", f"generated-{len(prompts)}", {})

    sent = []

    async def fake_send(chat_id, text, **kwargs):
        sent.append(text)
        return len(sent)

    monkeypatch.setattr(application_module, "send_message", fake_send)
    app = CoachApplication.__new__(CoachApplication)
    app.engine = engine
    app.domain = SQLCoachDomainAdapter(engine)
    app.advisor = object()
    app.runner = FakeRunner()

    async def fake_deliver(chat_id, outcome, **kwargs):
        if outcome.text:
            await fake_send(chat_id, outcome.text, **kwargs)

    app._deliver_outcome = fake_deliver

    await app._direct_review_action(1, "hint", review_id, callback_id="same")
    await app._direct_review_action(1, "hint", review_id, callback_id="same")
    await app._direct_review_action(1, "hint", review_id, callback_id="new")

    assert len(prompts) == 2
    assert "level 1 of 3" in prompts[0]
    assert "level 2 of 3" in prompts[1]
    assert sent == ["generated-1", "generated-1", "generated-2"]


@pytest.mark.asyncio
async def test_direct_start_review_hint_and_why_use_terra_and_delivery_pipeline(engine):
    from leetcode_coach.application import CoachApplication

    adapter = SQLCoachDomainAdapter(engine)
    review = await adapter.start_problem(
        chat_id=1,
        problem_slug="coin-change",
        title="Coin Change",
        difficulty="medium",
        tags="dp",
    )
    prompts = []
    delivered = []

    class FakeRunner:
        async def run(self, **kwargs):
            prompts.append(kwargs["message"])
            return AgentRunOutcome("completed", f"terra-{len(prompts)}", {})

    app = CoachApplication.__new__(CoachApplication)
    app.engine = engine
    app.domain = adapter
    app.advisor = object()
    app.runner = FakeRunner()

    async def capture(chat_id, outcome, **kwargs):
        delivered.append((chat_id, outcome.text, kwargs))

    app._deliver_outcome = capture
    await app._direct_review_action(1, "hint", review["id"], callback_id="hint-direct")
    await app._direct_review_action(1, "why", review["id"], callback_id="why-direct")

    assert "Coin Change" in prompts[0] and "Optional prior context" in prompts[0]
    assert "Coin Change" in prompts[1] and "Optional selection rationale" in prompts[1]
    assert [item[1] for item in delivered] == ["terra-1", "terra-2"]
