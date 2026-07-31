"""Unit tests for the external-service connectivity probes.

Each probe is tested in mock/disabled mode (no network) and with a stubbed
httpx transport (no real HTTP). The goal is to verify the probe logic —
mock detection, disabled detection, status mapping — not to test the
external services themselves.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest
import respx

from leetcode_coach.integrations.connectivity import (
    ProbeResult,
    _probe_browserless,
    _probe_gemini,
    _probe_openai,
    _probe_searxng,
    _probe_telegram,
    ping_all,
    render_probe_table,
)


def _settings(**overrides: str) -> SimpleNamespace:
    """Build a fake settings object with sensible mock defaults."""
    base = SimpleNamespace(
        telegram_bot_token="mock",
        telegram_chat_id="123456",
        openai_api_key="mock",
        openai_model="gpt-test",
        gemini_api_key="mock",
        gemini_model="gemini-test",
        browserless_url="",
        browserless_token="",
        searxng_url="",
    )
    for k, v in overrides.items():
        setattr(base, k, v)
    return base


# ---------------------------------------------------------------------------
# Mock / disabled detection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_telegram_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    from leetcode_coach.integrations import connectivity

    monkeypatch.setattr(connectivity, "get_settings", lambda: _settings())
    r = await _probe_telegram()
    assert r.status == "mock"


@pytest.mark.asyncio
async def test_openai_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    from leetcode_coach.integrations import connectivity

    monkeypatch.setattr(connectivity, "get_settings", lambda: _settings())
    r = await _probe_openai()
    assert r.status == "mock"


@pytest.mark.asyncio
async def test_gemini_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    from leetcode_coach.integrations import connectivity

    monkeypatch.setattr(connectivity, "get_settings", lambda: _settings())
    r = await _probe_gemini()
    assert r.status == "mock"


@pytest.mark.asyncio
async def test_browserless_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from leetcode_coach.integrations import connectivity

    monkeypatch.setattr(connectivity, "get_settings", lambda: _settings())
    r = await _probe_browserless()
    assert r.status == "disabled"


@pytest.mark.asyncio
async def test_searxng_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from leetcode_coach.integrations import connectivity

    monkeypatch.setattr(connectivity, "get_settings", lambda: _settings())
    r = await _probe_searxng()
    assert r.status == "disabled"


# ---------------------------------------------------------------------------
# Real-credential probes with stubbed HTTP (respx)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_telegram_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    from leetcode_coach.integrations import connectivity

    monkeypatch.setattr(
        connectivity,
        "get_settings",
        lambda: _settings(telegram_bot_token="123:abc"),
    )
    respx.get("https://api.telegram.org/bot123:abc/getMe").mock(
        return_value=httpx.Response(200, json={"result": {"username": "test_bot"}})
    )
    r = await _probe_telegram()
    assert r.status == "ok"
    assert r.detail == "@test_bot"


@pytest.mark.asyncio
@respx.mock
async def test_telegram_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    from leetcode_coach.integrations import connectivity

    monkeypatch.setattr(
        connectivity,
        "get_settings",
        lambda: _settings(telegram_bot_token="123:abc"),
    )
    respx.get("https://api.telegram.org/bot123:abc/getMe").mock(
        return_value=httpx.Response(401)
    )
    r = await _probe_telegram()
    assert r.status == "unreachable"
    assert "401" in r.detail


@pytest.mark.asyncio
@respx.mock
async def test_openai_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    from leetcode_coach.integrations import connectivity

    monkeypatch.setattr(
        connectivity,
        "get_settings",
        lambda: _settings(openai_api_key="sk-real", openai_model="gpt-test"),
    )
    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": []})
    )
    r = await _probe_openai()
    assert r.status == "ok"
    assert r.detail == "gpt-test"


@pytest.mark.asyncio
@respx.mock
async def test_browserless_ok_with_token(monkeypatch: pytest.MonkeyPatch) -> None:
    from leetcode_coach.integrations import connectivity

    monkeypatch.setattr(
        connectivity,
        "get_settings",
        lambda: _settings(
            browserless_url="https://chrome.browserless.io",
            browserless_token="abc123",
        ),
    )
    respx.get("https://chrome.browserless.io?token=abc123").mock(
        return_value=httpx.Response(200)
    )
    r = await _probe_browserless()
    assert r.status == "ok"


@pytest.mark.asyncio
@respx.mock
async def test_searxng_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    from leetcode_coach.integrations import connectivity

    monkeypatch.setattr(
        connectivity,
        "get_settings",
        lambda: _settings(searxng_url="https://search.example.com"),
    )
    respx.get("https://search.example.com/search").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    r = await _probe_searxng()
    assert r.status == "ok"


# ---------------------------------------------------------------------------
# ping_all + rendering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ping_all_returns_all_five(monkeypatch: pytest.MonkeyPatch) -> None:
    from leetcode_coach.integrations import connectivity

    monkeypatch.setattr(connectivity, "get_settings", lambda: _settings())
    results = await ping_all()
    assert len(results) == 5
    names = [r.name for r in results]
    assert names == ["telegram", "openai", "gemini", "browserless", "searxng"]


def test_render_probe_table() -> None:
    results = [
        ProbeResult("telegram", "ok", "@bot"),
        ProbeResult("openai", "mock"),
        ProbeResult("searxng", "unreachable", "timeout"),
    ]
    table = render_probe_table(results)
    assert "telegram" in table
    assert "openai" in table
    assert "searxng" in table
    assert "@bot" in table
    assert "timeout" in table
