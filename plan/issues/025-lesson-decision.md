# #025 — Lesson decision (double-gated graduation)

**Milestone:** M3 flow-b · **Labels:** `type:feature` `area:flow-b` `risk:high` `prio:P0`
**Depends on:** #003, #024

## Summary
The adaptability-loop core: save / reinforce / graduate `tutor_lessons` based on
the coach's decision, with graduation **double-gated** against the DB count.

## Context
- `docs/business-requirements.md` FR-2.6 + AGENTS.md gotcha #4:
  - Generalizable lesson only (a pattern across problems, not a one-off bug).
  - Existing active lesson matches (title similarity **or** same category +
    pattern) → bump `times_reinforced` (do **not** duplicate).
  - New lesson → insert with `times_reinforced = 1`, `active = true`.
  - **Graduation** requires `lesson_should_graduate = true` (coach) **AND** the
    existing row's `times_reinforced >= 5` **read from the DB** (not the coach).
    On graduation set `active = false`; feedback explicitly says "I'm retiring
    this lesson".
- `docs/architecture.md` §7: `tutor_lessons.title` is not DB-unique — dedup by
  similarity in code here.

## Tasks
- [ ] Similarity/category matcher against active lessons.
- [ ] Bump-vs-insert logic (no duplicates).
- [ ] Double-gate: read `times_reinforced` from DB; graduate only if coach flag
      **and** DB count ≥ 5; else bump.
- [ ] Return which lesson was saved / reinforced / retired for the reply (#026).

## Acceptance criteria
- [ ] New generalizable lesson → inserted with count 1, active true.
- [ ] Recurring lesson → existing row bumped, no duplicate inserted.
- [ ] Coach says graduate but DB count = 4 → **bump, not graduate** (the golden
      case in #027).
- [ ] Coach says graduate and DB count ≥ 5 → `active = false`, retirement noted.
- [ ] Count is always read from DB, never trusted from the coach.

## Notes
- Graduation threshold (5) is an open decision (§8.1) — keep it a single
  constant so #031 can recalibrate.

## Principles (how they apply here)
See [#034](034-engineering-principles-layers.md). KISS is the tie-breaker.
- **Single Responsibility:** this step owns lesson dedup + graduation only.
  It does not write `leetcode_log`, send Telegram replies, or call Google
  Tasks — those are #026.
- **Explicit over implicit / fail loud:** the graduation count is read from
  the DB row, never trusted from the coach (AGENTS.md gotcha #4). A coach
  hallucinating `times_reinforced = 7` must not graduate a count-2 lesson.
- **KISS:** similarity match is a title/category check, not an embedding
  model — the spec says "title similarity **or** same category + pattern."
- **Open/Closed:** the threshold (5) is a single named constant so #031
  can recalibrate without touching this logic.
