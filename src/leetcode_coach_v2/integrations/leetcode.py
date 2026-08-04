"""V2 LeetCode refresh through Browserless with exact-slug metadata checks."""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from leetcode_coach_v2.config import get_settings

_RECENT = """query recentAcSubmissions($username:String!,$limit:Int!){recentAcSubmissionList(username:$username,limit:$limit){title titleSlug}}"""
_META = """query problemsetQuestionList($filters:QuestionListFilterInput){problemsetQuestionList:questionList(categorySlug:"",limit:10,skip:0,filters:$filters){questions:data{difficulty title titleSlug topicTags{name slug}}}}"""
_CODE = """
export default async ({ page, context }) => {
  await page.goto('https://leetcode.com', { waitUntil: 'networkidle2', timeout: 30000 });
  const result = await page.evaluate(async (query, variables) => {
    const response = await fetch('https://leetcode.com/graphql/', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({query, variables})
    });
    return await response.json();
  }, context.query, context.variables);
  return {data: result, type: 'application/json'};
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


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=2, max=10),
    retry=retry_if_exception_type(_Transient),
    reraise=True,
)
async def _post(query: str, variables: dict) -> dict:
    settings = get_settings()
    if not settings.browserless_url:
        raise LeetCodeV2Error("BROWSERLESS_URL is required for LeetCode refresh")
    url = settings.browserless_url.rstrip("/") + "/function"
    if settings.browserless_token:
        url += f"?token={settings.browserless_token}"
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                url, json={"code": _CODE, "context": {"query": query, "variables": variables}}
            )
    except httpx.HTTPError as exc:
        raise _Transient("Browserless transport failure") from exc
    if response.status_code == 429 or response.status_code >= 500:
        raise _Transient(f"Browserless HTTP {response.status_code}")
    if response.status_code >= 400:
        raise LeetCodeV2Error(f"Browserless HTTP {response.status_code}: {response.text[:200]}")
    payload = response.json()
    if payload.get("errors"):
        raise LeetCodeV2Error(f"LeetCode GraphQL errors: {payload['errors']}")
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
