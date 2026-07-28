"""LeetCode GraphQL client — fetch + typed parsing for the weekly pool refresh.

Per issue #012: this module *fetches and parses* the user's recent accepted
submissions + enriches them with problem metadata (difficulty, tags) via
LeetCode's undocumented GraphQL endpoint. The DB upsert (`refresh_pool`)
lands in #029 — this module exposes `fetch_problems()` for that to consume.

The endpoint is unauthenticated for all queries used here (verified by
probing 2026-07-27 — see issue #012). Cloudflare sits in front; a block may
surface as an HTML challenge page rather than a clean 4xx/5xx, so responses
are defensively checked for JSON content-type before parsing.

Per architecture.md §12 / roadmap Phase 1: the Browserless fallback is a
**stub** in v1 — on a block we log the exact line below and re-raise
`LeetCodeFetchError`. We never silently succeed with fabricated data.

Mock-aware: if `LEETCODE_USERNAME` is the placeholder `mock` or empty,
returns a small canned problem set so the pool is non-empty for Flow A
testing.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx
import structlog
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from leetcode_coach.errors import LeetCodeFetchError

log = structlog.get_logger("leetcode")

_GRAPHQL_URL = "https://leetcode.com/graphql/"
_HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}

_RECENT_AC_QUERY = """
query recentAcSubmissions($username: String!, $limit: Int!) {
  recentAcSubmissionList(username: $username, limit: $limit) {
    id
    title
    titleSlug
    timestamp
  }
}
"""

_METADATA_QUERY = """
query problemsetQuestionList($categorySlug: String, $limit: Int, $skip: Int, $filters: QuestionListFilterInput) {
  problemsetQuestionList: questionList(
    categorySlug: $categorySlug
    limit: $limit
    skip: $skip
    filters: $filters
  ) {
    total: totalNum
    questions: data {
      difficulty
      title
      titleSlug
      topicTags { name slug }
    }
  }
}
"""


@dataclass
class ProblemRecord:
    """A typed LeetCode problem, ready for upsert into `leetcode_problems`."""

    slug: str
    title: str
    url: str
    difficulty: str
    tags: list[str]


class _TransientLeetCodeError(Exception):
    """429/5xx/timeout — retried by tenacity, never escapes `_post`."""


def _is_mock(username: str) -> bool:
    return not username or username == "mock"


async def fetch_problems(username: str, *, limit: int = 20) -> list[ProblemRecord]:
    """Fetch the user's recent accepted submissions, enriched with metadata.

    Returns typed `ProblemRecord`s (title, slug, url, difficulty, tags). The
    LeetCode `recentAcSubmissionList` query caps `limit` at 20 server-side —
    a known limitation for users who solve >20 problems/week (see #012).

    On a Cloudflare block or malformed response, logs the documented line
    and raises `LeetCodeFetchError` — the Browserless fallback is a stub in
    v1 (architecture.md §12).
    """
    if _is_mock(username):
        return _mock_pool()

    recent = await _post(_RECENT_AC_QUERY, {"username": username, "limit": min(limit, 20)})
    submissions = recent.get("data", {}).get("recentAcSubmissionList") or []

    records: list[ProblemRecord] = []
    for sub in submissions:
        slug = sub["titleSlug"]
        meta = await _fetch_metadata(slug)
        records.append(
            ProblemRecord(
                slug=slug,
                title=sub["title"],
                url=f"https://leetcode.com/problems/{slug}/",
                difficulty=(meta.get("difficulty") or "medium").lower(),
                tags=[t["name"] for t in meta.get("topicTags", [])],
            )
        )
    return records


async def _fetch_metadata(slug: str) -> dict:
    """Look up difficulty/tags for one problem via a targeted search."""
    resp = await _post(
        _METADATA_QUERY,
        {
            "categorySlug": "",
            "limit": 1,
            "skip": 0,
            "filters": {"searchKeywords": slug},
        },
    )
    questions = resp.get("data", {}).get("problemsetQuestionList", {}).get("questions", [])
    for q in questions:
        if q.get("titleSlug") == slug:
            return q
    return questions[0] if questions else {}


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=2, max=10),
    retry=retry_if_exception_type(_TransientLeetCodeError),
    reraise=True,
)
async def _post(query: str, variables: dict) -> dict:
    """POST one GraphQL query, with the Browserless-stub block handling.

    Retries only on transient HTTP (429/5xx/timeout). A Cloudflare block
    surfaces as a non-JSON body or a challenge status — detected and
    treated as a hard failure (Browserless stub, #012/#031).
    """
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                _GRAPHQL_URL,
                json={"query": query, "variables": variables},
                headers=_HEADERS,
            )
    except httpx.TimeoutException as e:
        raise _TransientLeetCodeError(f"leetcode graphql timeout: {e}") from e
    except httpx.HTTPError as e:
        raise _TransientLeetCodeError(f"leetcode graphql http error: {e}") from e

    if resp.status_code == 429 or resp.status_code >= 500:
        raise _TransientLeetCodeError(f"leetcode graphql HTTP {resp.status_code}")
    content_type = resp.headers.get("content-type", "")
    if resp.status_code >= 400 or "application/json" not in content_type:
        # Cloudflare block (HTML challenge page) or other non-JSON failure —
        # this is the Browserless fallback point. Stubbed in v1: log and
        # re-raise rather than silently succeeding with fabricated data.
        log.error("GraphQL failed, Browserless not configured")
        raise LeetCodeFetchError(
            f"leetcode graphql blocked or failed: HTTP {resp.status_code}, "
            f"content-type={content_type!r}"
        )

    data = resp.json()
    if data.get("errors"):
        # e.g. user-not-found — not transient, do not retry.
        log.warning("leetcode_graphql_errors", errors=data["errors"])
    return data


def _mock_pool() -> list[ProblemRecord]:
    """Canned problem set used when `LEETCODE_USERNAME` is unset/`mock`."""
    canned = [
        ("two-sum", "Two Sum", "easy", ["array", "hash-map"]),
        ("binary-search", "Binary Search", "easy", ["array", "binary-search"]),
        (
            "longest-substring-without-repeating-characters",
            "Longest Substring Without Repeating Characters",
            "medium",
            ["hash-map", "two-pointers"],
        ),
        ("merge-intervals", "Merge Intervals", "medium", ["array", "sorting"]),
        (
            "median-of-two-sorted-arrays",
            "Median of Two Sorted Arrays",
            "hard",
            ["array", "binary-search", "divide-and-conquer"],
        ),
    ]
    return [
        ProblemRecord(
            slug=slug,
            title=title,
            url=f"https://leetcode.com/problems/{slug}/",
            difficulty=diff,
            tags=tags,
        )
        for slug, title, diff, tags in canned
    ]
