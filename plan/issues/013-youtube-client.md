# #013 — YouTube client (optional)

**Milestone:** M1 integrations · **Labels:** `type:feature` `area:integrations` `prio:P1`
**Depends on:** #002, #008

## Summary
Optional YouTube Data API search used to enrich coach output with walkthrough
links. Disabled cleanly when no API key is configured.

## Context
- `docs/architecture.md` §3 lists `integrations/youtube.py`
  `search_walkthroughs(title)`; §12: **no LLM tool-calling loop** — if enabled,
  the search runs **before** the LLM call and results are passed in the prompt.
- `docs/business-requirements.md` §8.3: SearXNG replacement is deferred; at
  ~1 search/day the quota is a non-issue.
- Absent `YOUTUBE_API_KEY` ⇒ feature disabled (raise `YouTubeDisabled`).

## Tasks
- [ ] `integrations/youtube.py` — `search_walkthroughs(title) -> list[link]`.
- [ ] Raise `YouTubeDisabled` immediately if `YOUTUBE_API_KEY` is unset;
      callers treat this as "skip enrichment", not an error.
- [ ] `tenacity` retry on transient HTTP only.

## Acceptance criteria
- [ ] No API key ⇒ `search_walkthroughs` raises `YouTubeDisabled`.
- [ ] With a mocked key + response, returns a small list of links (tested in
      #014).
- [ ] Callers never crash when the feature is disabled.

## Principles (how they apply here)
See [#034](034-engineering-principles-layers.md). KISS is the tie-breaker.
- **YAGNI:** optional feature; no SearXNG fallback and no tool-calling loop in
  v1 (architecture §12). Search runs *before* the LLM call, results passed in.
- **KISS:** absent key ⇒ raise `YouTubeDisabled`; callers treat it as "skip",
  so the disabled path is the simplest possible.
- **Single Responsibility:** this client only searches YouTube; it does not
  decide whether the coach should use the links (that's the flow/prompt).

## External API reference (read before implementing)

**Primary source:** YouTube Data API v3 — search.list method
https://developers.google.com/youtube/v3/docs/search/list
**Quota calculator:** https://developers.google.com/youtube/v3/determine_quota_cost
**Python client:** `google-api-python-client` (or `httpx` direct — see below).

### Endpoint to call

- **`GET https://www.googleapis.com/youtube/v3/search`** (`search.list`)
- **Required parameters:**
  - `part=snippet` (the only valid value for search).
  - `q=<problem title>` — the search query. URL-encode; supports `|` (OR)
    and `-` (exclude) operators but we don't need them.
  - `type=video` — restrict to videos (default returns channels + playlists
    too, which we don't want for walkthroughs).
  - `maxResults=3` — keep the pool small; the coach prompt only needs a
    few links.
  - `order=relevance` (default) — or `viewCount` if we want popular
    walkthroughs specifically.
- **Auth:** API key as `?key={YOUTUBE_API_KEY}` (no OAuth needed for
  public search). Set the header `x-goog-api-key` instead of the query
  param if using `httpx` direct — both work, header is cleaner.

### Response shape (fields we use)

```
items[]:
  id.videoId        → "dQw4w9WgXcQ"  (the 11-char video ID)
  snippet.title     → "Two Sum - LeetCode 1 - Python"
  snippet.thumbnails.default.url  → thumbnail (optional, for the prompt)
```

Construct the walkthrough URL as `https://www.youtube.com/watch?v={id.videoId}`.
Return `list[str]` of these URLs (per the issue summary) — the coach
prompt (#023) just needs the links, not titles.

Full response reference:
https://developers.google.com/youtube/v3/docs/search#resource

### Quota (this is the real constraint)

- **Default daily quota: 10,000 units** across all YouTube Data API
  methods, **plus a separate `search.list` bucket of 100 calls/day**.
- **`search.list` costs 100 units per call** (not 1 — the quota calculator
  table is misleading; the 100-unit cost is documented at the top of the
  search.list page: "Quota impact: 100 calls per day. A call to this
  method has a quota cost of 1 unit in the Search Queries quota bucket."
  The "1 unit" is in the *search bucket*, but the *global* unit cost is
  100).
- At ~1 search/day (FR-2.6 estimate) we use ~100 units/day global + 1
  call/day search bucket — **well within both quotas**. Confirmed
  non-issue per `docs/business-requirements.md` §8.3.
- Daily quotas reset at **midnight Pacific Time** — irrelevant for our
  volume but worth knowing if we ever batch-refresh.

### Library choice

Two options:
1. **`google-api-python-client`** — `googleapiclient.discovery.build("youtube",
   "v3", developerKey=...)`. Heavier (brings the whole Google API
   discovery stack), but typed.
2. **`httpx` direct** — one `GET` with the params above. Lighter, matches
   the other clients' style, `respx`-testable.

**Recommendation: `httpx` direct** for symmetry with #009/#011/#012. The
search endpoint is a single GET with no auth complexity (API key, no
OAuth). If we later need `videos.list` for richer metadata, revisit.

### Error / retry surface

- HTTP 5xx, 429, `httpx.TimeoutException` → tenacity retry (transient).
- HTTP 403 `quotaExceeded` → **no retry** (quota won't reset mid-call),
  raise `YouTubeDisabled` (treat as "skip enrichment" — same as no key,
  callers handle it gracefully per the issue summary).
- HTTP 400 `badRequest` / 401 `invalid` (bad key) → log warning, raise
  `YouTubeDisabled` — a bad key is effectively "feature off".
- Missing `YOUTUBE_API_KEY` env var → raise `YouTubeDisabled` immediately
  on client construction (not on first call — fail fast at startup if the
  client is wired in).

### Open questions to resolve during implementation
- [ ] `httpx`-direct vs `google-api-python-client` — pick one and document
      it in the module docstring. (Recommendation: `httpx`-direct.)
- [ ] `maxResults=3` vs `5` — the coach prompt expects "a few" links;
      pick 3 to keep prompt token cost down, revisit in Phase 7
      calibration.
- [ ] Should we filter by `videoDuration=medium` (4-20 min) to skip
      10-second spam and 2-hour lectures? Defer to Phase 7 calibration —
      start with no filter.

## Notes
- SearXNG fallback is out of scope for v1 (Phase 7 / #031).
