# #040 — Credit ledger table + migration

**Milestone:** M9 phase-9 · **Labels:** `type:feature` `area:db` `prio:P0`
**Depends on:** #003, #036
**Spec:** `plan/PHASE9_DESIGN.md` §New Tables

## Summary
Add the `credit_ledger` table for the credit/debit budget system, add a
`credits_earned` column to `leetcode_log`, and expand `pending_review.status`
to include `skipped` and `saw_solution`. Alembic migration + historical backfill.

## Context
- The budget system (Phase 9) replaces the rigid daily cycle with a credit
  ledger. Each day accrues -2 credits (tax). Solving problems earns credits
  (hard=2, medium=1, easy=0.5). Reviewed=0.5, saw_solution=0.25, skip=0.
- The ledger is append-only and auditable. Balance = SUM(credits).
- `leetcode_log.credits_earned` is a denormalized copy for fast /status display.
- `LeetCodeLog.status` already documents `solved / reviewed / skipped /
  saw_solution` (model docstring, line 53) and `_VALID_STATUSES` in
  `flow_b.py` already accepts all four — no schema or validation change
  needed for `leetcode_log`.
- `pending_review.status` currently allows `open|done|expired` (model
  docstring, line 74). The new button actions (Skip, Solution) need
  `skipped` and `saw_solution` statuses. This is a docstring + application-
  code change only — the column is VARCHAR(10), so `saw_solution` (12 chars)
  **exceeds the column width** and must be shortened (e.g. `saw_soln`) or
  the column widened to VARCHAR(20). See Tasks.

## Tasks
- [ ] `db/models.py`: add `CreditLedger` model:
  - `id: int PK`
  - `date: DATE` (indexed)
  - `entry_type: str` — `tax|solve|review|saw_solution|skip|adjustment`
  - `problem_slug: str | None` — NULL for tax entries
  - `credits: float` — negative for tax, positive for awards
  - `note: str | None`
  - `created_at: TIMESTAMPTZ`
- [ ] `db/models.py`: add `credits_earned: float = Field(default=0.0)` to `LeetCodeLog`.
- [ ] `db/models.py`: widen `PendingReview.status` from `max_length=10` to
      `max_length=20` (current value 10 cannot hold `saw_solution` = 12 chars).
      Update the docstring to list `open|done|expired|skipped|saw_solution`.
- [ ] Alembic migration `0005_credit_ledger.py` (next number — `0004_bot_state.py`
      already exists):
  - Create `credit_ledger` table.
  - Add `credits_earned` column to `leetcode_log` (nullable, default 0).
  - Alter `pending_review.status` from VARCHAR(10) to VARCHAR(20).
- [ ] Backfill script (in the migration's `upgrade()`):
  - For each historical `leetcode_log` row, compute `credits_earned` from
    `status` + the problem's `difficulty` and set the column.
  - Do NOT create retroactive ledger entries — the backfill only populates
    `leetcode_log.credits_earned`. The ledger starts fresh from migration day.
- [ ] `tests/test_credit_ledger.py`: model creation, migration up/down.

## Credit value table (reference for #041)
| status         | easy  | medium | hard |
|----------------|-------|--------|------|
| solved         | 0.5   | 1.0    | 2.0  |
| reviewed       | 0.5   | 0.5    | 0.5  |
| saw_solution   | 0.25  | 0.25   | 0.25 |
| skipped        | 0     | 0      | 0    |

## Acceptance criteria
- [ ] `credit_ledger` table exists with the documented columns.
- [ ] `leetcode_log.credits_earned` column exists, defaults to 0.0.
- [ ] `pending_review.status` column widened to VARCHAR(20).
- [ ] Historical `leetcode_log` rows have `credits_earned` backfilled.
- [ ] Migration up + down works cleanly.
- [ ] `CreditLedger` model is importable and testable.

## Principles
- **KISS:** one table, append-only, no triggers. Balance is a SUM query.
- **Explicit over implicit:** the credit value table is documented above and
  will be encoded as a function in #041, not as magic numbers scattered in flows.
