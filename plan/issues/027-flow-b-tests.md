# #027 — Flow B tests (pick-parse + coach + golden gate)

**Milestone:** M3 flow-b · **Labels:** `type:test` `area:flow-b` `risk:high` `prio:P0`
**Depends on:** #021, #022, #024, #025, #026

## Summary
Full Flow B test coverage: routing, pick-parse, coach pass, and the golden
double-gate lesson test (BUG-2 regression included).

## Context
- `docs/roadmap.md` Phase 3 exit criteria: fake "2 5" reply → 2 per-problem
  messages → reply with fake code → coach feedback → DB shows
  `pending_review.status = done`, `leetcode_log` inserted, Google Task
  completed with feedback in notes.

## Tasks
- [ ] `tests/test_flow_b.py`:
  - **Routing:** reply-to per-problem id → coach; reply-to 5-list id →
    pick-parse; no-reply single fuzzy match → coach; zero/multiple → clarify +
    no state change.
  - **Pick-parse:** "2 5" → 2 msgs / 2 tasks / 2 rows; cap at 2; empty → "no
    valid picks" + zero rows.
  - **Coach path:** all five FR-2.7 updates happen **in order**.
  - **BUG-2 regression:** `mark_complete` called with `notes_append` and prior
    notes preserved.
  - **Golden double-gate:** coach says graduate but DB count = 4 → bump, not
    graduate; count ≥ 5 → retire.
  - Status notes: "skipped" logs status only; "saw solution" adds takeaway.
- [ ] Mock Telegram + Google Tasks + LLM; real Postgres via testcontainers.

## Acceptance criteria
- [ ] Suite green and covers every FR-2 sub-requirement above.
- [ ] Golden double-gate test fails if the DB-count gate is removed.
- [ ] BUG-2 test fails if notes are replaced instead of appended.

## Notes
- This is the highest-risk area; keep these tests as living regression
  baselines (feeds into #031 golden suite).

## Principles (how they apply here)
See [#034](034-engineering-principles-layers.md). KISS is the tie-breaker.
- **Dependency Inversion (verified by test):** mocks for Telegram / Google
  Tasks / LLM are substitutable because flows depend on the client
  abstractions, not on `httpx` or SDK types directly. Real Postgres via
  testcontainers keeps the DB layer honest.
- **Fail loud, verified:** the golden double-gate test **must fail** if the
  DB-count gate is removed; the BUG-2 test **must fail** if notes are
  replaced. Tests that can't fail on the bug they guard are theatre.
- **Explicit over implicit:** each case asserts the exact FR-2.7 ordering,
  not just "all five happened."
- **KISS:** one `tests/test_flow_b.py` covering all FR-2 sub-requirements —
  no parametrized framework, no shared fixture maze.
