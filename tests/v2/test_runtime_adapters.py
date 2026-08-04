import asyncio
from collections import defaultdict
from types import SimpleNamespace

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine, select

from leetcode_coach_v2.agent.orchestrator import AgentRunOutcome, AgentSettings
from leetcode_coach_v2.agent.state import PendingApproval, SerializedRunState
from leetcode_coach_v2.application import CoachApplication
from leetcode_coach_v2.db.base import V2SQLModel
from leetcode_coach_v2.db.models import (
    ApprovalStatus,
    Difficulty,
    V2AgentRun,
    V2Attempt,
    V2PendingApproval,
    V2Problem,
)
from leetcode_coach_v2.domain.exceptions import Conflict
from leetcode_coach_v2.runtime.adapters import (
    PostgresAgentSession,
    SQLCoachDomainAdapter,
    SQLRunStateRepository,
)


@pytest.fixture
def v2_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    V2SQLModel.metadata.create_all(engine)
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
    result = await adapter.draft_proposal(chat_id=1, selections=selections)
    house_robber = next(item for item in result["candidates"] if item["slug"] == "house-robber")
    assert house_robber["difficulty"] == "easy"


@pytest.mark.asyncio
async def test_draft_rejects_invented_slug(v2_engine):
    adapter = SQLCoachDomainAdapter(v2_engine)
    selections = [
        {"slug": f"invented-{index}", "reasoning": "x", "coaching_hint": "y"}
        for index in range(5)
    ]
    with pytest.raises(Exception, match="unknown canonical problem slug"):
        await adapter.draft_proposal(chat_id=1, selections=selections)


@pytest.mark.asyncio
async def test_run_state_alias_round_trip_and_expiry(v2_engine):
    repository = SQLRunStateRepository(v2_engine)
    state = SerializedRunState.new(
        chat_id=7,
        sdk_state={"opaque": True},
        approvals=[PendingApproval("call-1", "commit_picks", "call-1", {}, "Approve picks?")],
    )
    await repository.save(state)
    rows = await repository.pending_rows(7)
    assert len(rows) == 1
    assert len(rows[0].id) == 16
    assert await repository.resolve_alias(7, rows[0].id, True) == "call-1"
    resumable = await repository.load(chat_id=7)
    assert resumable is not None
    assert [approval.approval_id for approval in resumable.approvals] == ["call-1"]
    await repository.finalize_alias(7, rows[0].id, True)
    assert await repository.resolve_alias(7, rows[0].id, True) is None


@pytest.mark.asyncio
async def test_new_paused_state_expires_superseded_approval_aliases(v2_engine):
    repository = SQLRunStateRepository(v2_engine)
    first = SerializedRunState.new(
        chat_id=70,
        sdk_state={"step": 1},
        approvals=[PendingApproval("call-1", "commit_picks", "call-1", {}, "First")],
    )
    second = SerializedRunState.new(
        chat_id=70,
        sdk_state={"step": 2},
        approvals=[PendingApproval("call-2", "commit_attempt", "call-2", {}, "Second")],
    )
    await repository.save(first)
    await repository.save(second)

    pending = await repository.pending_rows(70)
    assert [row.summary for row in pending] == ["Second"]
    with Session(v2_engine) as session:
        old = session.exec(
            select(V2PendingApproval).where(V2PendingApproval.agent_run_id == first.run_id)
        ).one()
        assert old.status == ApprovalStatus.EXPIRED


@pytest.mark.asyncio
async def test_paused_metrics_update_preserves_resumable_sdk_state(v2_engine):
    repository = SQLRunStateRepository(v2_engine)
    state = SerializedRunState.new(
        chat_id=8,
        sdk_state={"opaque": {"continuation": True}},
        approvals=[PendingApproval("call-2", "skip_problem", "call-2", {}, "Skip?")],
    )
    await repository.save(state)
    application = CoachApplication.__new__(CoachApplication)
    application.engine = v2_engine
    application.agent_settings = AgentSettings()
    application._persist_metrics(
        8,
        AgentRunOutcome(
            status="awaiting_approval",
            text=None,
            approvals=state.approvals,
            metrics={
                "turns": 2,
                "input_tokens": 10,
                "cache_write_tokens": 4,
                "escalation_reason": "hard recursion tradeoff",
            },
            run_id=state.run_id,
        ),
    )

    loaded = await repository.load(chat_id=8)
    assert loaded is not None
    assert loaded.sdk_state == {"opaque": {"continuation": True}}
    with Session(v2_engine) as session:
        assert len(session.exec(select(V2AgentRun)).all()) == 1
        assert session.get(V2AgentRun, state.run_id).cache_write_tokens == 4
        assert session.get(V2AgentRun, state.run_id).escalation_reason == "hard recursion tradeoff"


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
    rows = await adapter.search_problem_pool(
        chat_id=1,
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
async def test_pending_approval_blocks_a_second_agent_run(v2_engine, monkeypatch):
    from leetcode_coach_v2 import application as application_module

    class FakeRepository:
        async def pending_rows(self, chat_id):
            return [
                SimpleNamespace(
                    id="alias",
                    summary="Approve pending write?",
                    approval_message_id=77,
                )
            ]

    class FakeRunner:
        async def run(self, **kwargs):
            raise AssertionError("a second agent run must not start")

    sent = []

    async def fake_send(chat_id, text, **kwargs):
        sent.append(text)
        return 1

    monkeypatch.setattr(application_module, "send_message", fake_send)
    application = CoachApplication.__new__(CoachApplication)
    application.engine = v2_engine
    application.repository = FakeRepository()
    application.runner = FakeRunner()
    application._locks = defaultdict(asyncio.Lock)

    await application.handle_text(
        chat_id=1,
        text="give me another proposal",
        message_id=10,
        reply_to_message_id=None,
    )

    assert sent == [
        "Resolve the pending approval with Approve/Reject or exact yes/no first."
    ]


@pytest.mark.asyncio
async def test_stale_review_callback_is_acknowledged_without_retry(v2_engine, monkeypatch):
    from leetcode_coach_v2 import application as application_module

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
async def test_natural_language_pick_reviews_get_delivery_threads(v2_engine, monkeypatch):
    from leetcode_coach_v2 import application as application_module
    from leetcode_coach_v2.domain.schemas import ProposalSelection
    from leetcode_coach_v2.domain.services import CoachDomain

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
    from leetcode_coach_v2.domain.schemas import ProposalSelection
    from leetcode_coach_v2.domain.services import CoachDomain

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

    from leetcode_coach_v2 import application as application_module

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
    await store.add_items([{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}])
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
