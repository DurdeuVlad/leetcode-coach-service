# #030 — Deploy to Coolify + GCP OAuth production flip

**Milestone:** M6 deploy · **Labels:** `type:infra` `area:ops` `risk:high` `prio:P0`
**Depends on:** #006, #016, #022, #026, #028, #029

## Summary
Ship the service to the homelab via Coolify, go live on the Telegram webhook,
and flip the GCP OAuth consent screen to production so the refresh token stops
expiring every 7 days.

## Context
- `docs/roadmap.md` Phase 6 is the checklist; `docs/architecture.md` §9 is the
  deploy design.
- AGENTS.md gotcha #3 + `docs/architecture.md` §6: the recurring
  `invalid_grant` root cause is the consent screen being in `Testing`; fix is
  flipping to `In production` (single-user, no verification needed).

## Tasks
- [ ] Push to a private GitHub repo.
- [ ] Coolify: create Postgres service; capture connection string.
- [ ] Coolify: create app service from the repo; set **all** env vars from
      `.env.example`; point `DATABASE_URL` at the Postgres service.
- [ ] First deploy runs `alembic upgrade head` on startup.
- [ ] Verify `GET /health` returns 200 from the public URL.
- [ ] App calls `setWebhook` on startup with
      `https://<coolify-domain>/telegram/webhook`; verify the logged response.
- [ ] Send a test message from Telegram → app logs the update.
- [ ] Flip GCP OAuth consent screen `Testing` → `In production`; re-authenticate
      the Google credential one final time; update `GOOGLE_REFRESH_TOKEN`.

## Acceptance criteria
- [ ] One full real day works end-to-end: 09:05 proposal → user picks 2 → sends
      code for one → coach feedback → the other expires at 05:05 next day.
- [ ] All four tables have real rows after that day.
- [ ] Cost log shows < $0.20 for the day (NFR-2 sanity).
- [ ] No `invalid_grant` recurrence after the production flip.

## Notes
- Do **not** commit secrets; all via Coolify env injection (NFR-4).
- Do not weaken any security controls to make CI/deploy pass — escalate
  instead.

## Principles (how they apply here)
See [#034](034-engineering-principles-layers.md). KISS is the tie-breaker.
- **KISS:** single container, single Postgres service, `alembic upgrade
  head` on startup — no blue/green, no canary, no multi-environment
  pipeline (architecture §12). The simplest deploy that satisfies the
  exit criteria is correct.
- **Fail loud:** the acceptance bar is one full real day end-to-end; if
  `invalid_grant` recurs after the production flip, that's a failure, not
  a "monitor and see."
- **Explicit over implicit / security:** every secret comes from Coolify
  env injection keyed off `.env.example` — never baked into the image,
  never committed (NFR-4).
- **YAGNI:** the GCP OAuth production flip is the **documented root-cause
  fix** for the 7-day expiry (AGENTS.md gotcha #3) — no token-refresh
  daemon, no periodic re-auth cron. Fix the cause, not the symptom.
