from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from leetcode_coach_v2.agent.orchestrator import (
    AGENT_RUN_TIMEOUT_SECONDS,
    MAX_READ_TOOL_CONCURRENCY,
    MAX_TURNS,
    TERRA_MODEL,
    AgentMetrics,
    AgentSettings,
    TerraCoachRunner,
    _usage_metrics,
    create_terra_agent,
)
from leetcode_coach_v2.agent.tool_models import LessonDelta
from leetcode_coach_v2.application import configure_openai_sdk

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
        "skip_problem",
        "mark_solution_viewed",
        "reattempt_problem",
        "extend_proposal",
        "accept_credit_deficit",
        "adjust_lesson",
    } == set(tools)
    assert all(
        tools[name].needs_approval is True
        for name in tools
        if name.startswith(
            ("commit_", "skip_", "mark_", "reattempt_", "extend_", "accept_", "adjust_")
        )
    )


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
        )
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
