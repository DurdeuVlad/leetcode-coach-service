# #020 — Candidate persistence decision

**Milestone:** M3 flow-b · **Labels:** `type:infra` `area:db` `area:flow-b` `prio:P0`
**Depends on:** #003, #016

## Summary
Decide and implement **where** Flow A's 5-candidate array is stored so Flow B's
pick-parse path can map reply numbers → problems. This is a required decision,
**not** something to punt (roadmap Phase 3a; AGENTS.md gotcha #5).

## Context
- `docs/roadmap.md` Phase 3a and AGENTS.md gotcha #5 name two options:
  1. a dedicated `daily_candidates` table, or
  2. pre-inserted `pending_review` rows with `status = proposed`.
- The 5-list Telegram message id is **never** stored in `pending_review`
  (FR-2.2) — routing distinguishes "reply to the 5-list" from "reply to a
  per-problem message" by that absence. The chosen mechanism must not break
  that invariant.

## Tasks
- [ ] Evaluate both options against: routing invariant above, expiry sweep
      simplicity (FR-3), and the "≤2 open per day" app-level rule.
- [ ] **Write the decision down** in this issue and, if it adds/changes a
      table, in `docs/architecture.md` §7 (with rationale).
- [ ] Implement the storage: write on Flow A (#016), read on pick-parse (#022).
- [ ] If a new table is chosen, add an Alembic migration (extends #004).

## Acceptance criteria
- [ ] A single documented decision (table choice + why) exists.
- [ ] Flow A can persist the 5 candidates; Flow B can read them back for the
      current day.
- [ ] The 5-list message id remains outside `pending_review` (routing
      invariant preserved).
- [ ] Schema change (if any) is migrated and reflected in docs.

## Principles (how they apply here)
See [#034](034-engineering-principles-layers.md). KISS is the tie-breaker.
- **KISS:** pick the *simplest* storage that satisfies the routing invariant —
  a small `daily_candidates` table beats overloading `pending_review` semantics
  unless evaluation proves otherwise.
- **YAGNI:** store just enough to map a pick number → problem for today; no
  historical candidate archive, no extra metadata "just in case".
- **Explicit over implicit:** the decision is written down (here + §7) — this is
  the one place the roadmap says "decide, don't punt".
- **Layer responsibility:** persistence lives in `db/`; Flow A/Flow B call it,
  they don't hand-roll SQL.

## Notes
- Recommended default: a `daily_candidates` table keyed by date + index, unless
  evaluation shows the pre-inserted-rows approach is clearly simpler. Record the
  final call here.

## Decision (resolved 2026-07-28)

**Chosen: `daily_candidates` table** (the recommended default), keyed by
`(proposed_at, pick_index)` with `pick_index` 1-based (1..5).

### Why over pre-inserted `pending_review` rows (`status = proposed`)
1. **Routing invariant (FR-2.2) is preserved cleanly.** The 5-list Telegram
   message_id is never stored in `pending_review`, so a reply to the 5-list
   → `pending_review` lookup misses → pick-parse path. Pre-inserted rows
   would require either storing the 5-list message_id (breaking the
   invariant) or a `status = proposed` filter that the lookup node would
   have to remember to apply — fragile.
2. **`pending_review` semantics stay clean.** It holds only per-problem
   threads (the rows the coach pass looks up by `message_id`). No
   `proposed`/`open` status split to reason about.
3. **Expiry sweep (FR-3) is simpler.** It sweeps `pending_review` where
   `status = open` — no need to exclude `proposed` rows or worry about
   expiring the 5-list itself.
4. **≤2-open-per-day rule** is enforced by Flow B's pick cap (≤2 picks);
   `pending_review` only ever receives rows for picked problems.

### Tradeoffs accepted
- One extra table + migration (0003). Minor.
- `daily_candidates` rows are write-once per day (Flow A replaces today's
  rows if it re-runs). No historical archive — YAGNI per the issue.

### Wiring
- **Write (Flow A, #016):** after sending the 5-list message, delete
  today's existing `daily_candidates` rows (idempotent re-run) and insert
  5 rows (one per candidate, `pick_index` 1..5).
- **Read (Flow B pick-parse, #022):** `SELECT * FROM daily_candidates WHERE
  proposed_at = today ORDER BY pick_index`. Map user pick numbers (1-based)
  → rows by `pick_index`.

Reflected in: `db/models.py::DailyCandidate`, `alembic/versions/0003_daily_candidates.py`,
`docs/architecture.md` §7.
