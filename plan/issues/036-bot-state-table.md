# #036 — `bot_state` table + Alembic migration

**Milestone:** M8 phase-8c · **Labels:** `type:feature` `area:db` `prio:P0`
**Depends on:** #003, #004
**Spec:** `docs/business-requirements.md` §5 (`bot_state`), FR-8.3

## Summary
Add a single key-value table for runtime state that must survive restarts
but should not require a redeploy to change. First and currently only use:
the pinned progression message ID (FR-8.3, #039).

## Context
- FR-8.3: the pinned message ID is stored in `bot_state`, not an env var,
  so it can be updated without redeploying.
- `docs/business-requirements.md` §5 schema:
  | Column | Type | Notes |
  |---|---|---|
  | `key` | string | primary key, e.g. `pinned_message_id` |
  | `value` | string | JSON-encoded value; consumer parses per key |
  | `updated_at` | timestamptz | set on every write |
- The table is intentionally generic. Add keys as new stateful features
  arrive; do not add columns to existing tables for one-off state.

## Tasks
- [ ] Add `BotState` SQLModel in `src/leetcode_coach/db/models.py`:
      `key: str (pk)`, `value: str`, `updated_at: datetime` (server default
      now, refreshed on write).
- [ ] Alembic migration `0004_bot_state.py` creating the table.
- [ ] Thin helper `db/queries.py` (or wherever existing query helpers live):
      `get_state(key) -> str | None`, `set_state(key, value) -> None`.
      `set_state` upserts and bumps `updated_at`.
- [ ] Tests: upsert on new key, upsert overwrites existing key and bumps
      `updated_at`, `get_state` returns None for missing key.

## Acceptance criteria
- [ ] `alembic upgrade head` creates the `bot_state` table with the three
      columns and `key` as PK.
- [ ] `set_state("pinned_message_id", "12345")` then
      `get_state("pinned_message_id")` returns `"12345"`.
- [ ] Second `set_state` on the same key overwrites and updates
      `updated_at`.
- [ ] `get_state("nonexistent")` returns `None`.
- [ ] Migration is reversible (`alembic downgrade -1` drops the table).
- [ ] Covered by `tests/test_bot_state.py`.

## Principles (how they apply here)
See [#034](034-engineering-principles-layers.md).
- **YAGNI:** one table, three columns. No JSONB, no indexes beyond the PK,
  no schema-per-key. We need it now for one key; we will not over-design it
  for hypothetical future state.
- **Explicit over implicit:** `value` is a JSON string and the consumer
  parses it per key. This keeps the column type stable across keys instead
  of constraining the schema to today's only use.

## Notes
- This issue is a prerequisite for #039 (pinned progression message). It
  can ship independently and be merged first.
- Do not add a `BotState` Pydantic API model — this table is internal state,
  not exposed over any HTTP surface.
