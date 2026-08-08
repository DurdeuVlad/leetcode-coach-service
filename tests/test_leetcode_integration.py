from types import SimpleNamespace

import httpx
import pytest
import respx

from leetcode_coach.integrations import leetcode

_BROWSERLESS_URL = "https://browserless.test/chrome/function"
_SETTINGS = SimpleNamespace(
    leetcode_username="coach",
    browserless_url="https://browserless.test",
    browserless_token="test-token",
)


def _browserless_response(graphql_data: dict) -> httpx.Response:
    """Wrap a LeetCode GraphQL payload in the Browserless function return shape."""
    return httpx.Response(200, json={"data": graphql_data, "type": "application/json"})


@pytest.mark.asyncio
@respx.mock
async def test_refresh_routes_through_browserless_and_exact_slug(monkeypatch) -> None:
    monkeypatch.setattr(leetcode, "get_settings", lambda: _SETTINGS)
    route = respx.post(_BROWSERLESS_URL).mock(
        side_effect=[
            _browserless_response(
                {
                    "data": {
                        "recentAcSubmissionList": [
                            {"title": "House Robber", "titleSlug": "house-robber"}
                        ]
                    }
                }
            ),
            _browserless_response(
                {
                    "data": {
                        "problemsetQuestionList": {
                            "questions": [
                                {
                                    "title": "House Robber",
                                    "titleSlug": "house-robber",
                                    "difficulty": "Easy",
                                    "topicTags": [{"slug": "dynamic-programming"}],
                                }
                            ]
                        }
                    }
                }
            ),
        ]
    )

    records = await leetcode.fetch_recent_solved()

    assert route.call_count == 2
    assert records[0].slug == "house-robber"
    assert records[0].difficulty == "easy"
    assert records[0].tags == "dynamic-programming"
    # Verify the request went to Browserless with the Puppeteer code payload
    body = route.calls[0].request.read()
    assert b"leetcode.com/graphql" in body  # GraphQL URL is inside the Puppeteer code
    assert b"recentAcSubmissions" in body


@pytest.mark.asyncio
@respx.mock
async def test_refresh_rejects_non_exact_metadata_match(monkeypatch) -> None:
    monkeypatch.setattr(leetcode, "get_settings", lambda: _SETTINGS)
    respx.post(_BROWSERLESS_URL).mock(
        side_effect=[
            _browserless_response(
                {
                    "data": {
                        "recentAcSubmissionList": [
                            {"title": "House Robber", "titleSlug": "house-robber"}
                        ]
                    }
                }
            ),
            _browserless_response(
                {
                    "data": {
                        "problemsetQuestionList": {
                            "questions": [{"title": "Rob", "titleSlug": "rob"}]
                        }
                    }
                }
            ),
        ]
    )

    with pytest.raises(leetcode.LeetCodeV2Error, match="exact canonical metadata"):
        await leetcode.fetch_recent_solved()


@pytest.mark.asyncio
async def test_refresh_raises_when_browserless_not_configured(monkeypatch) -> None:
    monkeypatch.setattr(
        leetcode,
        "get_settings",
        lambda: SimpleNamespace(
            leetcode_username="coach",
            browserless_url="",
            browserless_token="",
        ),
    )
    with pytest.raises(leetcode.LeetCodeV2Error, match="BROWSERLESS_URL not configured"):
        await leetcode.fetch_recent_solved()


def test_build_function_url_handles_bare_domain() -> None:
    url = leetcode._build_function_url("https://browserless.example.com", "tok")
    assert url == "https://browserless.example.com/chrome/function?token=tok"


def test_build_function_url_strips_existing_path() -> None:
    url = leetcode._build_function_url("https://browserless.example.com/old/path", "tok")
    assert url == "https://browserless.example.com/chrome/function?token=tok"


def test_build_function_url_no_token() -> None:
    url = leetcode._build_function_url("https://browserless.example.com", "")
    assert url == "https://browserless.example.com/chrome/function"
