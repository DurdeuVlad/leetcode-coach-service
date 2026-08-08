"""V2 LeetCode refresh — routes GraphQL through Browserless /chrome/function.

Browserless v2 requires the /chrome/ path prefix for all Chrome REST
endpoints. The Puppeteer function loads leetcode.com (past Cloudflare with
a real Chrome), then runs the GraphQL fetch from within the page context.

V2 enforces exact-slug canonical metadata: if LeetCode's search doesn't
return an exact titleSlug match, the problem is rejected (not silently
filled with wrong metadata).
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse, urlunparse

import httpx
import structlog
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from leetcode_coach.config import get_settings

log = structlog.get_logger("v2.leetcode")

_RECENT = """query recentAcSubmissions($username:String!,$limit:Int!){recentAcSubmissionList(username:$username,limit:$limit){title titleSlug}}"""
_META = """query problemsetQuestionList($filters:QuestionListFilterInput){problemsetQuestionList:questionList(categorySlug:"",limit:10,skip:0,filters:$filters){questions:data{difficulty title titleSlug topicTags{name slug}}}}"""

# Puppeteer code executed by Browserless's /chrome/function endpoint. It
# loads leetcode.com (getting past Cloudflare with a real Chrome), then
# runs the GraphQL fetch from within the page context. Returns JSON.
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


class LeetCodeV2Error(RuntimeError):
    pass


class _Transient(LeetCodeV2Error):
    pass


@dataclass(frozen=True)
class ProblemRecord:
    slug: str
    title: str
    difficulty: str
    tags: str


def _build_function_url(base: str, token: str) -> str:
    """Build the Browserless v2 /chrome/function URL with token query param.

    Handles BROWSERLESS_URL whether it's given as:
    - bare domain: https://browserless.example.com
    - with /chrome subpath: https://browserless.example.com/chrome
    - with existing path: https://browserless.example.com/chrome/function
    """
    parsed = urlparse(base.rstrip("/"))
    # Always normalize to /chrome/function — strip any existing path.
    path = "/chrome/function"
    url = urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))
    if token:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}token={token}"
    return url


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=2, max=10),
    retry=retry_if_exception_type(_Transient),
    reraise=True,
)
async def _post(query: str, variables: dict) -> dict:
    """POST one GraphQL query through the homelab Browserless /chrome/function."""
    settings = get_settings()
    if not settings.browserless_url or settings.browserless_url == "mock":
        raise LeetCodeV2Error(
            "BROWSERLESS_URL not configured — LeetCode GraphQL requires the "
            "homelab Browserless instance (v2 /chrome/function endpoint)"
        )

    function_url = _build_function_url(settings.browserless_url, settings.browserless_token)
    try:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            response = await client.post(
                function_url,
                json={
                    "code": _BROWSERLESS_CODE,
                    "context": {"query": query, "variables": variables},
                },
            )
    except httpx.TimeoutException as exc:
        raise _Transient(f"browserless timeout: {exc}") from exc
    except httpx.HTTPError as exc:
        raise _Transient(f"browserless http error: {exc}") from exc

    if response.status_code == 429 or response.status_code >= 500:
        raise _Transient(f"browserless /chrome/function HTTP {response.status_code}")
    if response.status_code >= 400:
        raise LeetCodeV2Error(
            f"browserless /chrome/function returned HTTP {response.status_code}: {response.text[:200]}"
        )

    data = response.json()
    if isinstance(data, dict) and "__error" in data:
        raise LeetCodeV2Error(f"browserless graphql fetch failed: {data['__error']}")
    # Browserless wraps the function return in {data: ..., type: ...}
    payload = data.get("data", data) if isinstance(data, dict) else data
    if isinstance(payload, dict) and payload.get("errors"):
        raise LeetCodeV2Error(f"LeetCode GraphQL errors: {payload['errors']}")
    log.info("leetcode_browserless_call_succeeded")
    return payload


async def fetch_recent_solved(limit: int = 20) -> list[ProblemRecord]:
    username = get_settings().leetcode_username
    if not username:
        return []
    recent = await _post(_RECENT, {"username": username, "limit": min(limit, 20)})
    records = []
    for submission in recent.get("data", {}).get("recentAcSubmissionList") or []:
        slug = str(submission["titleSlug"])
        metadata = await _post(_META, {"filters": {"searchKeywords": slug}})
        questions = metadata.get("data", {}).get("problemsetQuestionList", {}).get("questions", [])
        exact = next((row for row in questions if row.get("titleSlug") == slug), None)
        if exact is None:
            raise LeetCodeV2Error(f"No exact canonical metadata match for {slug}")
        records.append(
            ProblemRecord(
                slug=slug,
                title=str(exact["title"]),
                difficulty=str(exact["difficulty"]).lower(),
                tags=",".join(str(tag["slug"]) for tag in exact.get("topicTags", [])),
            )
        )
    return records
