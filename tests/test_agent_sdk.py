import json
from types import SimpleNamespace

import pytest
from agents.tool_context import ToolContext
from pydantic import ValidationError

from leetcode_coach.agent.orchestrator import (
    AGENT_RUN_TIMEOUT_SECONDS,
    CACHEABLE_TERRA_CONTEXT,
    CANONICAL_LOOKUP_TIMEOUT_SECONDS,
    MAX_READ_TOOL_CONCURRENCY,
    MAX_TURNS,
    TERRA_MODEL,
    AgentMetrics,
    AgentRuntimeContext,
    AgentSettings,
    TerraCoachRunner,
    _usage_metrics,
    create_terra_agent,
)
from leetcode_coach.agent.tool_models import LessonDelta
from leetcode_coach.application import configure_openai_sdk

agents = pytest.importorskip("agents", reason="openai-agents is a V2 runtime dependency")


def test_terra_agent_has_bounded_model_and_expected_tools() -> None:
    agent = create_terra_agent()
    tools = {tool.name: tool for tool in agent.tools}

    assert agent.model == TERRA_MODEL
    assert agent.model_settings.parallel_tool_calls is False
    assert agent.model_settings.reasoning.effort == "medium"
    assert agent.model_settings.prompt_cache_options == {"mode": "explicit", "ttl": "30m"}
    assert {
        "get_learning_profile",
        "search_problem_pool",
        "get_problem",
        "get_open_queue",
        "get_progress",
        "get_walkthroughs",
        "draft_proposal",
        "ask_sol_advisor",
        "commit_picks",
        "commit_attempt",
        "commit_canonical_attempt",
        "skip_problem",
        "mark_solution_viewed",
        "reattempt_problem",
        "extend_proposal",
        "accept_credit_deficit",
        "adjust_lesson",
    } == set(tools)
    assert all(
        tools[name].needs_approval is False
        for name in {
            "commit_picks",
            "commit_attempt",
            "commit_canonical_attempt",
            "skip_problem",
            "mark_solution_viewed",
            "reattempt_problem",
            "extend_proposal",
            "accept_credit_deficit",
            "adjust_lesson",
        }
    )
    assert "Never refuse to record verified work solely" in CACHEABLE_TERRA_CONTEXT
    assert "ask for the problem slug or LeetCode URL" in CACHEABLE_TERRA_CONTEXT
    assert "use commit_canonical_attempt" in CACHEABLE_TERRA_CONTEXT
    assert "operation_key" not in str(tools["commit_canonical_attempt"].params_json_schema)
    assert "attempted_on" in tools["commit_canonical_attempt"].params_json_schema["properties"]
    assert tools["get_problem"].timeout_seconds == CANONICAL_LOOKUP_TIMEOUT_SECONDS == 70
    assert tools["get_progress"].timeout_seconds != CANONICAL_LOOKUP_TIMEOUT_SECONDS


def test_attempt_tools_require_internal_message_operation_key() -> None:
    agent = create_terra_agent()
    tools = {tool.name: tool for tool in agent.tools}
    assert "operation_key" not in str(tools["commit_attempt"].params_json_schema)
    assert "operation_key" not in str(tools["commit_canonical_attempt"].params_json_schema)


@pytest.mark.asyncio
async def test_attempt_tool_captures_receipt_out_of_band() -> None:
    receipt = {
        "title": "Coin Change",
        "result": "Solved",
        "credit": "+1.00",
        "balance": "0.00 → 1.00",
        "path": "Direct attempt (no queue needed)",
        "replayed": False,
    }

    observed = {}

    class FakeDomain:
        async def commit_canonical_attempt(self, **kwargs):
            observed.update(kwargs)
            return {"problem_slug": kwargs["problem_slug"], "receipt": receipt}

    context = AgentRuntimeContext(
        chat_id=1,
        domain=FakeDomain(),
        sol_advisor=object(),
        operation_key="message-1",
    )
    tool = next(
        tool for tool in create_terra_agent().tools if tool.name == "commit_canonical_attempt"
    )

    arguments = json.dumps(
        {
            "problem_slug": "coin-change",
            "outcome": "solved",
            "feedback": "passed",
            "lesson_delta": {"lesson_id": None},
            "attempted_on": "yesterday",
        }
    )
    await tool.on_invoke_tool(
        ToolContext(
            context,
            tool_name=tool.name,
            tool_call_id="call-1",
            tool_arguments=arguments,
        ),
        arguments,
    )

    assert context.receipts == [receipt]
    assert observed["attempted_on"] == "yesterday"


@pytest.mark.asyncio
async def test_completed_outcome_carries_captured_receipts() -> None:
    receipt = {"title": "Coin Change"}
    context = AgentRuntimeContext(chat_id=1, domain=object(), sol_advisor=object())
    context.receipts.append(receipt)
    result = SimpleNamespace(
        interruptions=[], raw_responses=[], final_output="Coaching only.", context_wrapper=None
    )

    outcome = await TerraCoachRunner(object())._outcome(result=result, context=context)

    assert outcome.receipts == [receipt]


