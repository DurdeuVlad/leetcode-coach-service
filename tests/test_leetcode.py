"""LeetCode GraphQL client tests (#014) — parse + Browserless stub."""

from __future__ import annotations

import httpx
import pytest
import respx

from leetcode_coach.errors import LeetCodeFetchError
from leetcode_coach.integrations import leetcode

_GRAPHQL_URL = "https://leetcode.com/graphql/"


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
async def test_fetch_problems_parses_graphql_response() -> None:
    respx.post(_GRAPHQL_URL).mock(
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
async def test_cloudflare_block_raises_leetcode_fetch_error(caplog) -> None:
    """A non-JSON Cloudflare challenge page must be treated as a block: the
    Browserless stub logs the documented line and re-raises — it must
    never silently succeed."""
    respx.post(_GRAPHQL_URL).mock(
        return_value=httpx.Response(403, html="<html>Attention Required! | Cloudflare</html>")
    )
    with pytest.raises(LeetCodeFetchError):
        await leetcode.fetch_problems("realuser")


@pytest.mark.asyncio
@respx.mock
async def test_retries_transient_500_then_succeeds() -> None:
    route = respx.post(_GRAPHQL_URL).mock(
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
