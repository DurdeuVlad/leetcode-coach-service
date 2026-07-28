"""YouTube Data API search — optional, v1.

Per architecture.md §12: no SearXNG/Browserless in v1 unless a primary API
actually fails. YouTube search is done BEFORE the LLM call and results are
passed in the prompt (no tool-calling loop in v1).

Per issue #013: absent `YOUTUBE_API_KEY` raises `YouTubeDisabled` — callers
treat this as "skip enrichment", not a hard error. A bad key or exceeded
quota (403 `quotaExceeded`) also raise `YouTubeDisabled` (same "feature
off" handling); only transient failures (429/5xx/timeout) retry.
"""

from __future__ import annotations

import httpx
import structlog
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from leetcode_coach.config import get_settings
from leetcode_coach.errors import YouTubeDisabled

log = structlog.get_logger("youtube")

_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"


class _TransientYouTubeError(Exception):
    """429/5xx/timeout — retried by tenacity, never escapes `_search`."""


def _is_mock() -> bool:
    key = get_settings().youtube_api_key
    return not key or key == "mock"


async def search_walkthroughs(problem_title: str, max_results: int = 3) -> list[str]:
    """Search YouTube for walkthrough videos of the problem.

    Returns a list of `https://www.youtube.com/watch?v=...` URLs. Raises
    `YouTubeDisabled` if no API key is configured, the key is rejected, or
    the daily quota is exceeded — callers treat this as "skip enrichment".
    """
    if _is_mock():
        log.info("youtube_disabled", reason="no api key")
        raise YouTubeDisabled("YOUTUBE_API_KEY not configured")

    data = await _search(problem_title, max_results)
    items = data.get("items", [])
    return [
        f"https://www.youtube.com/watch?v={it['id']['videoId']}"
        for it in items
        if "videoId" in it.get("id", {})
    ]


@retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(min=2, max=10),
    retry=retry_if_exception_type(_TransientYouTubeError),
    reraise=True,
)
async def _search(problem_title: str, max_results: int) -> dict:
    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                _SEARCH_URL,
                params={
                    "part": "snippet",
                    "q": f"{problem_title} leetcode walkthrough",
                    "maxResults": max_results,
                    "type": "video",
                    "key": settings.youtube_api_key,
                },
            )
    except httpx.TimeoutException as e:
        raise _TransientYouTubeError(f"youtube search timeout: {e}") from e
    except httpx.HTTPError as e:
        raise _TransientYouTubeError(f"youtube search http error: {e}") from e

    if resp.status_code == 403:
        # quotaExceeded (or other 403) — quota won't reset mid-call; treat
        # as "feature off" rather than retrying.
        log.warning("youtube_quota_or_forbidden", status=403)
        raise YouTubeDisabled("youtube 403 — quota exceeded or forbidden")
    if resp.status_code in (400, 401):
        # Bad key/request — effectively "feature off".
        log.warning("youtube_bad_request_or_key", status=resp.status_code)
        raise YouTubeDisabled(f"youtube {resp.status_code} — bad request or key")
    if resp.status_code == 429 or resp.status_code >= 500:
        raise _TransientYouTubeError(f"youtube search HTTP {resp.status_code}")
    if resp.status_code >= 400:
        raise YouTubeDisabled(f"youtube {resp.status_code}: {resp.text}")
    return resp.json()
