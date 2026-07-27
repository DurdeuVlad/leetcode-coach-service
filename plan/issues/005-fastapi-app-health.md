# #005 — FastAPI app, lifespan, `/health`

**Milestone:** M0 bootstrap · **Labels:** `type:infra` `prio:P0`
**Depends on:** #002, #003

## Summary
The FastAPI application entrypoint with a lifespan handler (where the scheduler
will later start/stop) and a health endpoint.

## Context
- `docs/architecture.md` §4: one container, APScheduler runs **in-process**
  via the app lifespan. §9: `/health` returns 200 iff scheduler running + DB
  reachable. §11: structured JSON logs (structlog) to stdout.

## Tasks
- [ ] `src/leetcode_coach/main.py` — `app = FastAPI(lifespan=...)`.
- [ ] Lifespan: open DB engine on startup, dispose on shutdown. Leave a clearly
      marked hook to start/stop the APScheduler instance (implemented in #017).
- [ ] Configure structlog → JSON to stdout, level from `LOG_LEVEL`.
- [ ] `GET /health` → 200 with `{status, db, scheduler}`; 503 if DB
      unreachable or scheduler not running.

## Acceptance criteria
- [ ] `uv run uvicorn leetcode_coach.main:app` boots without error.
- [ ] `GET /health` returns 200 when DB is up.
- [ ] `GET /health` returns 503 when DB is unreachable (verified in a test with
      a bad `DATABASE_URL`).
- [ ] Logs are single-line JSON.

## Principles (how they apply here)
See [#034](034-engineering-principles-layers.md). KISS is the tie-breaker.
- **KISS:** `/health` is a simple 200/503 with a tiny JSON body — no metrics
  endpoint, no readiness/liveness split for a single-user service.
- **Single Responsibility:** `main.py` wires the app + lifespan only. Business
  logic lives in `flows/`; the lifespan just starts/stops infra.
- **Explicit over implicit:** structured JSON logs configured once here; the
  scheduler start hook is a named, visible seam (filled by #017).

## Notes
- The scheduler wiring is a placeholder until #017; `/health`'s scheduler check
  may report "not started" until then, but the endpoint must exist now.
