"""LLM client tests (#014) — fallback decision table + token counts.

The OpenAI and Gemini SDKs each own their transport internals differently
(the OpenAI SDK is httpx-based; google-genai's async path is not cleanly
respx-mockable). Both are tested here by monkeypatching the SDK client
classes directly (`openai.AsyncOpenAI`, `google.genai.Client`) — this
exercises our exact `LLMClient` exception-classification and fallback
logic (the acceptance criteria in #010/#014) without depending on each
SDK's internal transport.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import openai
import pytest
from google.genai import errors as genai_errors

from leetcode_coach.errors import LLMUnavailableError
from leetcode_coach.integrations import llm as llm_module
from leetcode_coach.integrations.llm import LLMClient


def _openai_success(model: str = "gpt-5.6-sol", text: str = '{"ok": true}'):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
        model=model,
        usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20),
    )


def _openai_http_response(status_code: int) -> httpx.Response:
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    return httpx.Response(status_code, request=request, json={"error": {"message": "boom"}})


def _gemini_http_response(status_code: int):
    import requests

    resp = requests.Response()
    resp.status_code = status_code
    resp._content = b'{"error": {"message": "boom"}}'
    return resp


def _gemini_success(text: str = '{"ok": true}'):
    return SimpleNamespace(
        text=text,
        usage_metadata=SimpleNamespace(
            prompt_token_count=50, candidates_token_count=10, total_token_count=60
        ),
    )


class _FakeOpenAIClient:
    def __init__(self, side_effect):
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=AsyncMock(side_effect=side_effect))
        )


class _FakeGenAIClient:
    def __init__(self, side_effect):
        self.aio = SimpleNamespace(
            models=SimpleNamespace(generate_content=AsyncMock(side_effect=side_effect))
        )


def _patch_openai(monkeypatch: pytest.MonkeyPatch, side_effect) -> AsyncMock:
    fake = _FakeOpenAIClient(side_effect)
    monkeypatch.setattr(openai, "AsyncOpenAI", lambda **kwargs: fake)
    return fake.chat.completions.create


def _patch_gemini(monkeypatch: pytest.MonkeyPatch, side_effect) -> AsyncMock:
    from google import genai

    fake = _FakeGenAIClient(side_effect)
    monkeypatch.setattr(genai, "Client", lambda **kwargs: fake)
    return fake.aio.models.generate_content


@pytest.mark.asyncio
async def test_primary_success_no_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "real-key")
    from leetcode_coach.config import get_settings

    get_settings.cache_clear()

    openai_create = _patch_openai(monkeypatch, side_effect=[_openai_success()])
    gemini_create = _patch_gemini(monkeypatch, side_effect=[_gemini_success()])

    resp = await LLMClient().complete("system", "user")

    assert resp.text == '{"ok": true}'
    assert resp.model == "gpt-5.6-sol"
    assert resp.tokens_in == 100
    assert resp.tokens_out == 20
    openai_create.assert_awaited_once()
    gemini_create.assert_not_awaited()
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_primary_500_retries_then_falls_back_to_gemini(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Primary 500 → retries then falls back to Gemini (#010/#014)."""
    monkeypatch.setenv("OPENAI_API_KEY", "real-key")
    from leetcode_coach.config import get_settings

    get_settings.cache_clear()

    server_error = openai.InternalServerError(
        message="boom", response=_openai_http_response(500), body=None
    )
    openai_create = _patch_openai(monkeypatch, side_effect=[server_error, server_error])
    gemini_create = _patch_gemini(monkeypatch, side_effect=[_gemini_success(text="fallback ran")])

    resp = await LLMClient().complete("system", "user")

    assert resp.text == "fallback ran"
    assert resp.model == "gemini-3.6-flash"
    assert openai_create.await_count == 2  # tenacity stop_after_attempt(2)
    gemini_create.assert_awaited_once()
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_primary_auth_error_no_retry_immediate_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Primary auth error → no retry, immediate fallback (#010)."""
    monkeypatch.setenv("OPENAI_API_KEY", "real-key")
    from leetcode_coach.config import get_settings

    get_settings.cache_clear()

    auth_error = openai.AuthenticationError(
        message="bad key", response=_openai_http_response(401), body=None
    )
    openai_create = _patch_openai(monkeypatch, side_effect=[auth_error])
    gemini_create = _patch_gemini(monkeypatch, side_effect=[_gemini_success()])

    resp = await LLMClient().complete("system", "user")

    assert resp.model == "gemini-3.6-flash"
    openai_create.assert_awaited_once()  # exactly one call — no retry
    gemini_create.assert_awaited_once()
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_primary_bad_request_no_retry_no_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """400/403/404 → no retry, no fallback — raises LLMUnavailableError."""
    monkeypatch.setenv("OPENAI_API_KEY", "real-key")
    from leetcode_coach.config import get_settings

    get_settings.cache_clear()

    bad_request = openai.BadRequestError(
        message="bad request", response=_openai_http_response(400), body=None
    )
    openai_create = _patch_openai(monkeypatch, side_effect=[bad_request])
    gemini_create = _patch_gemini(monkeypatch, side_effect=[_gemini_success()])

    with pytest.raises(LLMUnavailableError):
        await LLMClient().complete("system", "user")

    openai_create.assert_awaited_once()
    gemini_create.assert_not_awaited()
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_both_primary_and_fallback_fail_raises_llm_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "real-key")
    from leetcode_coach.config import get_settings

    get_settings.cache_clear()

    auth_error = openai.AuthenticationError(
        message="bad key", response=_openai_http_response(401), body=None
    )
    _patch_openai(monkeypatch, side_effect=[auth_error])
    server_error = genai_errors.ServerError(500, _gemini_http_response(500))
    _patch_gemini(monkeypatch, side_effect=[server_error, server_error])

    with pytest.raises(LLMUnavailableError):
        await LLMClient().complete("system", "user")
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_mock_mode_returns_canned_response_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "mock")
    from leetcode_coach.config import get_settings

    get_settings.cache_clear()

    resp = await LLMClient().complete("Propose 5 candidate problems", "user")
    assert "candidate_list_markdown" in resp.text
    get_settings.cache_clear()


def test_parse_json_response_strips_code_fences() -> None:
    raw = '```json\n{"a": 1}\n```'
    assert llm_module.parse_json_response(raw) == {"a": 1}
