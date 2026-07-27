# #006 — Dockerfile + docker-compose

**Milestone:** M0 bootstrap · **Labels:** `type:infra` `area:ops` `prio:P0`
**Depends on:** #001, #005

## Summary
A single-image multi-stage Dockerfile and a local `docker-compose.yml` that
brings up app + Postgres for development.

## Context
- `docs/architecture.md` §2/§9: one Docker image, multi-stage (uv install →
  slim runtime), deployed on Coolify, 256MB RAM target.
- §10 local dev flow: `docker compose up -d postgres`, then alembic, then
  uvicorn.

## Tasks
- [ ] `Dockerfile` — stage 1 `uv sync` into a venv; stage 2 slim runtime copying
      only the venv + `src/`. Runs uvicorn on `:8000`.
- [ ] Non-root user in the runtime stage.
- [ ] `docker-compose.yml` — `postgres` service (named volume) + `app` service
      wired via `DATABASE_URL`, `.env` file support.
- [ ] Container `HEALTHCHECK` hitting `/health`.

## Acceptance criteria
- [ ] `docker compose up` → both services start; app becomes healthy.
- [ ] `/health` returns 200 from the running container.
- [ ] Image builds reproducibly from a clean checkout.

## Principles (how they apply here)
See [#034](034-engineering-principles-layers.md). KISS is the tie-breaker.
- **KISS:** one image, one process (uvicorn + in-process scheduler); compose is
  just app + postgres for local dev.
- **YAGNI:** no orchestration, no sidecars, 256MB target — matches the
  single-user, mostly-idle workload (architecture §9/§12).
- **Security:** non-root runtime user; no secrets baked into the image — all
  injected via env at runtime (NFR-4).

## Notes
- No app-level secrets baked into the image; all via env at runtime.
