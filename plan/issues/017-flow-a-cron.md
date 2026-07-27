# #017 — APScheduler job for Flow A (09:05) + scheduler wiring

**Milestone:** M2 flow-a · **Labels:** `type:infra` `area:scheduling` `prio:P0`
**Depends on:** #005, #016

## Summary
Stand up the in-process APScheduler and register the daily Flow A job. Also
completes the scheduler side of `/health` started in #005.

## Context
- `docs/architecture.md` §4: APScheduler AsyncIO scheduler runs in-process,
  started/stopped by the FastAPI lifespan. Flow A trigger: `5 9 * * *`
  **Europe/Bucharest** (`TIMEZONE`).
- All cron jobs live here (#017 registers Flow A; #028 expiry, #029 weekly add
  theirs to the same scheduler).
- Escaped job errors must alert (layer 3, #008 job wrapper).

## Tasks
- [ ] `scheduling/cron.py` — build the scheduler, timezone from settings,
      register `propose_5` at `5 9 * * *`.
- [ ] Start/stop the scheduler in the app lifespan (#005 hook).
- [ ] Wrap the job in the #008 error wrapper so failures send exactly one alert.
- [ ] `/health` now reports scheduler running = true.

## Acceptance criteria
- [ ] On app startup the scheduler is running and the Flow A job is registered
      with the correct cron + timezone.
- [ ] `/health` returns scheduler = running.
- [ ] A raised error inside the job triggers one `send_alert` (tested).
- [ ] Manual invocation of `propose_5()` still works independently of the cron.

## Principles (how they apply here)
See [#034](034-engineering-principles-layers.md). KISS is the tie-breaker.
- **Single Responsibility:** `scheduling/cron.py` only decides *when* jobs fire
  and delegates to flow functions. No business logic in the scheduler.
- **KISS:** one in-process scheduler for all cron jobs — no separate worker,
  no queue (architecture §12).
- **Explicit over implicit:** timezone comes from `TIMEZONE` settings, cron
  strings are literal; the job is wrapped in the #008 handler so failures alert.

## Notes
- Keep the scheduler single-instance; one process, one scheduler
  (architecture §12).