def test_stable_prompt_reserves_final_text_for_coaching() -> None:
    assert "Final text is coaching only" in CACHEABLE_TERRA_CONTEXT


def test_runner_config_caps_local_tool_concurrency() -> None:
    config = TerraCoachRunner(object()).run_config()
    assert config.tool_execution.max_function_tool_concurrency == MAX_READ_TOOL_CONCURRENCY
    assert MAX_TURNS == 8
    assert AGENT_RUN_TIMEOUT_SECONDS == 600


def test_settings_adapt_v2_config_and_keep_the_hard_turn_limit() -> None:
    config = type(
        "Config",
        (),
        {
            "terra_model": "terra-test",
            "sol_advisor_model": "sol-test",
            "agent_max_turns": 8,
            "prompt_cache_ttl": "30m",
        },
    )()
    settings = AgentSettings.from_config(config)
    assert settings.terra_model == "terra-test"
    assert settings.sol_model == "sol-test"
    assert settings.max_turns == 8

    with pytest.raises(ValueError, match="between 1 and 8"):
        AgentSettings(max_turns=9)


def test_usage_metrics_records_cache_reads_and_writes() -> None:
    metrics = AgentMetrics()
    result = SimpleNamespace(
        new_items=[
            SimpleNamespace(raw_item=SimpleNamespace(type="function_call")),
            SimpleNamespace(raw_item=SimpleNamespace(type="function_call_output")),
        ],
        context_wrapper=SimpleNamespace(
            usage=SimpleNamespace(
                input_tokens=100,
                output_tokens=20,
                tool_calls=2,
                input_tokens_details=SimpleNamespace(
                    cached_tokens=80,
                    cache_write_tokens=10,
                ),
            )
        ),
    )
    _usage_metrics(result, metrics)
    assert metrics.cached_tokens == 80
    assert metrics.cache_write_tokens == 10
    assert metrics.tool_calls == 2


def test_configured_openai_key_is_injected_into_agents_sdk(monkeypatch) -> None:
    captured = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs

    def fake_set_client(client, *, use_for_tracing):
        captured["client"] = client
        captured["use_for_tracing"] = use_for_tracing

    monkeypatch.setattr("openai.AsyncOpenAI", FakeClient)
    monkeypatch.setattr(agents, "set_default_openai_client", fake_set_client)

    configure_openai_sdk("configured-through-dotenv")

    assert captured["client_kwargs"] == {
        "api_key": "configured-through-dotenv",
        "timeout": 90.0,
        "max_retries": 2,
    }
    assert captured["use_for_tracing"] is True


def test_tool_schemas_do_not_expose_runtime_context() -> None:
    agent = create_terra_agent()
    for tool in agent.tools:
        schema = tool.params_json_schema
        assert schema["type"] == "object"
        assert "ctx" not in schema["properties"]


def test_lesson_ids_are_database_integers_not_model_invented_names() -> None:
    with pytest.raises(ValidationError):
        LessonDelta(lesson_id="grid-connected-components")
    assert LessonDelta(lesson_id=7).lesson_id == 7


@pytest.mark.asyncio
async def test_legacy_attempt_approval_resume_uses_approval_id_as_operation_key(
    monkeypatch,
) -> None:
    target = SimpleNamespace(call_id="legacy-attempt-call")
    observed = {}

    class FakeState:
        def __init__(self, context):
            self.context = context

        def get_interruptions(self):
            return [target]

        def approve(self, item):
            assert item is target

        def reject(self, item, rejection_message):  # pragma: no cover - approve path only
            raise AssertionError((item, rejection_message))

    class FakeRunState:
        @staticmethod
        def from_json(agent, sdk_state, context_override):
            del agent, sdk_state
            return FakeState(context_override)

    class FakeRunner:
        @staticmethod
        async def run(agent, state, run_config, session):
            del agent, run_config, session
            observed["operation_key"] = state.context.operation_key
            return SimpleNamespace(interruptions=[], raw_responses=[], final_output="recorded")

    class FakeRepository:
        async def load(self, *, chat_id):
            return SimpleNamespace(
                chat_id=chat_id,
                expired=False,
                sdk_state={"legacy": True},
                run_id="legacy-run",
            )

        async def delete(self, *, chat_id, run_id):
            observed["deleted"] = (chat_id, run_id)

    monkeypatch.setattr(agents, "RunState", FakeRunState)
    monkeypatch.setattr(agents, "Runner", FakeRunner)
    context = AgentRuntimeContext(chat_id=7, domain=object(), sol_advisor=object())

    outcome = await TerraCoachRunner(FakeRepository()).resolve(
        chat_id=7,
        approval_id="legacy-attempt-call",
        decision="approve",
        context=context,
    )

    assert outcome.status == "completed"
    assert observed == {
        "operation_key": "legacy-attempt-call",
        "deleted": (7, "legacy-run"),
    }
    assert context.operation_key is None
