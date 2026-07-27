# #003 — DB layer: 4 SQLModel tables + engine/session

**Milestone:** M0 bootstrap · **Labels:** `type:infra` `area:db` `prio:P0`
**Depends on:** #002

## Summary
Define the four persistent tables as typed SQLModel models plus the engine and
session factory. This unblocks almost every flow.

## Context
- Schema is fixed in `docs/business-requirements.md` §5. **Column names are
  case-sensitive and referenced by name in code — match exactly.**
- Design notes in `docs/architecture.md` §7:
  - `leetcode_problems.slug` is the **PK**.
  - `tutor_lessons.title` is **not** DB-unique (dedup by similarity in #025).
  - All date fields are `DATE`, not `TIMESTAMPTZ` (single timezone).
  - `pending_review` "max 2 open per day" is enforced in app code, **not** a
    DB constraint.

## Tasks
- [ ] `src/leetcode_coach/db/base.py` — SQLAlchemy engine from
      `settings.DATABASE_URL`, async-friendly session factory, a `get_session`
      dependency/contextmanager.
- [ ] `src/leetcode_coach/db/models.py` — four models:
  - `LeetCodeProblem` (title, slug **PK**, url, difficulty, tags, solved
    default `false`, last_attempted nullable date, times_attempted default `0`).
  - `LeetCodeLog` (problem_slug FK, date, status, time_spent_min nullable,
    tutor_feedback text nullable, lesson_title nullable).
  - `PendingReview` (message_id, google_task_id, problem_slug FK,
    problem_title, proposed_at date, status).
  - `TutorLesson` (title, category, created_at date, times_reinforced
    default `1`, active default `true`).
- [ ] Add surrogate PKs where the spec has none (log, pending_review) if needed
      for row identity; keep the spec columns exactly.

## Acceptance criteria
- [ ] All four models import without error and match §5 column names/types.
- [ ] `slug` is the PK of `LeetCodeProblem`.
- [ ] `status` fields accept the documented enum values
      (`open`/`done`/`expired` for pending_review;
      `solved`/`reviewed`/`skipped`/`saw_solution` for log).
- [ ] Covered by the smoke test in #007 (tables round-trip a row).

## Principles (how they apply here)
See [#034](034-engineering-principles-layers.md). KISS is the tie-breaker.
- **KISS:** plain SQLModel tables that mirror §5 one-to-one. Surrogate PKs only
  where the spec genuinely lacks row identity.
- **Single Responsibility:** models hold *shape* only. No HTTP, no LLM, no flow
  decisions in the DB layer.
- **Explicit over implicit:** no `relationship=` cascades / lazy loading;
  queries elsewhere are explicit `select()` (architecture §12).
- **YAGNI:** no indexes/constraints beyond what the spec needs (e.g. the
  "≤2 open/day" rule stays in app code, not a DB constraint).

## Notes
- Explicit `select()` queries only; no relationship cascades / lazy loading
  (architecture §12).
