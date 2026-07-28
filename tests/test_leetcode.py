"""LeetCode GraphQL client tests (#014) — Browserless primary path.

Per the 2026-07-28 decision (docs/business-requirements.md §8 #4,
architecture.md §12), Browserless is the sole path for LeetCode GraphQL.
These tests mock the homelab Browserless `/function` endpoint — the direct
leetcode.com/graphql/ path is no longer exercised by the code.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from leetcode_coach.config import get_settings
from leetcode_coach.errors import LeetCodeFetchError
from leetcode_coach.integrations import leetcode

_BROWSERLESS_URL = "https://browserless.example.com"
_FUNCTION_URL = f"{_BROWSERLESS_URL}/function"


@pytest.fixture
def browserless_configured(monkeypatch: pytest.MonkeyPatch):
    """Configure BROWSERLESS_URL for tests that exercise the live path."""
    monkeypatch.setenv("BROWSERLESS_URL", _BROWSERLESS_URL)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def browserless_unset(monkeypatch: pytest.MonkeyPatch):
    """Clear BROWSERLESS_URL for tests that assert the not-configured branch."""
    monkeypatch.setenv("BROWSERLESS_URL", "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _recent_ac_response():
    return {
        "data": {
            "recentAcSubmissionList": [
                {"id": "1", "title": "Two Sum", "titleSlug": "two-sum", "timestamp": "1690000000"}
            ]
        }
    }


def _metadata_response():
    return {
        "data": {
            "problemsetQuestionList": {
                "total": 1,
                "questions": [
                    {
                        "difficulty": "Easy",
                        "title": "Two Sum",
                        "titleSlug": "two-sum",
                        "topicTags": [{"name": "Array", "slug": "array"}],
                    }
                ],
            }
        }
    }


@pytest.mark.asyncio
@respx.mock
async def test_fetch_problems_parses_graphql_response(browserless_configured) -> None:
    respx.post(_FUNCTION_URL).mock(
        side_effect=[
            httpx.Response(200, json=_recent_ac_response()),
            httpx.Response(200, json=_metadata_response()),
        ]
    )
    records = await leetcode.fetch_problems("realuser", limit=5)
    assert len(records) == 1
    rec = records[0]
    assert rec.slug == "two-sum"
    assert rec.title == "Two Sum"
    assert rec.url == "https://leetcode.com/problems/two-sum/"
    assert rec.difficulty == "easy"
    assert rec.tags == ["Array"]


@pytest.mark.asyncio
@respx.mock
async def test_browserless_not_configured_raises(browserless_unset) -> None:
    """When BROWSERLESS_URL is unset, fetch_problems raises
    LeetCodeFetchError immediately — no HTTP call is attempted."""
    with pytest.raises(LeetCodeFetchError, match="BROWSERLESS_URL not configured"):
        await leetcode.fetch_problems("realuser")


@pytest.mark.asyncio
@respx.mock
async def test_browserless_4xx_raises_fetch_error(browserless_configured) -> None:
    """A 4xx (non-transient) from Browserless /function raises
    LeetCodeFetchError — not retried, not silently swallowed."""
    respx.post(_FUNCTION_URL).mock(return_value=httpx.Response(400, text="bad code"))
    with pytest.raises(LeetCodeFetchError, match="HTTP 400"):
        await leetcode.fetch_problems("realuser")


@pytest.mark.asyncio
@respx.mock
async def test_browserless_graphql_error_field_raises(browserless_configured) -> None:
    """If the Puppeteer wrapper returns `{__error: ...}` (the in-page fetch
    got a non-JSON body), raise LeetCodeFetchError."""
    respx.post(_FUNCTION_URL).mock(
        return_value=httpx.Response(200, json={"__error": "cloudflare challenge page"})
    )
    with pytest.raises(LeetCodeFetchError, match="graphql fetch failed"):
        await leetcode.fetch_problems("realuser")


@pytest.mark.asyncio
@respx.mock
async def test_retries_transient_500_then_succeeds(browserless_configured) -> None:
    route = respx.post(_FUNCTION_URL).mock(
        side_effect=[
            httpx.Response(500),
            httpx.Response(200, json=_recent_ac_response()),
            httpx.Response(200, json=_metadata_response()),
        ]
    )
    records = await leetcode.fetch_problems("realuser")
    assert len(records) == 1
    assert route.call_count == 3


@pytest.mark.asyncio
async def test_mock_username_returns_canned_pool() -> None:
    records = await leetcode.fetch_problems("mock")
    assert len(records) >= 1
    assert all(r.slug for r in records)
