# #038 — Progression queries (`/status`, `/why`)

**Milestone:** M8 phase-8b · **Labels:** `type:feature` `area:flow-b` `prio:P1`
**Depends on:** #035
**Spec:** `docs/business-requirements.md` FR-7 (Progression queries)

## Summary
Two read-only commands that surface the adaptability loop to the user.
`/status` is a deterministic DB dump (no LLM). `/why` is a single bounded
LLM call. Neither writes anything.

## Context
- FR-7.1: `/status` → active lessons (title + `times_reinforced`), last 7
  days of `leetcode_log` (date, problem, solved?, lesson), current streak
  (consecutive days with ≥1 coached attempt). No LLM call.
- FR-7.2: `/why <slug>` → one LLM call, 2-3 sentences, explaining why a
  problem was proposed or what lesson it targets. Bounded to a single call.
- FR-7.3: progression queries are read-only. They never insert or update
  any row.
- These commands make the system's memory visible to the user — the highest-
  value piece of Phase 8 per the user (2026-07-29).

## Tasks
- [ ] `/status` handler in `flows/commands.py`:
  - Query active `tutor_lessons` (`active = true`), order by
    `times_reinforced` desc.
  - Query `leetcode_log` for the last 7 days, order by date desc.
  - Compute streak: consecutive days (ending today or yesterday) with ≥1
    `leetcode_log` row where `status = 'coached'` (or whatever field marks
    a real attempt — confirm against the schema).
  - Format as a compact Telegram message (markdown). No LLM call.
- [ ] `/why <slug>` handler:
  - Look up the problem by slug. If not in `leetcode_problems`, reply "no
    such problem" and stop.
  - Build a single LLM call: system prompt explains the user's active
    lessons + recent log; user prompt is "why was <slug> proposed / what
    lesson does it target?". 2-3 sentences.
  - Use the existing `LLMClient.complete` with `max_completion_tokens` low
    (e.g. 300) to keep cost down and bound the response.
  - Reply with the LLM output.
- [ ] Both handlers: read-only. Add an assertion in tests that no
      `tutor_lessons`, `leetcode_log`, `pending_review`, or `bot_state` row
      is inserted/updated after the call.
- [ ] `/why` with no slug → short "usage: /why <slug>" reply, no LLM call.

## Acceptance criteria
- [ ] `/status` against a seeded DB returns a message containing: each
      active lesson title + `times_reinforced`, the last 7 days of log
      rows, the streak count.
- [ ] `/status` makes zero LLM calls (assert in test).
- [ ] `/status` writes zero rows (assert in test).
- [ ] `/why two-sum` makes exactly one LLM call (assert in test).
- [ ] `/why nonexistent-slug` → "no such problem", zero LLM calls.
- [ ] `/why` with no args → "usage: /why <slug>", zero LLM calls.
- [ ] `/why` writes zero rows (assert in test).
- [ ] Covered by `tests/test_commands.py` (extend the file from #035).

## Principles (how they apply here)
See [#034](034-engineering-principles-layers.md).
- **KISS:** `/status` is straight SQL + string formatting. No LLM for
  something a query solves — same principle as FR-2.3's "regex, no LLM".
- **Cost-aware (NFR-2):** `/why` is bounded to one call with a low
  `max_completion_tokens`. The system's cost ceiling is <$10/month; an
  unbounded `/why` could blow that on its own if the user spams it.
- **Explicit over implicit:** read-only is enforced and tested, not
  assumed. A regression that accidentally writes from `/status` would be a
  silent corruption of the adaptability loop.

## Notes
- The streak definition: "consecutive days with ≥1 coached attempt." Confirm
  the exact `leetcode_log` column/value that marks a coached attempt vs a
  skipped/saw-solution entry — the schema has a status field. Use that, do
  not invent a new one.
- `/why` does not need to be deterministic — it's an LLM explanation. The
  test mocks the LLM and asserts the call shape, not the content.
