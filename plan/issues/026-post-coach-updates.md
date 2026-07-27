# #026 — Post-coach updates (+ BUG-2 fix)

**Milestone:** M3 flow-b · **Labels:** `type:feature` `type:bug-fix` `area:flow-b` `prio:P0`
**Depends on:** #011, #024, #025

## Summary
Apply all five ordered side effects after a coach pass and reply to the user —
including the append-not-replace Google Task notes fix (BUG-2).

## Context
- `docs/business-requirements.md` FR-2.7 (exact ordered steps).
- **BUG-2:** the n8n `mark complete` node dropped coach feedback by replacing
  notes. The port must call
  `google_tasks.mark_complete(task_id, notes_append=tutor_feedback)` — append.
  (Client support landed in #011; this issue wires it into the flow.)

## Tasks
- [ ] After the coach pass + lesson decision, **in order** (FR-2.7):
  1. Insert `leetcode_log` row (full schema, incl. `lesson_title` if fired).
  2. If solved → set `leetcode_problems.solved = true`.
  3. `mark_complete(task_id, notes_append=tutor_feedback)` — **append** notes.
  4. Update `pending_review.status = done`.
  5. Telegram reply: short confirmation + coach feedback, explicitly naming any
     lesson saved / reinforced / retired.
- [ ] Route `GoogleAuthExpiredError` to the distinct alert (#008), not the
      global handler.

## Acceptance criteria
- [ ] All five steps execute in the FR-2.7 order.
- [ ] Google Task notes **retain** prior content and gain the feedback
      (BUG-2 fixed) — asserted in #027.
- [ ] `leetcode_log` row reflects status + feedback + optional lesson.
- [ ] Reply names the lesson outcome (saved/reinforced/retired) when applicable.

## Notes
- Never fabricate/estimate on failure; fail loudly + alert.

## Principles (how they apply here)
See [#034](034-engineering-principles-layers.md). KISS is the tie-breaker.
- **Single Responsibility / layer:** this step orchestrates the five
  ordered side effects; the actual sends/SQL/HTTP go through the Telegram,
  Google Tasks, and DB layers — no raw HTTP or SQL strings here.
- **Explicit over implicit:** the five steps run in the FR-2.7 order
  (log → problem.solved → task notes → pending_review → reply); ids and
  feedback are passed explicitly, not stashed in a global.
- **Fail loud / typed errors:** `GoogleAuthExpiredError` routes to the
  distinct #008 alert, not the generic handler — never swallowed.
- **DRY (genuine):** BUG-2 is fixed once here by calling
  `mark_complete(..., notes_append=...)` via the #011 client; no second
  copy of the call anywhere.
