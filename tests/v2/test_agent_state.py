from datetime import UTC, datetime, timedelta

import pytest

from leetcode_coach_v2.agent.orchestrator import (
    CACHEABLE_TERRA_CONTEXT,
    AgentRuntimeContext,
    TerraCoachRunner,
    _bounded,
)
from leetcode_coach_v2.agent.state import PendingApproval, SerializedRunState, text_confirmation


def test_text_confirmation_requires_exact_yes_or_no_and_clear_target() -> None:
    assert (
        text_confirmation(" yes ", replying_to_approval=True, pending_approval_count=2) == "approve"
    )
    assert text_confirmation("NO", replying_to_approval=False, pending_approval_count=1) == "reject"
    assert (
        text_confirmation("yes please", replying_to_approval=True, pending_approval_count=1) is None
    )
    assert text_confirmation("yes", replying_to_approval=False, pending_approval_count=2) is None


def test_serialized_state_has_24_hour_expiry_and_round_trips() -> None:
    state = SerializedRunState.new(
        chat_id=10,
        sdk_state={"version": 1},
        approvals=[PendingApproval("a", "commit_picks", "a", {}, "Approve?")],
    )

    payload = state.to_dict()
    assert state.expires_at - state.created_at <= timedelta(hours=24, seconds=1)
    assert payload["chat_id"] == 10
    assert payload["approvals"][0]["tool_name"] == "commit_picks"


def test_serialized_state_uses_configured_ttl() -> None:
    state = SerializedRunState.new(chat_id=1, sdk_state={}, approvals=[], ttl_hours=72)
    hours = (state.expires_at - state.created_at).total_seconds() / 3600
    assert hours == 72


def test_expired_state_is_detected() -> None:
    state = SerializedRunState(
        chat_id=10,
        run_id="run",
        sdk_state={},
        approvals=[],
        created_at=datetime.now(UTC) - timedelta(days=2),
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    assert state.expired


def test_tool_results_are_bounded() -> None:
    value = {"items": [{"text": "x" * 5_000} for _ in range(50)]}
    bounded = _bounded(value)
    assert len(bounded["items"]) == 30
    assert len(bounded["items"][0]["text"]) == 4_000


def test_sol_cannot_run_after_write_or_pending_approval() -> None:
    context = AgentRuntimeContext(chat_id=1, domain=object(), sol_advisor=object())
    assert context.sol_allowed()
    context.write_started = True
    assert not context.sol_allowed()
    context.write_started = False
    context.approval_pending = True
    assert not context.sol_allowed()


def test_cache_breakpoint_precedes_volatile_user_message() -> None:
    request = TerraCoachRunner._input("show my status")
    content = request[0]["content"]
    assert content[0]["text"] == CACHEABLE_TERRA_CONTEXT
    assert content[0]["prompt_cache_breakpoint"] == {"mode": "explicit"}
    assert content[1]["text"] == "show my status"
    assert "Never simulate, narrate, or manually request approval" in content[0]["text"]


def test_sdk_missing_is_a_clear_runtime_error() -> None:
    pytest.importorskip("agents", reason="SDK-specific test only applies when installed")
