"""LeetCode GraphQL client — fetch + typed parsing for the weekly pool refresh.

Per issue #012: this module *fetches and parses* the user's recent accepted
submissions + enriches them with problem metadata (difficulty, tags) via
LeetCode's undocumented GraphQL endpoint. `refresh_pool()` (Phase 5, #029)
consumes `fetch_problems()` and upserts into `leetcode_problems`.

**Browserless is the primary and only path** (per the 2026-07-28 decision —
see `docs/business-requirements.md` §8 #4 and `docs/architecture.md` §12).
Cloudflare's 2026 bot detection blocks unauthenticated programmatic GraphQL
from datacenter/homelab IPs as a matter of course; running the same
`fetch()` from within a real Chrome page context (Browserless `/function`)
is the robust default. The direct httpx code path is removed. If
`BROWSERLESS_URL` is not configured, raise `LeetCodeFetchError` — never
attempt a direct call.

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

from leetcode_coach.config import get_settings
from leetcode_coach.db.base import get_session
from leetcode_coach.db.models import LeetCodeProblem
from leetcode_coach.errors import LeetCodeFetchError

log = structlog.get_logger("leetcode")

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

    All GraphQL calls go through the homelab Browserless instance
    (`docs/business-requirements.md` §8 #4). If Browserless is not
    configured or fails, raises `LeetCodeFetchError`.
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


# --- Browserless primary path --------------------------------------------

# Puppeteer code executed by Browserless's /function endpoint. It loads
# leetcode.com (getting past Cloudflare with a real Chrome), then runs the
# GraphQL fetch from within the page context. Returns JSON.
_BROWSERLESS_CODE = """
export default async ({ page, context }) => {
  await page.goto('https://leetcode.com', { waitUntil: 'networkidle2', timeout: 30000 });
  const result = await page.evaluate(async (query, variables) => {
    const resp = await fetch('https://leetcode.com/graphql/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query, variables }),
    });
    const text = await resp.text();
    try { return JSON.parse(text); } catch { return { __error: text.slice(0, 500) }; }
  }, context.query, context.variables);
  return { data: result, type: 'application/json' };
};
"""


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=2, max=10),
    retry=retry_if_exception_type(_TransientLeetCodeError),
    reraise=True,
)
async def _post(query: str, variables: dict) -> dict:
    """POST one GraphQL query through the homelab Browserless `/function`
    endpoint.

    Browserless is the primary and only path (per the 2026-07-28 decision —
    `docs/business-requirements.md` §8 #4, `docs/architecture.md` §12).
    The browser loads leetcode.com, then executes the GraphQL `fetch()`
    from within the page context — Cloudflare sees a real Chrome request.

    Retries only on transient Browserless HTTP (429/5xx/timeout). If
    `BROWSERLESS_URL` is not configured, raises `LeetCodeFetchError`
    immediately — never attempts a direct httpx call.
    """
    settings = get_settings()
    browserless_url = settings.browserless_url
    if not browserless_url or browserless_url == "mock":
        log.error("browserless_not_configured", hint="Set BROWSERLESS_URL in .env")
        raise LeetCodeFetchError(
            "BROWSERLESS_URL not configured — LeetCode GraphQL requires the "
            "homelab Browserless instance (per architecture.md §12)"
        )

    base = browserless_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{base}/function",
                json={
                    "code": _BROWSERLESS_CODE,
                    "context": {"query": query, "variables": variables},
                },
            )
    except httpx.TimeoutException as e:
        raise _TransientLeetCodeError(f"browserless timeout: {e}") from e
    except httpx.HTTPError as e:
        raise _TransientLeetCodeError(f"browserless http error: {e}") from e

    if resp.status_code == 429 or resp.status_code >= 500:
        raise _TransientLeetCodeError(f"browserless /function HTTP {resp.status_code}")
    if resp.status_code >= 400:
        raise LeetCodeFetchError(
            f"browserless /function returned HTTP {resp.status_code}: {resp.text[:200]}"
        )

    data = resp.json()
    if isinstance(data, dict) and "__error" in data:
        raise LeetCodeFetchError(f"browserless graphql fetch failed: {data['__error']}")
    if data.get("errors"):
        # e.g. user-not-found — not transient, do not retry.
        log.warning("leetcode_graphql_errors", errors=data["errors"])
    log.info("leetcode_browserless_call_succeeded")
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


async def refresh_pool() -> int:
    """Fetch the user's recent accepted submissions and upsert into
    `leetcode_problems` (FR-4.1).

    The upsert preserves user-tracked state on existing rows:
    - `solved` — never reset to false by a refresh (a problem solved once
      stays solved; the LeetCode AC list only grows).
    - `times_attempted` — never decremented.
    - `last_attempted` — never overwritten with None.

    For new rows (slug not in DB): inserted with `solved = true` (the user
    has an AC submission, per the recent-ac list we just fetched) and
    `times_attempted = 1`.

    Returns the number of rows upserted (inserts + updates). On a fetch
    failure, `fetch_problems` raises `LeetCodeFetchError` (e.g. Browserless
    unavailable — FR-4.2 / architecture.md §12). The caller
    (`_safe_refresh_pool` in cron.py) wraps this in the #008 alert handler.
    """
    settings = get_settings()
    records = await fetch_problems(settings.leetcode_username)
    upserted = 0
    with next(get_session()) as session:
        for rec in records:
            existing = session.get(LeetCodeProblem, rec.slug)
            if existing is None:
                # New problem: the user has an AC submission for it, so
                # mark solved=true + times_attempted=1.
                session.add(
                    LeetCodeProblem(
                        slug=rec.slug,
                        title=rec.title,
                        url=rec.url,
                        difficulty=rec.difficulty,
                        tags=",".join(rec.tags),
                        solved=True,
                        times_attempted=1,
                    )
                )
            else:
                # Existing row: refresh metadata, preserve user state.
                # `solved` only ever flips false→true (an AC submission
                # confirms solved); never true→false.
                existing.title = rec.title
                existing.url = rec.url
                existing.difficulty = rec.difficulty
                existing.tags = ",".join(rec.tags)
                if not existing.solved:
                    existing.solved = True
                session.add(existing)
            upserted += 1
        session.commit()
    log.info("leetcode_refresh_done", upserted=upserted, username=settings.leetcode_username)
    return upserted
