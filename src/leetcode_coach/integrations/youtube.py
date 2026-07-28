"""YouTube search via SearXNG (homelab) — replaces YouTube Data API.

Per architecture.md §4: SearXNG is the primary YouTube search path (promoted
from fallback per user decision 2026-07-28). The YouTube Data API is no longer
used — SearXNG queries YouTube via its built-in `youtube` engine and returns
JSON results, eliminating the API key + quota dependency.

Per issue #013: absent `SEARXNG_URL` raises `YouTubeDisabled` — callers treat
this as "skip enrichment", not a hard error. A SearXNG 4xx/5xx after retries
also raises `YouTubeDisabled` (same "feature off" handling); only transient
failures (429/5xx/timeout) retry.

SearXNG JSON API: GET /search?q=QUERY&format=json&engines=youtube
Each result has: url, title, content, engine, category, publishedDate, etc.
We filter for youtube.com/watch URLs and return them.
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


class _TransientYouTubeError(Exception):
    """429/5xx/timeout — retried by tenacity, never escapes `_search`."""


def _is_mock() -> bool:
    url = get_settings().searxng_url
    return not url or url == "mock"


async def search_walkthroughs(problem_title: str, max_results: int = 3) -> list[str]:
    """Search YouTube for walkthrough videos of the problem via SearXNG.

    Returns a list of `https://www.youtube.com/watch?v=...` URLs. Raises
    `YouTubeDisabled` if no SearXNG URL is configured or SearXNG returns a
    permanent error — callers treat this as "skip enrichment".
    """
    if _is_mock():
        log.info("youtube_disabled", reason="no searxng url")
        raise YouTubeDisabled("SEARXNG_URL not configured")

    data = await _search(problem_title, max_results)
    results = data.get("results", [])
    links: list[str] = []
    for r in results:
        url = r.get("url", "")
        # SearXNG youtube engine returns watch URLs; filter defensively
        # in case other engines leak through (e.g. google videos).
        if "youtube.com/watch" in url or "youtu.be/" in url:
            links.append(url)
        if len(links) >= max_results:
            break
    return links


@retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(min=2, max=10),
    retry=retry_if_exception_type(_TransientYouTubeError),
    reraise=True,
)
async def _search(problem_title: str, max_results: int) -> dict:
    settings = get_settings()
    base = settings.searxng_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{base}/search",
                params={
                    "q": f"{problem_title} leetcode walkthrough",
                    "format": "json",
                    "engines": "youtube",
                    "safesearch": 0,
                },
            )
    except httpx.TimeoutException as e:
        raise _TransientYouTubeError(f"searxng search timeout: {e}") from e
    except httpx.HTTPError as e:
        raise _TransientYouTubeError(f"searxng search http error: {e}") from e

    if resp.status_code in (400, 401, 403):
        # SearXNG config error (json format disabled, etc.) — treat as "feature off".
        log.warning("searxng_client_error", status=resp.status_code)
        raise YouTubeDisabled(f"searxng {resp.status_code} — check json format enabled")
    if resp.status_code == 429 or resp.status_code >= 500:
        raise _TransientYouTubeError(f"searxng search HTTP {resp.status_code}")
    if resp.status_code >= 400:
        raise YouTubeDisabled(f"searxng {resp.status_code}: {resp.text}")
    return resp.json()
