# #022 — Pick-parse path

**Milestone:** M3 flow-b · **Labels:** `type:feature` `area:flow-b` `prio:P0`
**Depends on:** #009, #011, #016, #020, #021

## Summary
Parse a pick-list reply (e.g. "2 5") into ≤2 chosen problems, then create the
per-problem threads, Google Tasks, and `pending_review` rows.

## Context
- `docs/business-requirements.md` FR-2.3 (parse) + FR-2.4 (per-pick side
  effects, in order).
- Parse is **regex, no LLM** (`\d+`), capped at 2, mapped to today's 5-list
  candidates (from #020). Empty/invalid → short "no valid picks" message, log
  nothing.

## Tasks
- [ ] Regex parse of ≤2 numbers; map indices → today's candidates; cap at 2.
- [ ] Empty/invalid picks → send short "no valid picks" message, persist
      nothing, return.
- [ ] For each chosen problem, **in order** (FR-2.4):
  1. Send per-problem Telegram message ("Problem 1/2: ...") incl.
     `coaching_hint`; capture `message_id`.
  2. Create Google Task (title = problem name; notes = slug + reasoning +
     coaching_hint; due = next day); capture `task_id`.
  3. Insert `pending_review` row (message_id, google_task_id, problem_slug,
     problem_title, proposed_at = today, status = open).

## Acceptance criteria
- [ ] "2 5" → exactly 2 messages, 2 tasks, 2 `pending_review` rows with the
      captured ids.
- [ ] "2 3 4 5" → capped at 2 chosen.
- [ ] Empty/garbage reply → "no valid picks", zero rows written.
- [ ] Side effects happen in the FR-2.4 order (message → task → row).
- [ ] Covered by #027.

## Principles (how they apply here)
See [#034](034-engineering-principles-layers.md). KISS is the tie-breaker.
- **KISS:** parsing is a regex over digits — **no LLM** for something a regex
  solves (FR-2.3). The cap-at-2 rule is a simple slice.
- **Single Responsibility / layer:** the flow orchestrates the ordered side
  effects; the actual sends/tasks/rows go through the Telegram, Google Tasks,
  and DB layers — no raw HTTP or SQL strings here.
- **DRY:** reuse the #009/#011 clients rather than re-implementing calls.
- **Explicit over implicit:** side effects run in the FR-2.4 order
  (message → task → row), capturing ids as it goes.

## Notes
- The ≤2-open-per-day rule (architecture §7) is enforced here via the cap.
