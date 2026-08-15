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


@pytest.mark.asyncio
@respx.mock
async def test_resolve_exact_problem_accepts_leetcode_url_with_one_browserless_call(
    monkeypatch,
) -> None:
    monkeypatch.setattr(leetcode, "get_settings", lambda: _SETTINGS)
    route = respx.post(_BROWSERLESS_URL).mock(
        return_value=_browserless_response(
            {
                "data": {
                    "problemsetQuestionList": {
                        "questions": [
                            {
                                "title": "Coin Change",
                                "titleSlug": "coin-change",
                                "difficulty": "Medium",
                                "topicTags": [{"slug": "dynamic-programming"}],
                            }
                        ]
                    }
                }
            }
        )
    )

    record = await leetcode.fetch_exact_problem(
        "https://leetcode.com/problems/coin-change/description/?envType=study-plan"
    )

    assert route.call_count == 1
    assert record == leetcode.ProblemRecord(
        slug="coin-change",
        title="Coin Change",
        difficulty="medium",
        tags="dynamic-programming",
    )


@pytest.mark.asyncio
async def test_resolve_exact_problem_rejects_titles_and_non_leetcode_urls() -> None:
    with pytest.raises(leetcode.LeetCodeV2Error, match="exact slug or leetcode.com problem URL"):
        await leetcode.fetch_exact_problem("Coin Change")
    with pytest.raises(leetcode.LeetCodeV2Error, match="exact slug or leetcode.com problem URL"):
        await leetcode.fetch_exact_problem("https://example.com/problems/coin-change/")


def test_build_function_url_handles_bare_domain() -> None:
    url = leetcode._build_function_url("https://browserless.example.com", "tok")
    assert url == "https://browserless.example.com/chrome/function?token=tok"


def test_build_function_url_strips_existing_path() -> None:
    url = leetcode._build_function_url("https://browserless.example.com/old/path", "tok")
    assert url == "https://browserless.example.com/chrome/function?token=tok"


def test_build_function_url_no_token() -> None:
    url = leetcode._build_function_url("https://browserless.example.com", "")
    assert url == "https://browserless.example.com/chrome/function"


@pytest.mark.asyncio
@respx.mock
async def test_fetch_problemset_paginates_medium_and_hard(monkeypatch) -> None:
    monkeypatch.setattr(leetcode, "get_settings", lambda: _SETTINGS)

    def problemset_page(difficulty: str, skip: int, count: int) -> dict:
        questions = [
            {
                "title": f"{difficulty.title()} Problem {skip + i}",
                "titleSlug": f"{difficulty}-problem-{skip + i}",
                "difficulty": difficulty.title(),
                "topicTags": [{"slug": "array"}],
            }
            for i in range(count)
        ]
        return _browserless_response({"data": {"problemsetQuestionList": {"questions": questions}}})

    respx.post(_BROWSERLESS_URL).mock(
        side_effect=[
            problemset_page("medium", 0, 50),  # medium page 1 (full page)
            problemset_page("medium", 50, 30),  # medium page 2 (partial — stop)
            problemset_page("hard", 0, 50),  # hard page 1 (full page)
            problemset_page("hard", 50, 10),  # hard page 2 (partial — stop)
        ]
    )

    records = await leetcode.fetch_problemset(per_difficulty_limit=100, page_size=50)

    assert len(records) == 140  # 80 medium + 60 hard
    assert records[0].slug == "medium-problem-0"
    assert records[0].difficulty == "medium"
    assert records[80].slug == "hard-problem-0"
    assert records[80].difficulty == "hard"


@pytest.mark.asyncio
@respx.mock
async def test_fetch_problemset_respects_per_difficulty_limit(monkeypatch) -> None:
    monkeypatch.setattr(leetcode, "get_settings", lambda: _SETTINGS)

    respx.post(_BROWSERLESS_URL).mock(
        side_effect=[
            _browserless_response(
                {
                    "data": {
                        "problemsetQuestionList": {
                            "questions": [
                                {
                                    "title": f"Medium {i}",
                                    "titleSlug": f"medium-{i}",
                                    "difficulty": "Medium",
                                    "topicTags": [],
                                }
                                for i in range(25)
                            ]
                        }
                    }
                }
            ),
            _browserless_response(
                {
                    "data": {
                        "problemsetQuestionList": {
                            "questions": [
                                {
                                    "title": f"Hard {i}",
                                    "titleSlug": f"hard-{i}",
                                    "difficulty": "Hard",
                                    "topicTags": [],
                                }
                                for i in range(25)
                            ]
                        }
                    }
                }
            ),
        ]
    )

    records = await leetcode.fetch_problemset(per_difficulty_limit=25, page_size=50)

    assert len(records) == 50  # 25 medium + 25 hard
    assert all(r.difficulty in ("medium", "hard") for r in records)


@pytest.mark.asyncio
@respx.mock
async def test_fetch_problemset_stops_on_empty_page(monkeypatch) -> None:
    monkeypatch.setattr(leetcode, "get_settings", lambda: _SETTINGS)

    respx.post(_BROWSERLESS_URL).mock(
        return_value=_browserless_response({"data": {"problemsetQuestionList": {"questions": []}}})
    )

    records = await leetcode.fetch_problemset()

    assert records == []
