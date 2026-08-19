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
      runs, durable coaching memory, attempt revisions, and follow-ups.
- [x] Implement deterministic HTML proposal and plain-text/code-safe
      renderers.
- [x] Implement a migration command that imports only canonical problems,
      attempt history, and tutor lessons; it starts credits at zero and
      discards old operational state. *(Not used in the current cutover —
      operator chose a fresh empty database. The importer remains
      available if legacy learning data is later needed.)*

**Exit criteria:** migration count/representative tests pass; canonical
metadata is the only metadata persisted or rendered.

## 3. Autonomous agent runner, tools, and Telegram adapter

- [x] Implement a serialized per-chat Agents SDK runner using Terra,
      bounded to 16 turns by default (32 maximum) and three concurrent reads.
- [x] Add typed parallel reads, serial immediate writes, fresh-run conversation
      state, and duplicate-update protection. Live approval/resume is removed.
- [x] Add the read-only, once-per-run Terra-to-Sol advisor tool and
      telemetry.

**Exit criteria:** immediate-write, callback replay, idempotency, tool validation,
and Sol-bound tests pass.

## 4. Scheduling and feature parity

- [x] Rebuild proposal/refill, coaching, lessons, credits/tax, nudges,
      expiry, extension, status, hints, explanations, reattempts, and
      follow-ups on the domain layer.
- [ ] Re-run the terminal/live real-model suite against SQLite and PostgreSQL
      for the 2026-08-16 autonomy release. The earlier V1 proof does not verify
      the current behavior.
- [x] Validate the Telegram adapter locally against the official Bot API
      contract, including webhook authentication, HTML/entity limits,
      callback byte limits, edit/answer methods, retries, and duplicate
      updates.
- [x] Replace the controller's procedural rulebook with a lean coaching role,
      personality, and curriculum playbook. Move only mechanical integrity
      invariants into code.
- [x] Allow exact agent-supplied problem identity metadata for atomic recording
      and proposals when the local registry is empty or lookup is unavailable.
- [x] Support today/past attempt dates and flexible proposal/pick counts without
      rigid pool, mix, eligibility, solved-state, or open-review caps.
- [x] Add callback-idempotent toggle + Done selection and checkpointed at-least-once
      informational pagination under Telegram's 4,096-visible-character limit.
- [x] Enforce the mechanical 20-candidate Telegram controller limit in tool schemas
      and before database writes.
- [x] Add exact `start_problem`, flexible catalog modes, durable coaching memory,
      rich attempt history/correction/reversal, and UTC-backed follow-ups.
- [x] Make morning coaching unconditional and Hint/Why Terra-driven with
      progressive replay-safe hint levels.

**Exit criteria:** automated acceptance scenarios pass without model-generated
markup or fabricated state. The 2026-08-16 flexibility repair is automated-test
verified only; the updated live-model proof remains pending.

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

- **Coach by outcome, not choreography.** Keep the prompt focused on role,
  personality, and the learning loop. Enforce only mechanical integrity in code.
- **Each phase ends with a working thing on the homelab.** No phase is
  "scaffold only." If a phase can't be deployed, it's too big.
- **Tests come with the code, not after.** The coach pass prompt is the
  highest-risk piece (LLM output is non-deterministic); it gets a
  golden-output test suite.
