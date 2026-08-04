import json

import pytest

from leetcode_coach_v2.agent.advisor import OpenAISolAdvisor


@pytest.mark.asyncio
async def test_sol_advisor_makes_one_toolless_structured_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received: dict[str, object] = {}

    class FakeResponses:
        async def create(self, **kwargs: object) -> object:
            received.update(kwargs)
            return type(
                "Response",
                (),
                {
                    "output_text": json.dumps(
                        {
                            "recommendation": "Ask one focused question.",
                            "risks": ["missing code"],
                            "missing_evidence": ["current approach"],
                            "suggested_next_action": "Request the failing test.",
                        }
                    )
                },
            )()

    class FakeClient:
        responses = FakeResponses()

    def fake_openai(**kwargs):
        received["client_kwargs"] = kwargs
        return FakeClient()

    monkeypatch.setattr("openai.AsyncOpenAI", fake_openai)
    advice = await OpenAISolAdvisor(api_key="dotenv-key").advise(
        objective="Help with a difficult dynamic-programming explanation.",
        evidence={"profile": "arrays"},
        constraints="Read only.",
        uncertainty="Which hint to give first?",
    )

    assert received["model"] == "gpt-5.6-sol"
    assert received["store"] is False
    assert "tools" not in received
    assert received["client_kwargs"] == {
        "api_key": "dotenv-key",
        "timeout": 90.0,
        "max_retries": 2,
    }
    assert advice.suggested_next_action == "Request the failing test."
