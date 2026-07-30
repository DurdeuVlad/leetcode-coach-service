# #045 — Per-problem thread: action buttons

**Milestone:** M9 phase-9 · **Labels:** `type:feature` `area:flow-b` `area:telegram` `prio:P1`
**Depends on:** #043, #041
**Spec:** `plan/PHASE9_DESIGN.md` §Per-Problem Thread

## Summary
Add inline keyboard `[⏭️ Skip] [💡 Hint] [📖 Solution] [🤔 Why]` to each
per-problem thread message. Implement each button's handler: Skip closes
the thread with 0 credits, Hint shows the coaching hint, Solution closes
with 0.25 credits, Why runs the /why flow inline.

## Context
- Each picked problem gets a thread message (Flow B's `_pick_parse_path`).
  Currently plain HTML text. Add buttons for the four actions.
- No "Send Code" button (decision from brainstorming): code is submitted by
  replying to the thread message with text. Buttons are only for actions.
- Skip: sets `pending_review.status = 'skipped'`, logs 0 credits, edits
  message to remove buttons, appends "⏭️ Skipped", refreshes pinned.
- Hint: re-displays the `coaching_hint` from `daily_candidates`. Can be a
  toast (short) or a reply message (if hint is long). Use toast if ≤200
  chars, else reply message.
- Solution: sets `pending_review.status = 'saw_solution'`, logs 0.25 credits,
  edits message to remove buttons, appends "📖 Solution viewed", refreshes
  pinned. Does NOT show the actual solution (we don't have it) — just marks
  the outcome.
- Why: runs the `/why` logic inline. NOTE: `commands._cmd_why` is tightly
  coupled to `update.message.chat.id` and calls `send_message` directly —
  it is NOT a reusable function that returns text. This issue must extract
  a `_generate_why_explanation(slug) -> str` helper from `_cmd_why` (the
  context-gathering + LLM call, lines 302-349), then both `_cmd_why` and
  `handle_why` call it. Read-only, no DB writes.
- **Soft dependency on #044:** both issues modify the per-problem thread
  message builder in `_pick_parse_path` (or its extracted helper
  `_create_threads_for_candidates`). If #044 lands first, the buttons are
  added to the helper; if #045 lands first, they're added to
  `_pick_parse_path` directly. Coordinate the merge order.

## Tasks
- [ ] `flows/commands.py`: extract `_generate_why_explanation(slug: str) -> str`
      from `_cmd_why` (lines 302-349: context gathering + LLM call). Refactor
      `_cmd_why` to call it then `send_message` the result. Pure refactor —
      no behavior change to `/why`.
- [ ] **Register action handlers** (per #043's `register_action`):
      `register_action("skip", handle_skip)`, `register_action("hint",
      handle_hint)`, `register_action("solution", handle_solution)`,
      `register_action("why", handle_why)`. This wiring is mandatory, not
      implicit.
- [ ] `flows/flow_b.py`: in `_create_threads_for_candidates` (extracted in
      #044) OR in `_pick_parse_path` (if #044 hasn't landed — coordinate),
      add inline keyboard to the per-problem message:
  - `[⏭️ Skip]` → `callback_data=encode_callback_data("skip", slug)`
  - `[💡 Hint]` → `callback_data=encode_callback_data("hint", slug)`
  - `[📖 Solution]` → `callback_data=encode_callback_data("solution", slug)`
  - `[🤔 Why]` → `callback_data=encode_callback_data("why", slug)`
  - Send with `parse_mode="HTML"` (already done) + `reply_markup=keyboard`.
  - Verify `send_message` accepts `reply_markup` (may need #044's signature change).
- [ ] `webhooks/callbacks.py`: implement `handle_skip(slug, callback_query)`:
  - Find today's `pending_review` by slug + `status='open'`.
  - If not found → toast "No open problem for this slug".
  - Set `status = 'skipped'`.
  - Look up `difficulty` from `LeetCodeProblem` by slug.
  - `award_credits(session, slug, 'skipped', difficulty)` → 0.0.
  - Insert `leetcode_log` entry with `status='skipped'`.
  - Edit thread message: append "\n⏭️ Skipped", remove buttons.
  - Refresh pinned message.
  - Answer callback with toast "Skipped".
- [ ] `webhooks/callbacks.py`: implement `handle_hint(slug, callback_query)`:
  - Read `coaching_hint` from today's `daily_candidates` by slug.
  - If hint ≤200 chars → answer callback with `show_alert=True` (toast dialog).
  - If hint >200 chars → send a reply message with the hint.
  - Do NOT close the thread or remove buttons (user can still solve it).
- [ ] `webhooks/callbacks.py`: implement `handle_solution(slug, callback_query)`:
  - Same as Skip but: `status='saw_solution'`, `award_credits(..., 'saw_solution', ...)`
    → 0.25, append "📖 Solution viewed", remove buttons.
- [ ] `webhooks/callbacks.py`: implement `handle_why(slug, callback_query)`:
  - Call `commands._generate_why_explanation(slug)` (the helper extracted above).
  - Send the result as a reply message.
  - Do NOT close the thread or remove buttons.
  - Answer callback with toast "Generating explanation..." then send message.
- [ ] `tests/test_callbacks.py`: each button action, credit awarding, message
      editing, pinned refresh, "no open problem" edge case.
- [ ] `tests/test_commands.py`: verify `/why` still works after the refactor.

## Acceptance criteria
- [ ] Per-problem message has 4 buttons: Skip, Hint, Solution, Why.
- [ ] Skip: sets status='skipped', 0 credits, removes buttons, appends "Skipped".
- [ ] Hint: shows coaching hint (toast if short, reply if long), thread stays open.
- [ ] Solution: sets status='saw_solution', 0.25 credits, removes buttons.
- [ ] Why: sends LLM explanation as a reply, thread stays open.
- [ ] All actions refresh the pinned message.
- [ ] Tapping buttons on a closed thread → toast "already closed".
- [ ] Credits awarded correctly per the value table.

## Principles
- **DRY:** Why reuses the extracted `_generate_why_explanation` helper, not a copy.
- **KISS:** Hint is just re-displaying existing data, no LLM call.
- **Explicit over implicit:** Solution doesn't fake having the actual solution
  text — it just marks the outcome honestly.
