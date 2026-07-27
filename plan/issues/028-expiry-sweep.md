# #028 — Expiry sweep + cron + tests

**Milestone:** M4 expiry · **Labels:** `type:feature` `area:expiry` `area:scheduling` `prio:P0`
**Depends on:** #011, #017, #022

## Summary
The 05:05 daily sweep that marks unanswered problems expired, annotates their
Google Tasks, and sends one summary message.

## Context
- `docs/business-requirements.md` FR-3: at 05:05 Europe/Bucharest sweep today's
  `pending_review` where `status = open`; per row set `status = expired` and
  update the Google Task notes with "Expired without reply on <date>"
  (**do not delete** the task); send one Telegram summary (or "No problems
  expired today").
- `docs/architecture.md` §4: cron `5 5 * * *`.

## Tasks
- [ ] `flows/expiry.py` `sweep_expired()`:
  1. Select today's `pending_review` rows with `status = open`.
  2. For each: set `status = expired`; `google_tasks.update_task(...,
     notes_append="Expired without reply on <date>")` (append, not delete).
  3. Send one Telegram summary listing expired problems, or the "none" message.
- [ ] Register APScheduler job `5 5 * * *` (add to #017's scheduler).
- [ ] Wrap in the #008 job-error wrapper (layer 3 alert).
- [ ] `tests/test_expiry.py` with 0, 1, and 2 open rows.

## Acceptance criteria
- [ ] 2 open rows → both `expired`, both tasks annotated (not deleted), one
      summary sent.
- [ ] 0 open rows → "No problems expired today" sent.
- [ ] Google auth failure routes to the distinct alert, not a generic crash.
- [ ] Job runs at 05:05 in the configured timezone.

## Notes
- Not user-facing latency-sensitive (NFR-3: no target).

## Principles (how they apply here)
See [#034](034-engineering-principles-layers.md). KISS is the tie-breaker.
- **Single Responsibility:** `flows/expiry.py` orchestrates the sweep; the
  DB update, Google Task annotation, and Telegram summary each go through
  their own layer — no raw SQL or HTTP here.
- **Explicit over implicit:** the cron string `5 5 * * *` and the timezone
  are explicit (Europe/Bucharest), not inferred from system locale.
- **Fail loud / typed errors:** wrapped in the #008 job-error wrapper;
  `GoogleAuthExpiredError` routes to the distinct alert, not a generic
  crash. Never silently mark rows expired if a step fails mid-loop.
- **YAGNI:** "do not delete the Google Task" is the spec — no
  "soft-delete then hard-delete" machinery, just `notes_append`.
