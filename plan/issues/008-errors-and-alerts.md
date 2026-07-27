# #008 — Typed error hierarchy + `send_alert`

**Milestone:** M1 integrations · **Labels:** `type:infra` `area:integrations` `prio:P0`
**Depends on:** #002

## Summary
A typed exception hierarchy plus a `send_alert(message)` helper that posts to
the operator's Telegram chat. This is the backbone of the three-layer
reliability model.

## Context
- `docs/business-requirements.md` NFR-1 three layers:
  1. retry on transient failures (handled per client via tenacity),
  2. **typed error branches** for known non-recoverable failures (esp. Google
     `invalid_grant`) → distinct alert,
  3. **global catch** → one Telegram alert.
- **Forbidden:** "log with estimated defaults" to paper over infra failures.
- `docs/architecture.md` §6 names `GoogleAuthExpiredError`.

## Tasks
- [ ] `src/leetcode_coach/errors.py`:
  - Base `CoachError`.
  - `GoogleAuthExpiredError` (routed to a **distinct** alert, not the global
    handler).
  - `YouTubeDisabled`, `LeetCodeFetchError`, `LLMUnavailableError` as needed by
    later clients.
- [ ] `send_alert(message)` — posts to Telegram `TELEGRAM_CHAT_ID`. Must be
      safe to call from the global handler (best-effort, never raises).
- [ ] A FastAPI global exception handler + a helper wrapper for scheduled jobs
      so escaped errors in cron jobs also alert (layer 3).

## Acceptance criteria
- [ ] `GoogleAuthExpiredError` carries the exact operator-facing message
      "Google auth expired — re-authenticate the Google credential".
- [ ] `send_alert` failures are swallowed and logged, never re-raised.
- [ ] Unit test: an unhandled error in a job wrapper triggers exactly one
      `send_alert`.

## Principles (how they apply here)
See [#034](034-engineering-principles-layers.md). KISS is the tie-breaker.
- **Fail loud, fail typed:** this issue *is* the embodiment of that principle —
  typed exceptions + alerts, and the explicit ban on "log estimated defaults".
- **KISS:** a thin exception hierarchy + one best-effort `send_alert`. No error
  framework, no severity taxonomy beyond what NFR-1 needs.
- **Single Responsibility:** `errors.py` defines error types + alerting only; it
  does not decide flow control (flows catch and route).
- **Interface Segregation:** `send_alert(message)` is a one-method surface,
  callable from anywhere without dragging in the Telegram client's full API.

## Notes
- `send_alert` may import the Telegram client (#009) lazily to avoid a cycle.
