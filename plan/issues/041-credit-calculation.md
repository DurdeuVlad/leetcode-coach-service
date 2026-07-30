# #041 — Credit calculation + balance query

**Milestone:** M9 phase-9 · **Labels:** `type:feature` `area:db` `prio:P0`
**Depends on:** #040
**Spec:** `plan/PHASE9_DESIGN.md` §Budget System

## Summary
Pure functions for credit computation: per-outcome credit values, balance
query (SUM of ledger), daily tax accrual (idempotent), and credit awarding
on solve/review/saw_solution/skip.

## Context
- The credit value table is the single source of truth for what each
  outcome is worth. Encoded as one function, not scattered constants.
- Balance = `SELECT SUM(credits) FROM credit_ledger`. Simple, auditable.
- Daily tax must be idempotent: if the process restarts and the midnight
  job fires twice, or the job was missed and runs on startup, only one
  tax entry per day. Guard with `last_tax_date` in `bot_state`.
- Credit awarding happens in TWO places:
  1. Flow B's `_post_coach_updates` — after the coach pass determines the
     status. `_VALID_STATUSES` in `flow_b.py` already includes all four
     (`solved`, `reviewed`, `skipped`, `saw_solution`), so the coach pass
     itself can produce any of them. `award_credits` must be called for
     **all four statuses**, not just solved/reviewed.
  2. The new button handlers (#045) — Skip and Solution buttons produce
     `skipped` and `saw_solution` without a coach pass.
- `award_credits` needs the problem's `difficulty`, which lives on
  `LeetCodeProblem` (not on `PendingReview` or `CoachResult`). The caller
  must look it up: `session.get(LeetCodeProblem, slug).difficulty`.
- `award_credits` updates `leetcode_log.credits_earned` for the most recent
  log entry of the slug. Therefore it **must be called AFTER** the log row
  is inserted (in `_post_coach_updates`, that means after step 1, before
  or after step 4 — but the log row must exist).

## Tasks
- [ ] `flows/credits.py` (new module):
  - `CREDIT_TABLE: dict[tuple[str, str], float]` — keyed by
    `(status, difficulty)`. See #040 for the value table.
  - `compute_credits(status: str, difficulty: str) -> float` — lookup, 0.0
    for unknown combinations (log warning, don't crash).
  - `get_balance(session) -> float` — `SELECT SUM(credits) FROM credit_ledger`.
    Returns 0.0 if table empty.
  - `accrue_daily_tax(session) -> bool` — insert `entry_type='tax'`,
    `credits=-2.0` for today if not already present. Check `bot_state`
    key `last_tax_date` for idempotency. Returns True if inserted, False
    if already accrued. Update `last_tax_date` on insert.
  - `award_credits(session, slug, status, difficulty, note=None) -> float` —
    compute credits via `compute_credits`, insert ledger entry, update
    `leetcode_log.credits_earned` for the most recent log entry of this slug.
    **Caller must look up `difficulty` from `LeetCodeProblem`** — it is not
    available on `PendingReview` or `CoachResult`. Must be called AFTER the
    `leetcode_log` row is inserted (it updates that row). Return credits awarded.
  - `format_balance(balance: float) -> str` — `"+3.5 (ahead 1 day)"` or
    `"-1.0 (behind 0.5 days)"`. Used by pinned message (#048) and nudge (#047).
- [ ] `tests/test_credits.py`:
  - Credit table: all status×difficulty combinations return expected values.
  - Unknown combination returns 0.0 + warning log.
  - `get_balance`: empty ledger → 0.0; after tax → -2.0; after solve → correct.
  - `accrue_daily_tax`: first call inserts, second call is no-op.
  - `award_credits`: inserts ledger entry, updates leetcode_log.credits_earned.
  - `format_balance`: positive/negative/zero formatting.

## Acceptance criteria
- [ ] `compute_credits("solved", "hard")` returns 2.0.
- [ ] `compute_credits("reviewed", "easy")` returns 0.5.
- [ ] `compute_credits("saw_solution", "medium")` returns 0.25.
- [ ] `compute_credits("skipped", "hard")` returns 0.0.
- [ ] `get_balance` on empty ledger returns 0.0.
- [ ] `accrue_daily_tax` is idempotent within the same day.
- [ ] `award_credits` creates a ledger entry AND updates leetcode_log.
- [ ] `format_balance` formats positive/negative/zero correctly.
- [ ] All tests pass.

## Principles
- **DRY:** the credit table is defined once, used everywhere.
- **KISS:** balance is a SUM query, not a cached column. No denormalization
  beyond `leetcode_log.credits_earned` (which is for /status display only).
- **Idempotency:** tax accrual must survive restarts and duplicate job fires.
