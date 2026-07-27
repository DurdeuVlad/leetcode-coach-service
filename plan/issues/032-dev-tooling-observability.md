# #032 — Dev tooling & observability polish

**Milestone:** M0 bootstrap · **Labels:** `type:infra` `area:ops` `prio:P1`
**Depends on:** #005, #006

## Summary
Close the small local-dev and observability gaps called out in
`docs/architecture.md` §10–§11 that don't belong to any single flow issue:
the local Telegram tunnel helper, a documented one-command run, and a
consistent per-run structured-log shape.

## Context
- `docs/architecture.md` §10 (local development) references
  `uv run python -m leetcode_coach.scripts.ngrok_tunnel` to expose `:8000` and
  set the webhook for local Telegram testing, plus a one-command dev run.
- `docs/architecture.md` §11 (observability): every flow run should log
  `flow=a|b|expiry`, `run_id`, `outcome`, `duration_ms`, and (for LLM calls)
  `llm_model`, `tokens_in`, `tokens_out`; a daily rollup logs `cost_usd` so
  the <$10/month NFR is verifiable from logs alone.

## Tasks
- [ ] `scripts/ngrok_tunnel.py` — exposes `:8000` via a tunnel and calls the
      Telegram `set_webhook` (#009) with the resulting public URL, for local
      Flow B testing.
- [ ] Document the one-command local run in `README.md` (matching §10:
      `uv sync` → `docker compose up -d postgres` → `alembic upgrade head` →
      `uvicorn ... --reload`).
- [ ] A small logging helper/decorator that stamps `flow`, `run_id`, `outcome`,
      `duration_ms` around each flow entry point; adopt it in #016, #024, #028,
      #029.
- [ ] Daily `cost_usd` rollup log line derived from the per-call token logs
      (#010).

## Acceptance criteria
- [ ] Running the tunnel script locally results in a live webhook the bot can
      reach; a test message hits `handle_update`.
- [ ] `README.md` documents the exact one-command dev sequence from §10.
- [ ] Each flow run emits one structured log with `flow`, `run_id`, `outcome`,
      `duration_ms`; LLM calls include model + token counts.
- [ ] A daily rollup log line reports `cost_usd`.

## Notes
- Observability stays log-only in v1 (no metrics backend); Loki/Grafana is
  deferred to #031 (architecture §11).
- The logging helper is additive — it must not change flow behavior.

## Principles (how they apply here)
See [#034](034-engineering-principles-layers.md). KISS is the tie-breaker.
- **KISS:** observability is **log-only** in v1 — no metrics backend, no
  OpenTelemetry collector, no Loki/Grafana (those are #031, triggered by
  real need). A decorator that stamps `flow`/`run_id`/`duration_ms` is the
  simplest thing that makes the NFR-2 cost verifiable from logs.
- **YAGNI:** the `ngrok_tunnel` script exists because Flow B testing needs
  a real webhook — concrete present need, not speculative tooling. No
  "dev dashboard," no fake-update generator.
- **Single Responsibility:** the logging helper stamps context; it does
  **not** alter flow behavior, retry, or swallow errors (additive only).
- **Explicit over implicit:** cost is a **daily rollup log line** derived
  from per-call token logs — not a hidden counter, not a sidecar service.
