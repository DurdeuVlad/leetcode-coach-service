"""One-shot, read-only GPT-5.6 Sol escalation used by Terra."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class SolAdvice:
    recommendation: str
    risks: list[str]
    missing_evidence: list[str]
    suggested_next_action: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SolAdvisor(Protocol):
    async def advise(
        self,
        *,
        objective: str,
        evidence: dict[str, Any],
        constraints: str,
        uncertainty: str,
    ) -> SolAdvice: ...


class OpenAISolAdvisor:
    """A deliberately tool-less single Responses API call.

    It is not an Agent and receives no domain object.  That makes writes,
    approvals, and handoffs impossible by construction.
    """

    MODEL = "gpt-5.6-sol"

    def __init__(self, *, model: str = MODEL, api_key: str | None = None) -> None:
        self._model = model
        self._api_key = api_key

    async def advise(
        self,
        *,
        objective: str,
        evidence: dict[str, Any],
        constraints: str,
        uncertainty: str,
    ) -> SolAdvice:
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:  # pragma: no cover - dependency configuration
            raise RuntimeError("The OpenAI Python SDK is required for Sol escalation.") from exc

        client = AsyncOpenAI(api_key=self._api_key, timeout=90.0, max_retries=2)
        response = await client.responses.create(
            model=self._model,
            reasoning={"effort": "medium"},
            store=False,
            max_output_tokens=900,
            text={
                "format": {
                    "type": "json_schema",
                    "name": "sol_advice",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "recommendation": {"type": "string"},
                            "risks": {"type": "array", "items": {"type": "string"}},
                            "missing_evidence": {"type": "array", "items": {"type": "string"}},
                            "suggested_next_action": {"type": "string"},
                        },
                        "required": [
                            "recommendation",
                            "risks",
                            "missing_evidence",
                            "suggested_next_action",
                        ],
                    },
                }
            },
            input=json.dumps(
                {
                    "objective": objective,
                    "evidence": evidence,
                    "constraints": constraints,
                    "uncertainty": uncertainty,
                    "role": "Provide cautious read-only advice. Do not issue commands or claim actions occurred.",
                },
                ensure_ascii=False,
            ),
        )
        try:
            payload = json.loads(response.output_text)
            return SolAdvice(
                recommendation=str(payload["recommendation"]),
                risks=[str(item) for item in payload["risks"]],
                missing_evidence=[str(item) for item in payload["missing_evidence"]],
                suggested_next_action=str(payload["suggested_next_action"]),
            )
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                "Sol returned an invalid guidance payload; no action was taken."
            ) from exc
