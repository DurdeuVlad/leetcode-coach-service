# #048 — Pinned message: add credits display

**Milestone:** M9 phase-9 · **Labels:** `type:feature` `area:pinned` `prio:P2`
**Depends on:** #041, #039
**Spec:** `plan/PHASE9_DESIGN.md` §Pinned Message

## Summary
Add the credit balance to the pinned progression message. Format as
`Credits: +3.5 (ahead 1 day)` or `Credits: -1.0 (behind 0.5 days)`. Add
new refresh triggers: after tax accrual, after credit awarding (solve/
skip/saw_solution), after nudge dismiss.

## Context
- The pinned message (#039) currently shows: proposed/picked/coached/expired
  counts, active lessons, streak. Add a credits line.
- **The pinned message is PLAIN TEXT, not HTML.** `pinned.py` calls
  `send_message(chat_id, snapshot)` with NO `parse_mode` (line 149), and
  `_build_snapshot` returns plain text with emoji + `\n` joins (line 93-99).
  The credits line must be plain text too — no `<b>` tags, no HTML entities.
  This is intentional: pinned messages are read-only reference, and plain
  text avoids HTML-escaping issues with dynamic values.
- The balance is a live SUM of the credit ledger — no caching needed.
- "Ahead 1 day" / "behind 0.5 days" = `abs(balance) / 2` (since daily tax is 2).
  Round to 1 decimal. If balance is exactly 0, show "Credits: 0 (on track)".
- New refresh triggers beyond #039's (Flow A, pick, coach):
  - After `accrue_daily_tax` (midnight — but user is likely asleep, so the
    next morning interaction will refresh it anyway; still, refresh for
    correctness).
  - After `award_credits` (called from coach pass + button handlers).
  - After nudge dismiss (Accept/Snooze — the balance hasn't changed but the
    user is looking at the pinned message).

## Tasks
- [ ] `flows/pinned.py`: in `_build_snapshot()`, add credits line:
  - `balance = get_balance(session)`
  - `credits_line = f"Credits: {format_balance(balance)}"`
  - Insert after the streak line (or before — user preference, but after
    streak keeps the "progress" grouping).
- [ ] `flows/pinned.py`: ensure `refresh_pinned_message()` is called after:
  - `accrue_daily_tax` (in `scheduling/cron.py` `_safe_daily_tax`).
  - `award_credits` — call refresh at the end of `award_credits` if the
    function is called outside a flow that already refreshes. Simplest:
    have each caller (coach pass, button handlers) call refresh, same as
    #039's pattern.
- [ ] `flows/credits.py`: `format_balance(balance)` — already specified in
      #041. Ensure it handles the "on track" (0.0) case.
- [ ] `tests/test_pinned.py`: update snapshot tests to include credits line.
      Test: positive balance, negative balance, zero balance.

## Acceptance criteria
- [ ] Pinned message shows `Credits: +X.X (ahead N days)` when balance > 0.
- [ ] Pinned message shows `Credits: -X.X (behind N days)` when balance < 0.
- [ ] Pinned message shows `Credits: 0 (on track)` when balance = 0.
- [ ] Pinned message refreshes after credit awarding (solve/skip/saw_solution).
- [ ] Pinned message refreshes after tax accrual.
- [ ] Existing pinned message tests updated and passing.

## Principles
- **KISS:** one new line in the snapshot. No charts, no history.
- **DRY:** `format_balance` is shared with the propose footer (#044) and
  nudge message (#047).
