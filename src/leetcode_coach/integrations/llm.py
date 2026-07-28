"""LLM client — OpenAI primary + Gemini fallback, with explicit fallback logic.

Per architecture.md §5 / issue #010's fallback decision table:

| Primary raises                       | Retry? | Fall back? | Alert? |
|---------------------------------------|--------|------------|--------|
| APIConnectionError / timeout          | yes    | after retries exhausted | degraded |
| RateLimitError (429)                  | yes    | after retries exhausted | degraded |
| InternalServerError (5xx)             | yes    | after retries exhausted | degraded |
| AuthenticationError (401)             | no     | immediately | config error |
| BadRequestError/403/404               | no     | no (raises `LLMUnavailableError`) | yes |

If Gemini also fails, raise `LLMUnavailableError` — never fabricate content.

`stop_after_attempt(2)` is a hard cap so retry + fallback can never loop
indefinitely (the n8n #18797 bug is structurally impossible here: retry
and fallback are separate code paths, not nested node behaviors).

Mock-aware: if `OPENAI_API_KEY` is the placeholder `mock` or empty, returns
canned JSON responses so the flows can be exercised end-to-end without real
LLM credentials.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import structlog
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from leetcode_coach.config import get_settings
from leetcode_coach.errors import LLMUnavailableError

log = structlog.get_logger("llm")

# Primary/fallback model names (architecture.md §2).
_PRIMARY_MODEL = "gpt-5.6-sol"
_FALLBACK_MODEL = "gemini-3.6-flash"


@dataclass
class LLMResponse:
    """Typed completion result. `tokens_in`/`tokens_out` feed the cost NFR
    (<$10/month, NFR-2) via the structured cost log emitted per call."""

    text: str
    model: str
    tokens_in: int
    tokens_out: int


class _TransientLLMError(Exception):
    """Timeout/5xx/429 from the primary — retried by tenacity, then falls
    back to Gemini once retries are exhausted."""


class _AuthLLMError(Exception):
    """401 from the primary — no retry, immediate fallback to Gemini."""


def _is_mock() -> bool:
    key = get_settings().openai_api_key
    return not key or key == "mock"


class LLMClient:
    """Primary OpenAI + fallback Gemini, with mock mode for development.

    The same client serves both the propose pass (Flow A) and the coach
    pass (Flow B); only the prompt differs — this client is prompt-agnostic.
    """

    async def complete(self, system: str, user: str, *, max_tokens: int = 2000) -> LLMResponse:
        """Return the LLM's completion, trying primary then falling back.

        Raises `LLMUnavailableError` if the request itself is rejected
        (400/403/404 — no retry, no fallback) or if both primary and
        fallback ultimately fail.
        """
        if _is_mock():
            return _mock_response(system, user)

        try:
            return await self._call_primary_with_retry(system, user, max_tokens)
        except _AuthLLMError as e:
            log.warning("primary_llm_auth_failed_falling_back", error=str(e))
        except _TransientLLMError as e:
            log.warning("primary_llm_transient_failed_falling_back", error=str(e))
        # Auth or transient (post-retry) failure — explicit fallback path.
        try:
            return await self._call_fallback_with_retry(system, user, max_tokens)
        except Exception as e2:
            raise LLMUnavailableError(f"both primary and fallback LLM failed: {e2}") from e2

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(min=2, max=10),
        retry=retry_if_exception_type(_TransientLLMError),
        reraise=True,
    )
    async def _call_primary_with_retry(
        self, system: str, user: str, max_tokens: int
    ) -> LLMResponse:
        return await self._call_primary(system, user, max_tokens)

    async def _call_primary(self, system: str, user: str, max_tokens: int) -> LLMResponse:
        """OpenAI call. Classifies exceptions per the decision table:
        transient (retry), auth (fallback immediately), or request-level
        (no retry, no fallback — raises `LLMUnavailableError`)."""
        import openai

        # max_retries=0: our tenacity wrapper is the single retry owner,
        # otherwise the SDK's own auto-retry compounds retry counts.
        client = openai.AsyncOpenAI(api_key=get_settings().openai_api_key, max_retries=0)
        try:
            resp = await client.chat.completions.create(
                model=_PRIMARY_MODEL,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
            )
        except openai.AuthenticationError as e:
            raise _AuthLLMError(str(e)) from e
        except (openai.APIConnectionError, openai.RateLimitError, openai.InternalServerError) as e:
            raise _TransientLLMError(str(e)) from e
        except (openai.BadRequestError, openai.PermissionDeniedError, openai.NotFoundError) as e:
            raise LLMUnavailableError(f"openai request rejected: {e}") from e

        usage = resp.usage
        return LLMResponse(
            text=resp.choices[0].message.content or "",
            model=resp.model,
            tokens_in=usage.prompt_tokens if usage else 0,
            tokens_out=usage.completion_tokens if usage else 0,
        )

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(min=2, max=10),
        retry=retry_if_exception_type(_TransientLLMError),
        reraise=True,
    )
    async def _call_fallback_with_retry(
        self, system: str, user: str, max_tokens: int
    ) -> LLMResponse:
        return await self._call_fallback(system, user, max_tokens)

    async def _call_fallback(self, system: str, user: str, max_tokens: int) -> LLMResponse:
        """Gemini call. Treats 429/5xx/timeouts as transient (retry);
        400/401/403 raise `LLMUnavailableError` (no retry)."""
        from google import genai
        from google.genai import errors as genai_errors
        from google.genai import types

        client = genai.Client(api_key=get_settings().gemini_api_key)
        try:
            resp = await client.aio.models.generate_content(
                model=_FALLBACK_MODEL,
                contents=user,
                config=types.GenerateContentConfig(
                    system_instruction=system,
                    max_output_tokens=max_tokens,
                    response_mime_type="application/json",
                ),
            )
        except genai_errors.ServerError as e:
            raise _TransientLLMError(str(e)) from e
        except genai_errors.ClientError as e:
            code = getattr(e, "code", None)
            if code == 429:
                raise _TransientLLMError(str(e)) from e
            raise LLMUnavailableError(f"gemini request rejected: {e}") from e

        usage = resp.usage_metadata
        return LLMResponse(
            text=resp.text or "",
            model=_FALLBACK_MODEL,
            tokens_in=usage.prompt_token_count if usage else 0,
            tokens_out=usage.candidates_token_count if usage else 0,
        )


def parse_json_response(text: str) -> dict:
    """Parse the JSON object both flows expect out of a completion.

    Strips markdown code fences defensively in case a model ignores the
    "no code fences" instruction in the prompt.
    """
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()
    return json.loads(cleaned)


def _mock_response(system: str, user: str) -> LLMResponse:
    """Return a canned `LLMResponse` matching the prompt output contract.

    Detects propose vs coach by inspecting the system/user prompt content.
    The shapes match what prompts/propose.py and prompts/coach.py require.
    """
    if "propose 5 candidate" in system.lower() or "candidate_list_markdown" in system.lower():
        text = json.dumps(_MOCK_PROPOSE)
    elif "tutor_feedback" in system.lower() or "lesson_should_graduate" in system.lower():
        text = json.dumps(_MOCK_COACH)
    else:
        text = json.dumps({"text": "mock llm response"})
    return LLMResponse(text=text, model="mock", tokens_in=0, tokens_out=0)


# Canned responses — shaped to match the prompt output contracts exactly.
# The propose mock returns 5 candidates drawn from common LeetCode problems.
_MOCK_PROPOSE: dict[str, Any] = {
    "candidate_list_markdown": (
        "1. *Two Sum* — array,hash-map — easy — https://leetcode.com/problems/two-sum/\n"
        "   Why: warmup; targets your 'check empty input' lesson.\n"
        "   Hint: before writing code, ask: can I trade space for time?\n"
        "2. *Binary Search* — array,binary-search — easy — https://leetcode.com/problems/binary-search/\n"
        "   Why: reinforces your 'off-by-one on inclusive bounds' lesson.\n"
        "   Hint: pick your bounds convention (inclusive vs half-open) and stick with it.\n"
        "3. *Longest Substring Without Repeating Characters* — hash-map,two-pointers — medium — https://leetcode.com/problems/longest-substring-without-repeating-characters/\n"
        "   Why: medium difficulty, exercises sliding window which you're reinforcing.\n"
        "   Hint: when the window shrinks, what exactly leaves the set?\n"
        "4. *Merge Intervals* — array,sorting — medium — https://leetcode.com/problems/merge-intervals/\n"
        "   Why: medium, pattern you've seen — solidify the sort-then-sweep approach.\n"
        "   Hint: sort first; the sweep is trivial once sorted.\n"
        "5. *Median of Two Sorted Arrays* — array,binary-search,divide-and-conquer — hard — https://leetcode.com/problems/median-of-two-sorted-arrays/\n"
        "   Why: hard, classic — pushes your binary-search lesson to partition-on-array.\n"
        "   Hint: you're binary searching on the partition index, not on a value.\n"
    ),
    "candidates": [
        {
            "slug": "two-sum",
            "title": "Two Sum",
            "url": "https://leetcode.com/problems/two-sum/",
            "tags": "array,hash-map",
            "difficulty": "easy",
            "reasoning": "warmup; targets your 'check empty input' lesson.",
            "coaching_hint": "before writing code, ask: can I trade space for time?",
        },
        {
            "slug": "binary-search",
            "title": "Binary Search",
            "url": "https://leetcode.com/problems/binary-search/",
            "tags": "array,binary-search",
            "difficulty": "easy",
            "reasoning": "reinforces your 'off-by-one on inclusive bounds' lesson.",
            "coaching_hint": "pick your bounds convention (inclusive vs half-open) and stick with it.",
        },
        {
            "slug": "longest-substring-without-repeating-characters",
            "title": "Longest Substring Without Repeating Characters",
            "url": "https://leetcode.com/problems/longest-substring-without-repeating-characters/",
            "tags": "hash-map,two-pointers",
            "difficulty": "medium",
            "reasoning": "medium difficulty, exercises sliding window which you're reinforcing.",
            "coaching_hint": "when the window shrinks, what exactly leaves the set?",
        },
        {
            "slug": "merge-intervals",
            "title": "Merge Intervals",
            "url": "https://leetcode.com/problems/merge-intervals/",
            "tags": "array,sorting",
            "difficulty": "medium",
            "reasoning": "medium, pattern you've seen — solidify the sort-then-sweep approach.",
            "coaching_hint": "sort first; the sweep is trivial once sorted.",
        },
        {
            "slug": "median-of-two-sorted-arrays",
            "title": "Median of Two Sorted Arrays",
            "url": "https://leetcode.com/problems/median-of-two-sorted-arrays/",
            "tags": "array,binary-search,divide-and-conquer",
            "difficulty": "hard",
            "reasoning": "hard, classic — pushes your binary-search lesson to partition-on-array.",
            "coaching_hint": "you're binary searching on the partition index, not on a value.",
        },
    ],
}

_MOCK_COACH: dict[str, Any] = {
    "tutor_feedback": (
        "<b>Correctness:</b> the code works on the happy path but breaks on empty input "
        "(your recurring lesson — check empty input before indexing).\n"
        "<b>Complexity:</b> O(n) time, O(n) space — appropriate for this problem.\n"
        "<b>Style:</b> clear variable names, could use a hashmap comprehension.\n"
        "<b>Pattern:</b> this is a classic hash-map lookup pattern.\n"
        "<b>Next step:</b> add an empty-input guard before your next attempt."
    ),
    "lesson_title": "check empty input before indexing",
    "lesson_category": "defensive-coding",
    "lesson_should_graduate": False,
    "status": "solved",
    "time_spent_min": 18,
}
