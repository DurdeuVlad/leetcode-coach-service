# Roadmap — LeetCode Coach Service

> **Active delivery plan.** Checked items are implemented and locally
> verified. Deployment-only items stay unchecked until the operator
> completes them; "implemented" is not "shipped."

## 1. Establish the baseline

- [x] Dockerfile defaults to `entrypoint.sh` + `leetcode_coach.main:app`.
      The Coolify `Application::find(1)` deploy builds and runs the app.
- [x] Run the scheduler in-process via the `main.py` lifespan (single
      container, no separate scheduler service). The pg advisory lock
      prevents double-firing if two instances run.
- [ ] Verify the production deployment SHA after the next Coolify deploy.
- [ ] Provision a fresh production database (`DATABASE_URL` pointing at a
      new Postgres database). `entrypoint.sh` runs `alembic upgrade head`
      on startup.

**Exit criteria:** deployed SHA is recorded; the service has isolated
production infrastructure.

## 2. Schema, domain layer, and learning-only import

- [x] Create the Alembic schema and domain services for canonical
      problems, attempts, lessons, proposal batches, reviews, credit
      ledger, bot state, Telegram idempotency, conversation items, agent
      runs, and pending approvals.
- [x] Implement deterministic HTML proposal and plain-text/code-safe
      renderers.
- [x] Implement a migration command that imports only canonical problems,
      attempt history, and tutor lessons; it starts credits at zero and
      discards old operational state. *(Not used in the current cutover —
      operator chose a fresh empty database. The importer remains
      available if legacy learning data is later needed.)*

**Exit criteria:** migration count/representative tests pass; canonical
metadata is the only metadata persisted or rendered.

## 3. Agent runner, tools, approvals, and Telegram adapter

- [x] Implement a serialized per-chat Agents SDK runner using Terra,
      bounded to eight turns and three concurrent read tools.
- [x] Add typed read tools, serial write tools, PostgreSQL run state,
      approval persistence/resume/expiry, and duplicate-update protection.
- [x] Add the read-only, once-per-run Terra-to-Sol advisor tool and
      telemetry.

**Exit criteria:** approval, restart/resume, stale callback, idempotency,
tool validation, and Sol-bound tests pass.

## 4. Scheduling and feature parity

- [x] Rebuild proposal/refill, coaching, lessons, credits/tax, nudges,
      expiry, extension, status, hints, explanations, reattempts, and
      follow-ups on the domain layer.
- [x] Run the terminal/live real-model suite against SQLite and
      PostgreSQL.
- [x] Validate the Telegram adapter locally against the official Bot API
      contract, including webhook authentication, HTML/entity limits,
      callback byte limits, edit/answer methods, retries, and duplicate
      updates.

**Exit criteria:** all acceptance scenarios pass without model-generated
markup or fabricated state.

## 5. Cutover

- [ ] Deploy to Coolify (push to `master` triggers `deploy.yml`). The
      Dockerfile defaults to the app with in-process scheduler;
      `entrypoint.sh` runs migrations on the fresh database and starts
      the app.
- [ ] Set `DATABASE_URL` in Coolify to the fresh Postgres database.
- [ ] Run the evidence-gated
      [`production verification prompt`](./production-verification-prompt.md)
      with the independently known expected production SHA; record the resulting
      SHA chain and safety attestations.
- [ ] Verify the webhook is registered and `/health` returns
      `{"status":"ok"}` against the production URL.

**Exit criteria:** production cutover is verified.

## Guiding principles

- **Port the spec, don't redesign it.** Prompts carry over verbatim. Flow
  logic carries over as functions. The only redesign is the runtime.
- **Each phase ends with a working thing on the homelab.** No phase is
  "scaffold only." If a phase can't be deployed, it's too big.
- **Tests come with the code, not after.** The coach pass prompt is the
  highest-risk piece (LLM output is non-deterministic); it gets a
  golden-output test suite.
