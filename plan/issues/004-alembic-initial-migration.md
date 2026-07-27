# #004 — Alembic initial migration

**Milestone:** M0 bootstrap · **Labels:** `type:infra` `area:db` `prio:P0`
**Depends on:** #003

## Summary
Wire Alembic to the SQLModel metadata and generate the initial migration that
creates all four tables.

## Context
- `docs/architecture.md` §3 shows `alembic/env.py` + `alembic/versions/`.
- Migrations run on startup in deploy (#030 / roadmap Phase 6).

## Tasks
- [ ] `alembic.ini` + `alembic/env.py` targeting `db.models` metadata and
      reading `DATABASE_URL` from settings (not hardcoded).
- [ ] Generate the initial revision creating: `leetcode_problems`,
      `leetcode_log`, `pending_review`, `tutor_lessons`.
- [ ] Confirm PK/defaults from #003 are reflected (slug PK, `solved=false`,
      `times_reinforced=1`, `active=true`, etc.).

## Acceptance criteria
- [ ] `uv run alembic upgrade head` on a fresh Postgres creates all 4 tables.
- [ ] `uv run alembic downgrade base` drops them cleanly.
- [ ] Autogenerate against the migrated DB reports **no** diff (models and
      migration are in sync).

## Principles (how they apply here)
See [#034](034-engineering-principles-layers.md). KISS is the tie-breaker.
- **KISS:** a single initial migration for all four tables — no premature
  splitting into per-table revisions.
- **Explicit over implicit:** `env.py` reads `DATABASE_URL` from settings, never
  hardcoded; no destructive auto-downgrade in prod.
- **Layer responsibility:** the migration reflects `db/models.py` and nothing
  else — no data seeding or business logic in migrations.

## Notes
- Keep migrations explicit; do not auto-run destructive downgrades in prod.
