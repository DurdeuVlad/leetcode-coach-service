# #042 — Daily tax + nudge + queue refill scheduler jobs

**Milestone:** M9 phase-9 · **Labels:** `type:feature` `area:scheduler` `prio:P0`
**Depends on:** #041
**Spec:** `plan/PHASE9_DESIGN.md` §New Scheduler Jobs

## Summary
Replace the old 09:05 propose-only cron with three new jobs: midnight tax
accrual, 20:00 evening nudge (if behind), and 09:05 queue refill check
(propose only if open queue < 3). Move expiry sweep from 05:05 to 22:00.

## Context
- The budget system needs a daily tax accrual at 00:00 Europe/Bucharest.
- The nudge at 20:00 checks balance; if < 0, sends a message with buttons
  (Solve Now / Accept Deficit / Snooze). The nudge message + buttons are
  built in #047; this issue only wires the scheduler job + the conditional
  check.
- The 09:05 job changes from "always propose" to "propose only if open
  queue < 3". Open queue = `pending_review` rows with `status='open'`.
- Expiry moves to 22:00 and changes behavior: it doesn't delete problems
  from the queue — it only makes the propose message buttons inert. The
  actual expiry behavior change is in #049; this issue only moves the cron.

## Tasks
- [ ] `scheduling/cron.py`: add `_safe_daily_tax` wrapper → calls
      `accrue_daily_tax()`. Register at `CronTrigger(hour=0, minute=0)`.
- [ ] `scheduling/cron.py`: add `_safe_evening_nudge` wrapper → calls
      `flows.nudge.maybe_send_nudge()`. Register at `CronTrigger(hour=20, minute=0)`.
      (The nudge function is a stub in this issue — just checks balance and
      sends a plain-text message. #047 adds the buttons + full message.)
- [ ] `scheduling/cron.py`: replace `_safe_propose_5` with
      `_safe_queue_refill_check` → calls `flow_a.propose_5()` only if
      `SELECT COUNT(*) FROM pending_review WHERE status='open'` < 3.
      Keep the same 09:05 cron time, same job ID `flow_a_propose_5`.
- [ ] `scheduling/cron.py`: move `expiry_sweep` from `hour=5, minute=5` to
      `hour=22, minute=0`.
- [ ] `flows/nudge.py` (new, stub): `maybe_send_nudge()` — check balance
      via `flows.credits.get_balance()` (from #041, which this issue depends
      on), if < 0 send a plain-text message "You're behind by X credits."
      The full nudge with buttons is #047.
- [ ] `tests/test_cron.py` (or extend existing): verify all 4 jobs registered
      with correct triggers. Verify queue_refill_check skips when queue ≥ 3.

## Acceptance criteria
- [ ] `daily_tax` job registered at 00:00 Europe/Bucharest.
- [ ] `evening_nudge` job registered at 20:00 Europe/Bucharest.
- [ ] `queue_refill_check` job registered at 09:05 (replaces old propose).
- [ ] `expiry_sweep` job moved to 22:00.
- [ ] Queue refill check does NOT propose when ≥3 open problems exist.
- [ ] Queue refill check DOES propose when <3 open problems exist.
- [ ] Nudge sends a message when balance < 0, does nothing when balance ≥ 0.
- [ ] All existing scheduler tests still pass.

## Principles
- **KISS:** the scheduler only decides WHEN, not WHAT. Business logic stays
  in the flow modules.
- **NFR-1:** each job is wrapped in the `_safe_*` error handler — a crash
  sends one alert, doesn't kill the scheduler thread.
