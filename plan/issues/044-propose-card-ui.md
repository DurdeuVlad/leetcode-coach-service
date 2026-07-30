# #044 — Propose message: card-style format + pick buttons

**Milestone:** M9 phase-9 · **Labels:** `type:feature` `area:flow-a` `area:telegram` `prio:P1`
**Depends on:** #043, #041
**Spec:** `plan/PHASE9_DESIGN.md` §Message Format Examples

## Summary
Rewrite the propose message to card-style HTML with difficulty badges,
hyperlinked titles, and blockquotes for Why/Hint. Add inline keyboard
`[1] [2] [3] [4] [5]` with `pick:<slug>` callbacks. Implement the direct-pick
flow: first tap stores state + edits message, second tap triggers Flow B,
cancel resets.

## Context
- The propose message is the first thing the user sees every morning.
  Currently a wall-of-text MarkdownV2 built by `_parse_candidates()` in
  `flow_a.py` (which returns `(markdown, candidates)` from the LLM response).
  There is NO `_build_propose_message()` function — the markdown is the LLM's
  raw output. This issue adds a new `_build_propose_html(candidates)` function
  and changes `propose_5` to send that instead of the LLM's markdown.
- `propose_5` currently sends via `send_message(target_chat, markdown,
  parse_mode="MarkdownV2")` (line 253) and does NOT capture the returned
  `message_id`. This issue must capture it and store it in `bot_state` as
  `propose_message_id` (needed by #049's expiry edit).
- `propose_5` returns the markdown string (line 277). Switching to HTML
  changes the return value. Tests that assert on the return value will
  break and must be updated.
- Pick flow (decision #2): direct pick, no confirm. Tap one → message edits
  to "Pick 1/2" + Cancel button. Tap second → Flow B fires, message edits
  to summary, buttons removed. Cancel → reset to original 5 buttons.
- Pick state stored in `bot_state` key `pick_in_progress` as JSON:
  `{"first_pick_slug": "two-sum", "first_pick_index": 3, "propose_message_id": 123}`.
- After picks: message stays as reference (decision #3), buttons removed.
- Stale button validation (decision #9): if tapped slug not in today's
  `daily_candidates`, toast "This list has expired — run /propose".
- **Flow B integration gap:** `_pick_parse_path(chat_id, text)` takes TEXT
  like `"2 5"` and parses it with regex (line 276). It does NOT accept slugs.
  To call it from the button handler with slugs, we must extract a new
  helper `_create_threads_for_candidates(chat_id, chosen: list[DailyCandidate],
  dry_run=False)` from the existing code (lines 316-378), then have both
  `_pick_parse_path` and the button handler call it. This refactor is part
  of this issue.

## Tasks
- [ ] `flows/flow_a.py`: add NEW function `_build_propose_html(candidates:
      list[DailyCandidate]) -> str` — card-style HTML:
  - Header: `<b>📊 Today's Problems</b>`
  - Per candidate: `<b>N. <a href="{url}">{title}</a></b> {badge} {difficulty}\n<i>{tags}</i>\n<blockquote><b>Why:</b> {reasoning}\n<b>Hint:</b> {coaching_hint}</blockquote>`
  - Difficulty badges: 🔴 hard, 🟡 medium, 🟢 easy
  - Footer: `<b>Credits: {format_balance(balance)}</b>` (from #041)
  - Returns HTML string (to be sent with `parse_mode="HTML"`).
  - **HTML-escape** all interpolated fields (`html.escape(title)`,
    `html.escape(reasoning)`, `html.escape(coaching_hint)`) to prevent
    Telegram parse errors on titles containing `<`, `>`, `&`.
- [ ] **Register action handlers** (per #043's `register_action`):
      `register_action("pick", handle_pick)`, `register_action("cancel",
      handle_cancel)`. This wiring is mandatory, not implicit.
- [ ] `flows/flow_a.py`: modify `propose_5`:
  - After `_parse_candidates` + `_validate_candidates`, call
    `_build_propose_html(candidates)` instead of using the LLM's `markdown`.
  - Capture the return of `send_message(...)` as `propose_message_id` and
    store it in `bot_state` via `set_state("propose_message_id", str(id))`.
  - Build inline keyboard with 5 buttons (button text = `1`..`5`,
    `callback_data` = `encode_callback_data("pick", slug)` from #043).
    Pass as `reply_markup` to `send_message` (may need to add `reply_markup`
    param to `send_message` if not already present — check signature).
  - Return the HTML string (was returning markdown; update callers/tests).
- [ ] `flows/flow_b.py`: extract `_create_threads_for_candidates(chat_id,
      chosen: list[DailyCandidate], dry_run=False) -> list[dict]` from the
      existing `_pick_parse_path` body (lines 316-378). Refactor
      `_pick_parse_path` to: parse text → nums → candidates → call the new
      helper. This is a pure refactor — no behavior change to the text path.
- [ ] `webhooks/callbacks.py`: implement `handle_pick(slug, callback_query)`:
  - Read `pick_in_progress` from `bot_state`.
  - If no pick in progress (first tap):
    - Store `pick_in_progress` JSON in `bot_state`.
    - Edit propose message text to "✅ Pick 1/2: {title}\nTap your second choice."
    - Edit reply markup: remove the picked button, add `[Cancel]` button.
    - Answer callback with toast "Picked: {title}".
  - If pick in progress (second tap):
    - Read first pick slug from state.
    - Look up both `DailyCandidate` rows by slug (today's).
    - Call `flow_b._create_threads_for_candidates(chat_id, [c1, c2])` (the
      helper extracted above — NOT `_pick_parse_path`, which takes text).
    - Edit propose message to "✅ Picks: {title1}, {title2}\nCheck the threads below 👇".
    - Remove all buttons.
    - Clear `pick_in_progress` from `bot_state`.
    - Answer callback with toast "Picks confirmed — threads created!".
  - Stale check: if slug not in today's `daily_candidates`, toast "expired".
- [ ] `webhooks/callbacks.py`: implement `handle_cancel(callback_query)`:
  - Clear `pick_in_progress` from `bot_state`.
  - Restore propose message to original card-style text + 5 buttons.
  - Answer callback with toast "Cancelled".
- [ ] `integrations/telegram.py`: `edit_message_reply_markup` (from #043)
  used to swap buttons. `edit_message_text` (existing) used to change text.
  Verify `send_message` accepts `reply_markup` — if not, add the param.
- [ ] `tests/test_flow_a.py`: update propose message format tests for HTML
  output and new return value. Test `propose_message_id` stored in bot_state.
- [ ] `tests/test_flow_b.py`: verify `_create_threads_for_candidates` helper
  works identically to the old inline code (refactor is behavior-preserving).
- [ ] `tests/test_callbacks.py`: pick flow state transitions, cancel, stale.

## Acceptance criteria
- [ ] Propose message renders as card-style HTML with badges, hyperlinks, blockquotes.
- [ ] Titles are clickable hyperlinks to LeetCode.
- [ ] Difficulty badges (🔴/🟡/🟢) display correctly.
- [ ] Five pick buttons `[1] [2] [3] [4] [5]` appear below the message.
- [ ] `propose_message_id` stored in `bot_state` after sending.
- [ ] First tap: message edits to "Pick 1/2", picked button removed, Cancel appears.
- [ ] Second tap: `_create_threads_for_candidates` fires (not `_pick_parse_path`),
      message edits to summary, all buttons removed.
- [ ] Cancel: resets to original 5 buttons, clears pick state.
- [ ] Stale button (yesterday's propose): toast "expired", no action.
- [ ] `pick_in_progress` state survives a restart (stored in DB).
- [ ] Credits balance shown in footer.
- [ ] `_pick_parse_path` text path still works (refactor is behavior-preserving).

## Principles
- **KISS:** the pick flow is two taps + optional cancel. No multi-step wizards.
- **Resilience:** pick state in DB, not memory. Survives restarts.
- **Explicit over implicit:** stale buttons are validated, not assumed valid.
