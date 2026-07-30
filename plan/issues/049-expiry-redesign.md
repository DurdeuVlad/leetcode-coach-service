# #049 — Expiry redesign: 22:00 + extend button

**Milestone:** M9 phase-9 · **Labels:** `type:feature` `area:flow-a` `area:expiry` `prio:P2`
**Depends on:** #042, #044
**Spec:** `plan/PHASE9_DESIGN.md` §Expiry redesign

## Summary
Move the expiry sweep to 22:00 and change its behavior: instead of deleting
problems from the queue, it only makes the propose message buttons inert
(edits the message to remove buttons + appends a notice). Add an
`[Extend to Tomorrow]` button on the expired propose message so the user
can re-enable picking the next day.

## Context
- **Old expiry behavior (being REMOVED by this issue):** `sweep_expired()` in
  `flows/expiry.py` did three things:
  1. Set `pending_review.status = 'expired'` for all open rows (line 96).
  2. Appended an "Expired without reply" note to each problem's Google Task
     via `google_tasks.update_task` (line 32 import, line 89 area).
  3. Sent a Telegram summary message listing the expired problems
     (line 101-110, "Expired N problem(s) without reply...").
  All three are removed by this issue. The function is rewritten to only
  edit the propose message buttons.
- **New expiry (22:00 same day):** the propose message buttons go inert, but
  the `pending_review` rows stay 'open'. The user can still coach them the
  next day by running /propose again (which will include them in the
  candidates) or by using the Extend button.
- This aligns with the budget system: problems don't "expire" — they stay
  in the queue until solved, skipped, or saw_solution. The only thing that
  expires is the propose message's buttons (because the daily list is stale).
- **Google Tasks integration:** the `update_task` import and call are
  removed from `expiry.py`. The Google Tasks integration itself stays (it's
  used in Flow B's coach pass); only the expiry-triggered note append is
  removed. If a problem is never solved, its Google Task note simply won't
  get an expiry annotation — that's acceptable per the budget model.
- **Summary message:** the "Expired N problem(s)" Telegram message is
  removed. The user gets the expiry notice inline on the propose message
  instead (the "⏰ Buttons expired" append).
- Extend: re-adds the pick buttons to the propose message. Stores
  `propose_extended_until` in `bot_state` as tomorrow's date. The next
  expiry sweep will check this — if extended, don't re-expire until that date.

## Tasks
- [ ] `flows/expiry.py`: rewrite `sweep_expired()`:
  - **Remove** the `google_tasks.update_task` import (line 32) and the
    per-row Google Task note append (line 89 area).
  - **Remove** the Telegram summary message (lines 101-110).
  - **Remove** the `pending_review.status = 'expired'` mutation (line 96).
  - Find today's propose message (from `bot_state.propose_message_id`,
    stored by #044).
  - If found: edit the message to remove all buttons, append
    "\n\n⏰ Buttons expired — problems stay in your queue. Tap Extend to
    pick again tomorrow."
  - Add `[Extend to Tomorrow]` button with `callback_data="extend"`.
  - Do NOT change `pending_review.status` — leave as 'open'.
  - Refresh pinned message (the counts now reflect that propose is stale).
  - Check `propose_extended_until` in `bot_state` — if set and > today,
    skip the expiry (the user extended).
  - Return value changes: was `int` (expired count); now `bool` (edited
    the message or not). Update callers in `scheduling/cron.py`.
- [ ] `webhooks/callbacks.py`: implement `handle_extend(callback_query)`:
  - Store `propose_extended_until` = tomorrow in `bot_state`.
  - Re-add the 5 pick buttons to the propose message (same as #044's
    original keyboard — reuse the keyboard builder).
  - Edit message text to remove the "⏰ Buttons expired" notice.
  - Answer callback with toast "Extended to tomorrow — pick when ready!".
- [ ] `scheduling/cron.py`: update `_safe_sweep_expired` — the return type
      changed from `int` to `bool`. Update any logging that referenced
      `expired_count`.
- [ ] `tests/test_expiry.py`: update tests for new behavior:
  - Expiry edits message, removes buttons, adds Extend button.
  - Expiry does NOT change `pending_review.status`.
  - Expiry does NOT call `google_tasks.update_task`.
  - Expiry does NOT send a summary message.
  - Extend re-adds buttons, stores tomorrow's date.
  - Expiry skips if propose_extended_until > today.

## Acceptance criteria
- [ ] Expiry runs at 22:00 (cron moved in #042).
- [ ] Expiry edits the propose message: removes pick buttons, adds Extend button.
- [ ] Expiry does NOT change `pending_review.status` (stays 'open').
- [ ] Expiry does NOT call `google_tasks.update_task` (import removed).
- [ ] Expiry does NOT send a separate summary message.
- [ ] Extend button re-adds the 5 pick buttons.
- [ ] Extend stores `propose_extended_until` = tomorrow in bot_state.
- [ ] Subsequent expiry sweep skips if `propose_extended_until` > today.
- [ ] `sweep_expired` return type changed from `int` to `bool`; callers updated.
- [ ] All expiry tests updated and passing.

## Principles
- **KISS:** expiry is now just "make buttons inert + offer extend". No
  status changes, no queue mutations.
- **User agency:** the user decides whether to extend or let the list go.
  Problems persist either way.
- **Consistency:** the budget system already decoupled "problems in queue"
  from "daily cycle". Expiry now matches that decoupling.
