# #012 — LeetCode GraphQL client (+ Browserless stub)

**Milestone:** M1 integrations · **Labels:** `type:feature` `area:integrations` `prio:P1`
**Depends on:** #002, #003, #008

## Summary
Client that pulls the user's problem history from the LeetCode GraphQL API for
the weekly pool refresh, with a documented (but stubbed) Browserless fallback.

## Context
- `docs/business-requirements.md` FR-4: weekly pull → upsert into
  `leetcode_problems`; Browserless fallback **only** if GraphQL is
  rate-limited/blocked, and **only if available**.
- `docs/roadmap.md` Phase 1: stub the Browserless fallback — log
  "GraphQL failed, Browserless not configured" and re-raise for now.
- `docs/architecture.md` §12: Browserless/SearXNG code paths are stubs in v1.

## Tasks
- [ ] `integrations/leetcode.py`:
  - `fetch_problems(username) -> list[ProblemRecord]` via GraphQL (`httpx`).
  - `refresh_pool()` upsert semantics are implemented in #029; this issue
    delivers the fetch + typed parsing.
  - `tenacity` retry on transient HTTP only.
- [ ] Browserless fallback = stub: on GraphQL block, log the exact message
      above and re-raise `LeetCodeFetchError`.

## Acceptance criteria
- [ ] `fetch_problems` returns typed records (title, slug, url, difficulty,
      tags) from a mocked GraphQL response (tested in #014).
- [ ] On a simulated block, the Browserless stub logs the documented line and
      re-raises `LeetCodeFetchError` — it does **not** silently succeed.

## Principles (how they apply here)
See [#034](034-engineering-principles-layers.md). KISS is the tie-breaker.
- **YAGNI:** the Browserless fallback is a *stub* in v1 — a real headless
  browser path isn't built until a block actually happens (#031).
- **Fail loud:** on a block the stub logs the documented line and re-raises
  `LeetCodeFetchError` — it must never fake a successful pull.
- **Single Responsibility:** this issue only *fetches + parses*; the DB upsert
  and scheduling live in #029. No DB writes of business state here.
- **KISS:** one `httpx` GraphQL call with transient-only retries.

## External API reference (probed 2026-07-27 — read before implementing)

**Important caveat:** LeetCode's GraphQL API is **undocumented and
unofficial** — there is no public API reference from LeetCode. The queries
and response shapes below were **verified by probing the live endpoint**
on 2026-07-27 (POST `https://leetcode.com/graphql/` with each query). The
API can change without notice; pin the exact operation names and field
selections in code so a break is loud, not silent.

### Endpoint (verified)

- **URL:** `https://leetcode.com/graphql/` (single endpoint, POST).
- **Headers:** `Content-Type: application/json`. A browser-like
  `User-Agent` is required — bare `httpx` default UA gets challenged by
  Cloudflare. Set `User-Agent: Mozilla/5.0 ...` or similar.
- **Auth:** **All three queries below work unauthenticated** (verified by
  probing without cookies). The `LEETCODE_SESSION` cookie is only needed
  for private data (a user's full submission history, private profile
  fields). For our v1 use case (public recent AC submissions, problem
  metadata, public solved counts) **no auth cookie is required** — this
  resolves the open question below.
- **Body:** `{"query": "...", "variables": {...}, "operationName": "..."}`.
  `operationName` is optional but recommended.
- **Cookies:** The server auto-sets a `csrftoken` cookie on every
  response. For unauthenticated queries we do **not** need to send it
  back. For authenticated queries (out of v1 scope) we'd need to echo it
  in an `x-csrftoken` header.
- **Cloudflare is in front** (`Server: cloudflare`, `cf-cache-status:
  DYNAMIC`). Implications:
  - Responses are never cached at the edge — every request hits LeetCode's
    backend.
  - **No `X-RateLimit-*` headers are exposed** — we cannot read remaining
    quota from headers; must use a client-side limiter.
  - Blocking may surface as a **Cloudflare 403 challenge page (HTML, not
    JSON)** or HTTP 1015, not a clean application-level 429. The client
    must detect non-JSON 4xx/5xx bodies and treat them as a block, not
    crash trying to `response.json()`.

### Queries (all three probed and verified working 2026-07-27)

Community references (no official docs exist):
- https://github.com/akarsh1995/leetcode-graphql-queries
- https://github.com/yerass11/Leetcode-Stats-API
- https://github.com/JacobLinCool/LeetCode-Query (TypeScript, has rate
  limiter — useful reference for the default 20 req / 10 sec limit).

**1. Recent accepted submissions** (for the weekly pool refresh — FR-4):

```graphql
query recentAcSubmissions($username: String!, $limit: Int!) {
  recentAcSubmissionList(username: $username, limit: $limit) {
    id
    title
    titleSlug
    timestamp
  }
}
```

**Verified response shape** (probed with a non-existent username → empty
array; real usernames return populated entries):

```json
{"data":{"recentAcSubmissionList":[
  {"id":"123","title":"Two Sum","titleSlug":"two-sum","timestamp":"1690000000"}
]}}
```

- `limit` max is **20** (server-enforced). For a weekly refresh this is
  likely insufficient if the user solves > 20 problems/week — note this
  as a known limitation; the full-history endpoint requires `leetcode.cn`
  and is out of scope for v1.
- `timestamp` is a Unix epoch **string** (not int); convert to `DATE` for
  the DB.
- `titleSlug` is the URL-stable identifier
  (`https://leetcode.com/problems/{titleSlug}/`).
- Non-existent username → `{"data":{"recentAcSubmissionList":[]}}` (empty,
  no error). Distinguish "no recent submissions" from "user not found" —
  use `userPublicProfile` (#3 below) to validate the username first if
  needed.

**2. Problem metadata** (difficulty, tags — needed to enrich the pool):

```graphql
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
      frontendQuestionId: questionFrontendId
      title
      titleSlug
      topicTags { name slug }
    }
  }
}
```

**Verified response shape** (probed with `limit:2` → real data):

```json
{"data":{"problemsetQuestionList":{"total":4003,"questions":[
  {"difficulty":"Easy","frontendQuestionId":"1","title":"Two Sum",
   "titleSlug":"two-sum","topicTags":[{"name":"Array","slug":"array"},
   {"name":"Hash Table","slug":"hash-table"}]},
  {"difficulty":"Medium","frontendQuestionId":"2","title":"Add Two Numbers",
   "titleSlug":"add-two-numbers","topicTags":[{"name":"Linked List","slug":"linked-list"},
   {"name":"Math","slug":"math"},{"name":"Recursion","slug":"recursion"}]}
]}}}
```

- `total` was **4003** at probe time — useful as a sanity check that the
  endpoint is healthy (a sudden drop to 0 or 50 would indicate a schema
  break or block).
- Use `filters: {searchKeywords: $titleSlug}` to fetch metadata for a
  specific problem, or fetch the full problemset in pages of 50 and
  match locally. **Prefer the targeted search** — smaller payload, lower
  chance of tripping rate limits.

**3. User profile / solved counts** (optional, for stats):

```graphql
query userPublicProfile($username: String!) {
  matchedUser(username: $username) {
    username
    submitStats: submitStatsGlobal {
      acSubmissionNum { difficulty count }
    }
  }
}
```

**Verified response shape** (probed with username `"leetcode"` → real
data; non-existent username → structured error):

```json
{"data":{"matchedUser":{"username":"LeetCode","submitStats":{"acSubmissionNum":[
  {"difficulty":"All","count":45},
  {"difficulty":"Easy","count":12},
  {"difficulty":"Medium","count":22},
  {"difficulty":"Hard","count":11}
]}}}}
```

For a non-existent username, the server returns:

```json
{"errors":[{"message":"That user does not exist.","locations":[{"line":1,"column":47}],
  "path":["matchedUser"],"extensions":{"handled":true,"skip_sentry":true}}],
 "data":{"matchedUser":null}}
```

- **Error handling rule:** always inspect `response.json()["errors"]`. A
  non-empty `errors` array with `data.matchedUser: null` means "user not
  found" — raise a typed `LeetCodeUserNotFoundError` (or just log and
  skip, depending on flow). This is **not** a transient failure — do not
  retry. Distinguish from a network/5xx error which *should* retry.

### Rate limiting & blocking behavior

- LeetCode does not publish rate limits and exposes no rate-limit headers
  (verified — no `X-RateLimit-*` in the response). Community consensus:
  ~20 req / 10 sec before soft-block. Use a token-bucket limiter in the
  client (e.g. `aiolimiter`) — **do not** rely on tenacity alone here,
  because a block from LeetCode/Cloudflare is often a long block, not a
  quick retry.
- On HTTP 429: **do not retry blindly.** Log, raise
  `LeetCodeFetchError`, and let the flow decide. The Browserless stub
  (below) is the documented v1 fallback.
- On a **non-JSON 4xx/5xx response** (Cloudflare challenge HTML page):
  detect via `Content-Type` header not containing `application/json`, log
  the first 200 chars of the body for diagnosis, raise
  `LeetCodeFetchError`. Do not attempt to parse as JSON.
- LeetCode may return HTTP 200 with an `errors` array in the GraphQL
  body — always inspect `response.json()["errors"]` and raise on
  non-empty (see the `userPublicProfile` example above).

### Browserless fallback (stubbed in v1)

Per `docs/architecture.md` §12 and `docs/roadmap.md` Phase 1, the
Browserless path is a **stub**: on GraphQL block, log the exact line
`"GraphQL failed, Browserless not configured"` and re-raise
`LeetCodeFetchError`. **Never fake a successful pull** — that would
reintroduce the "log with estimated defaults" anti-pattern (NFR-1 layer 2).
A real Browserless implementation is Phase 7 / #031, only if a block
actually happens in production.

### Open questions to resolve during implementation
- [x] ~~Does the operator need `LEETCODE_SESSION`?~~ **Resolved by probing:
      no — all three v1 queries work unauthenticated.** Only add
      `LEETCODE_SESSION` to `.env.example` if a future phase needs private
      data (full submission history).
- [ ] Confirm the 20-submission `limit` cap is acceptable for the weekly
      refresh; if not, document the gap and consider the `leetcode.cn`
      full-history endpoint as a Phase 7 follow-up.
- [ ] Decide whether to fetch problem metadata per-problem (targeted
      `searchKeywords`) or in bulk (paged `problemsetQuestionList`). The
      per-problem path is simpler and lower-volume — start there.
- [ ] Pick a rate-limiter library (`aiolimiter` is the common choice) and
      add it to `pyproject.toml` only if needed — don't speculatively
      depend on it.
- [ ] Set a browser-like `User-Agent` in the `httpx` client config (bare
      httpx UA gets Cloudflare-challenged — confirmed during probing).

## Notes
- Actual weekly scheduling + upsert lives in #029.
