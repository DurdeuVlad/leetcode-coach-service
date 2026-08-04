from types import SimpleNamespace

import httpx
import pytest
import respx

from leetcode_coach_v2.integrations import leetcode


@pytest.mark.asyncio
@respx.mock
async def test_refresh_uses_direct_graphql_and_exact_slug(monkeypatch) -> None:
    monkeypatch.setattr(
        leetcode, "get_settings", lambda: SimpleNamespace(leetcode_username="coach")
    )
    route = respx.post("https://leetcode.com/graphql/").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "data": {
                        "recentAcSubmissionList": [
                            {"title": "House Robber", "titleSlug": "house-robber"}
                        ]
                    }
                },
            ),
            httpx.Response(
                200,
                json={
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
                },
            ),
        ]
    )

    records = await leetcode.fetch_recent_solved()

    assert route.call_count == 2
    assert records[0].slug == "house-robber"
    assert records[0].difficulty == "easy"
    assert records[0].tags == "dynamic-programming"
    assert b"Browserless" not in route.calls[0].request.content


@pytest.mark.asyncio
@respx.mock
async def test_refresh_rejects_non_exact_metadata_match(monkeypatch) -> None:
    monkeypatch.setattr(
        leetcode, "get_settings", lambda: SimpleNamespace(leetcode_username="coach")
    )
    respx.post("https://leetcode.com/graphql/").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "data": {
                        "recentAcSubmissionList": [
                            {"title": "House Robber", "titleSlug": "house-robber"}
                        ]
                    }
                },
            ),
            httpx.Response(
                200,
                json={
                    "data": {
                        "problemsetQuestionList": {
                            "questions": [{"title": "Rob", "titleSlug": "rob"}]
                        }
                    }
                },
            ),
        ]
    )

    with pytest.raises(leetcode.LeetCodeV2Error, match="exact canonical metadata"):
        await leetcode.fetch_recent_solved()
