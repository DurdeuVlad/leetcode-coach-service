# #046 — Coach feedback: follow-up buttons

**Milestone:** M9 phase-9 · **Labels:** `type:feature` `area:flow-b` `area:telegram` `prio:P1`
**Depends on:** #043, #041, #045
**Spec:** `plan/PHASE9_DESIGN.md` §Coach Feedback

## Summary
Add inline keyboard `[▶️ Next Problem] [🔄 Re-attempt] [🤔 Why This Lesson?]`
to the coach feedback reply. Next jumps to the other open thread (or "all
done"), Re-attempt re-opens the current thread for another submission, Why
This Lesson explains the lesson that was saved/reinforced.

## Context
- After the coach pass, the bot replies with feedback + lesson footer.
  Currently plain HTML. Add three follow-up buttons.
- Next: finds the next `pending_review` with `status='open'` for today.
  If found, sends its thread message (re-uses the thread message builder).
  If none, toast "All done for today! 🎉".
- Re-attempt: resets the current `pending_review.status` back to 'open'
  (from 'done'), re-sends the thread message so the user can reply with
  a new code submission. Does NOT undo the credit awarding or the log
  entry — those stand as a record of the first attempt.
- Why This Lesson: if the coach saved/reinforced a lesson, runs a short
  LLM call explaining why that lesson is relevant. 2-3 sentences, max 300
  tokens (same budget as /why). If no lesson was saved (outcome='none'),
  toast "No lesson was saved for this problem". NOTE: this is NEW code —
  there is no existing "why_lesson" function to reuse. It builds its own
  prompt and calls the LLM directly. Do NOT confuse it with `_cmd_why`
  (which explains the problem) or `_generate_why_explanation` from #045
  (same — explains the problem). This explains the LESSON.

## Tasks
- [ ] `flows/flow_b.py`: in `_post_coach_updates`, add inline keyboard to
      the coach reply:
  - `[▶️ Next Problem]` → `callback_data=encode_callback_data("next", slug)`
  - `[🔄 Re-attempt]` → `callback_data=encode_callback_data("reattempt", slug)`
  - `[🤔 Why This Lesson?]` → `callback_data=encode_callback_data("why_lesson", slug)`
  - Only add buttons if the coach pass completed successfully (not dry_run).
  - Note: `why_lesson` + `:` + slug may exceed 64 bytes for long slugs;
    `encode_callback_data` (from #043) handles this via hash mapping.
- [ ] `webhooks/callbacks.py`: implement `handle_next(slug, callback_query)`:
  - Find next `pending_review` with `status='open'`, `proposed_at=today`,
    `problem_slug != slug` (exclude the one just coached).
  - If found: build thread message (reuse `flow_b` thread builder) + send
    with the 4 action buttons from #045.
  - If none: answer callback with toast "All done for today! 🎉".
- [ ] `webhooks/callbacks.py`: implement `handle_reattempt(slug, callback_query)`:
  - Find `pending_review` by slug + today. Must be `status='done'`.
  - Reset `status = 'open'`.
  - Re-send the thread message with action buttons.
  - Answer callback with toast "Thread re-opened — send your new code".
  - Do NOT undo credits or log entries.
- [ ] `webhooks/callbacks.py`: implement `handle_why_lesson(slug, callback_query)`:
  - Read the most recent `leetcode_log` entry for this slug.
  - If `lesson_title` is None/empty → toast "No lesson was saved".
  - Else: build a short prompt: "Explain why the lesson '{lesson_title}' is
    relevant to the problem '{slug}'. 2-3 sentences." Call LLM, reply.
  - Answer callback with toast "Generating..." then send message.
- [ ] `tests/test_callbacks.py`: next (found + not found), re-attempt
  (re-opens thread, keeps credits), why_lesson (with + without lesson).

## Acceptance criteria
- [ ] Coach reply has 3 buttons: Next, Re-attempt, Why This Lesson.
- [ ] Next: if open problems exist, sends that thread's message with buttons.
- [ ] Next: if no open problems, toast "All done for today! 🎉".
- [ ] Re-attempt: resets status to 'open', re-sends thread message.
- [ ] Re-attempt: does NOT undo credits or log entries.
- [ ] Why This Lesson: if lesson saved, sends LLM explanation.
- [ ] Why This Lesson: if no lesson, toast "No lesson was saved".
- [ ] All tests pass.

## Principles
- **KISS:** Next is just a query + re-send. No complex navigation state.
- **Honesty:** Re-attempt doesn't erase history. The first attempt's credits
  and log entry stand. The second attempt is a new log entry.
- **DRY:** thread message builder is shared between Flow B pick and Next/Re-attempt.
