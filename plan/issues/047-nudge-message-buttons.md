# #047 — Nudge message + buttons

**Milestone:** M9 phase-9 · **Labels:** `type:feature` `area:flow-b` `area:telegram` `prio:P2`
**Depends on:** #042, #043, #041, #044
**Spec:** `plan/PHASE9_DESIGN.md` §Nudge Message

## Summary
Replace the stub nudge from #042 with the full nudge message: HTML formatted,
shows the deficit amount, with three buttons: Solve Now (lists open problems
with pick buttons), Accept Deficit (dismisses), Snooze to Tomorrow (suppresses
nudges until tomorrow morning).

## Context
- The 20:00 nudge job (#042) calls `maybe_send_nudge()`. In #042 this was a
  stub that sent plain text. This issue adds the full message + buttons.
- Nudge fires only when balance < 0 (you're behind).
- Solve Now: shows the open problems (pending_review with status='open') as
  a list with pick-style buttons so the user can jump straight into solving.
  If no open problems exist, toast "No problems in queue — tap /propose".
  **Depends on #044:** reuses `_build_propose_html` (or a subset of it) and
  the pick button construction. The pick handler (`handle_pick` from #044)
  is shared — tapping a pick button from the nudge list triggers the same
  two-tap pick flow.
- Accept Deficit: the user acknowledges they won't catch up today. Dismisses
  the nudge. No DB change (the deficit is real, the balance stays negative).
  Logs "accepted_deficit" for analytics.
- Snooze: stores `nudge_snoozed_until` in `bot_state` as tomorrow's date.
  The nudge job checks this before sending — if snoozed, skip today.

## Tasks
- [ ] `flows/nudge.py`: replace stub `maybe_send_nudge()` with full implementation:
  - Check `nudge_snoozed_until` in `bot_state`. If today < snoozed_until → skip.
  - Get balance. If ≥ 0 → skip.
  - Build nudge message (HTML): "⚠️ You're behind by {abs(balance)} credits.\n
    Solve 1 hard or 2 mediums to catch up."
  - Build inline keyboard:
    - `[💪 Solve Now]` → `callback_data="nudge_solve"`
    - `[⏭️ Accept Deficit]` → `callback_data="nudge_accept"`
    - `[😴 Snooze to Tomorrow]` → `callback_data="nudge_snooze"`
  - Send with `parse_mode="HTML"` + `reply_markup`.
- [ ] `webhooks/callbacks.py`: implement `handle_nudge_solve(callback_query)`:
  - Query open `pending_review` rows.
  - If none → toast "No problems in queue — use /propose".
  - If some exist → send a NEW message (don't edit the nudge) listing the
    open problems using `_build_propose_html` (from #044, adapted for a
    subset of candidates) with pick buttons built via
    `encode_callback_data("pick", slug)`. The pick handler from #044 is
    reused — same two-tap flow, same `pick_in_progress` state.
  - Answer callback with toast "Here are your open problems".
- [ ] `webhooks/callbacks.py`: implement `handle_nudge_accept(callback_query)`:
  - Edit nudge message to remove buttons, append "\n✅ Deficit accepted."
  - Log "nudge_accepted" (structlog, not DB — analytics only).
  - Answer callback with toast "Deficit accepted. Catch up tomorrow!".
- [ ] `webhooks/callbacks.py`: implement `handle_nudge_snooze(callback_query)`:
  - Store `nudge_snoozed_until` = tomorrow's date in `bot_state`.
  - Edit nudge message to remove buttons, append "\n😴 Snoozed to tomorrow."
  - Answer callback with toast "Snoozed. I'll remind you tomorrow.".
- [ ] `tests/test_nudge.py`:
  - Nudge sent when balance < 0 and not snoozed.
  - Nudge NOT sent when balance ≥ 0.
  - Nudge NOT sent when snoozed (snoozed_until > today).
  - Solve Now: lists open problems / "no problems" toast.
  - Accept Deficit: edits message, removes buttons.
  - Snooze: stores tomorrow's date, edits message.

## Acceptance criteria
- [ ] Nudge message is HTML formatted with deficit amount.
- [ ] Three buttons: Solve Now, Accept Deficit, Snooze to Tomorrow.
- [ ] Nudge only fires when balance < 0 AND not snoozed.
- [ ] Solve Now shows open problems with pick buttons (or toast if none).
- [ ] Accept Deficit dismisses the nudge, removes buttons.
- [ ] Snooze stores tomorrow's date, suppresses next nudge.
- [ ] All tests pass.

## Principles
- **KISS:** the nudge is one message, three buttons. Not a complex workflow.
- **User agency:** the user can accept the deficit or snooze. The bot informs,
  doesn't punish.
- **NFR-1:** nudge failure is fire-and-forget — a failed nudge doesn't crash
  the scheduler.
