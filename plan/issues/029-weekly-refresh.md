# #029 — Weekly LeetCode pool refresh + cron + tests

**Milestone:** M5 weekly-refresh · **Labels:** `type:feature` `area:refresh` `area:scheduling` `prio:P1`
**Depends on:** #012, #017

## Summary
The weekly job that pulls LeetCode problem history and upserts it into
`leetcode_problems`, keeping the unsolved pool current.

## Context
- `docs/business-requirements.md` FR-4: weekly GraphQL pull → upsert; Browserless
  fallback only if blocked and available (stub in v1).
- `docs/architecture.md` §4: cron `0 3 * * 1` (Monday 03:00).
- Independent of the flows (roadmap sequencing) — can be built any time after
  M1's LeetCode client (#012).

## Tasks
- [ ] `refresh_pool()` in `integrations/leetcode.py` (or a small flow wrapper):
      call `fetch_problems`, upsert by `slug` (insert new, update
      title/url/difficulty/tags; **do not** clobber `solved`/attempt counters).
- [ ] Register APScheduler job `0 3 * * 1` (add to #017's scheduler).
- [ ] Wrap in the #008 job-error wrapper (layer 3 alert; Browserless stub
      re-raise surfaces here).
- [ ] `tests/test_leetcode_refresh.py` with a mocked GraphQL response.

## Acceptance criteria
- [ ] After a run against a mocked response, `leetcode_problems` contains the
      fixture rows (new inserted, existing updated).
- [ ] Upsert preserves `solved` and attempt history for existing slugs.
- [ ] Job runs Monday 03:00 in the configured timezone.
- [ ] GraphQL block → Browserless stub logs the documented line and the job
      alerts (no silent success).

## Notes
- Wiring the real Browserless fallback is deferred to Phase 7 / #031.

## Principles (how they apply here)
See [#034](034-engineering-principles-layers.md). KISS is the tie-breaker.
- **YAGNI:** the Browserless fallback is a **stub** in v1 — only build the
  real one in #031 if the GraphQL endpoint actually blocks the homelab IP
  (open decision §8.4). No speculative retry/rotation machinery now.
- **Single Responsibility / layer:** `refresh_pool()` lives in the LeetCode
  integration layer (it owns *how* to pull + upsert); the cron job in
  #017 only owns *when* to fire it.
- **Explicit over implicit:** upsert by `slug` and **do not clobber**
  `solved`/attempt counters — the upsert column list is explicit, not a
  blanket `INSERT ... ON CONFLICT DO UPDATE *`.
- **Fail loud:** a GraphQL block surfaces via the #008 alert with the
  documented stub log line — never a silent "success with empty pool."
