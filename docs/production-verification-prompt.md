# Production verification prompt

Copy this prompt into a Codex session running on the production server. Replace
`<EXPECTED_PRODUCTION_SHA>` with the full 40-character commit SHA that should be
live. Do not run it without that independent release identifier.

---

You are auditing the live LeetCode Coach production server.

Expected production SHA: `<EXPECTED_PRODUCTION_SHA>`

First validate that the expected SHA is exactly 40 lowercase hexadecimal
characters. If it is missing or malformed, stop and ask for it. Do not infer the
expected release from whatever happens to be deployed.

Your goal is to establish, with timestamped evidence, whether the deployed
Coolify application, running container, database schema, Telegram webhook,
scheduler singleton, and GitHub CI/CD path are healthy and all correspond to the
expected SHA. Begin read-only. Repair only a proven CI/CD defect under the narrow
repair policy below. Do not deploy or modify the application or production data.

## Sources of truth

Locate the checkout for
`https://github.com/DurdeuVlad/leetcode-coach-service.git` and read, in order:

1. `AGENTS.md`
2. `README.md`
3. `docs/agentic-v2.md`
4. `docs/roadmap.md`
5. `docs/live-proof.md`
6. `Dockerfile` and `entrypoint.sh`
7. `src/leetcode_coach/main.py` and `src/leetcode_coach/scheduler.py`
8. `.github/workflows/ci.yml` and `.github/workflows/deploy.yml`

Treat `docs/agentic-v2.md`, `docs/roadmap.md`, and current code as
authoritative. The older two-service material in `docs/architecture.md` is V1
history. Production should have one app container; APScheduler runs inside the
FastAPI lifespan and is protected by a PostgreSQL advisory lock.

## Non-negotiable safety rules

- Never print, copy, diff, store, or summarize secret values. This includes
  environment variables, database URLs, tokens, webhook secrets, GitHub or
  Coolify credentials, runner registration data, and complete container `Env`
  arrays. Report only presence booleans or explicitly safe, redacted metadata.
- Never dump a complete Coolify application model, Docker inspection object,
  process environment, workflow secret context, or raw log stream. Query only
  the specific non-secret fields required for evidence.
- Perform no database writes. Do not run Alembic upgrade/downgrade, migrations,
  imports, backup restores, schema changes, DML, data corrections, or advisory
  lock acquisition/release. Limit database access to `SELECT 1`, schema metadata,
  Alembic version, and lock metadata.
- Perform no Telegram writes. Do not call `setWebhook`, `deleteWebhook`,
  `sendMessage`, edit/reaction methods, or POST synthetic/replayed updates to the
  app webhook. Only read-only `getMe` and `getWebhookInfo` checks are allowed.
- Do not restart, redeploy, scale, stop, or recreate Coolify, Docker, the app,
  PostgreSQL, or the scheduler. Do not dispatch or rerun the deploy workflow.
  A stale SHA or unhealthy runtime is a finding, not permission to deploy.
- Do not run the live proof harness against production. It intentionally creates
  database and Telegram-side effects.
- Do not reveal user messages, code submissions, coaching content, or learning
  records. Project logs to timestamp, level, event name, and safe status fields;
  report aggregates and sanitized evidence only.
- If the application, container, database, or repository target is ambiguous,
  stop. Do not guess from a partial name or choose the newest-looking resource.
- Keep a command/evidence journal. Before every command, classify it as read-only
  or an allowed CI/CD repair and state why it is safe.

## Verification procedure

### 1. Resolve the exact production target

Record UTC time, hostname, checkout path, repository remote, and current branch
without changing refs. Resolve Coolify `Application::find(1)`, its resource UUID,
the associated running container ID/name/image, replica count, creation time, and
start time. When using Coolify tinker or Docker inspection, select individual
non-secret fields; never print application configuration or container environment.

Require evidence that the chosen container belongs to this Coolify application.
If multiple plausible app or scheduler containers exist, stop and report the
ambiguity.

### 2. Prove the complete SHA chain

Collect all of the following independently:

- the supplied expected production SHA;
- the current GitHub `master` SHA, using `git ls-remote origin refs/heads/master`
  or the GitHub API without changing the checkout;
- the SHA and conclusion of the corresponding GitHub Actions deploy run;
- the commit, status, deployment UUID, and completion time of the applicable
  Coolify deployment-queue row;
- the SHA or deployment label tied to the currently running container.

Report full 40-character SHAs. Do not treat a green Actions trigger, the latest
finished queue row, an image creation time, or the checkout HEAD alone as proof of
the running release. The expected SHA, successful gated workflow run, completed
Coolify deployment, and current container must agree. If the container does not
carry enough metadata to prove its SHA, report the release as `unverified`; do not
substitute an inference.

### 3. Check container and application health

Inspect only the running state, Docker health status, restart count, OOM flag,
exit code, and safe image/container identifiers. Verify there is exactly one
intended app replica.

Check both:

- the container-local `http://127.0.0.1:8000/health`; and
- the known Coolify public HTTPS `/health` endpoint discovered from safe routing
  metadata.

The required body is `{"status":"ok","database":"up"}`. Do not accept HTTP 200
or Docker `healthy` alone: the FastAPI endpoint intentionally returns a degraded
JSON body with HTTP 200 when the database is down, and the Docker curl healthcheck
does not inspect that JSON.

Do not invent a production URL or add one to CI. If the public route cannot be
discovered safely, mark only that check unverified.

### 4. Check migrations and database connectivity

Inside the running app container, run read-only `alembic current` and
`alembic heads`. Both must resolve to `v2_0001`. Confirm database reachability with
`SELECT 1` and verify the Alembic version through schema metadata. Do not print
`DATABASE_URL`, database credentials, application rows, or user data.

`entrypoint.sh` normally runs `alembic upgrade head` before Uvicorn. This audit
must not run it. Any mismatch or missing schema is a critical finding and a stop
condition.

### 5. Check Telegram without writing

From inside the app container, use the configured token internally for read-only
`getMe` and `getWebhookInfo` calls. Never echo the token or place it in a displayed
command line. Report only:

- whether bot identity lookup succeeded;
- whether the registered webhook exactly equals the safely discovered public base
  URL plus `/telegram/webhook`;
- whether it is HTTPS;
- pending update count and allowed-update metadata; and
- sanitized last-error timestamp/type, if present.

Do not register, delete, or test-deliver the webhook. Do not send a message.

### 6. Inspect bounded, sanitized logs

Inspect a bounded window covering the active deployment start and recent runtime.
Project only safe timestamp, level, event, and status fields. Look for:

- Alembic startup completion and Uvicorn start;
- `scheduler_started` or `scheduler_start_failed`;
- `webhook_registered`, `webhook_skipped`, or registration failures;
- update/provider failures, restart loops, OOM events, and health failures.

Return event counts and a few sanitized timestamps, not raw exception bodies,
payloads, user text, URLs containing credentials, or full logs. If safe projection
is impossible, stop rather than expose logs.

### 7. Prove the scheduler singleton

The current scheduler facts are:

- in-process in the single app container;
- timezone `Europe/Bucharest`;
- advisory lock bigint `8204202602`;
- lock metadata split `classid=1`, `objid=3909235306`;
- schema gate `v2_0001`;
- jobs at 00:00 daily tax, 09:05 queue refill, 20:00 nudge, 22:00 expiry,
  and Monday 03:00 problem refresh.

Verify one current app leader, one `scheduler_started` event for the active
container generation, no competing dedicated scheduler container/process, and
exactly one granted PostgreSQL advisory lock matching that identifier. Query
`pg_locks` metadata only. Never call `pg_try_advisory_lock`, unlock, terminate a
backend, or kill a competing process. Multiple leaders are a critical finding.

### 8. Verify CI/CD and the self-hosted runner

Use `gh run list/view` or the GitHub API to inspect the CI and deploy run for the
expected SHA. Sanitize failure logs. Verify the checked-in contract:

- `ci.yml` keeps direct `dev` push and `dev` pull-request triggers and exposes
  `workflow_call`;
- its existing Ubuntu/PostgreSQL 16, checkout, uv, Ruff, format, Alembic, and
  pytest steps remain intact;
- `ci.yml` has no independent `master` push trigger;
- `deploy.yml` keeps `master` push and manual triggers;
- its `ci` job calls `./.github/workflows/ci.yml`;
- its Coolify deploy job declares `needs: ci` before entering the production
  environment or executing the Coolify command;
- the Coolify script still queues `Application::find(1)` with `${{ github.sha }}`
  and polls its deployment UUID for up to ten minutes, succeeding only on
  `finished` and failing on `failed`, `cancelled-by-user`, or timeout.

Inspect the self-hosted runner service using safe status fields. Verify it is
registered/online, corresponds to this production workflow, and can access the
Coolify Docker container without exposing runner tokens or configuration secrets.
Determine whether it is idle before considering any repair. Do not infer health
from a service process alone; correlate with GitHub runner/job evidence.

## Narrow CI/CD repair policy

Repair is allowed only when the audit proves a causal defect in GitHub workflow
plumbing or the self-hosted runner. Runtime, application, database, Docker,
Coolify, Telegram, and product-code repairs are out of scope.

Before changing anything, record:

1. the exact defect and causal evidence;
2. why it is CI/CD rather than a production/app failure;
3. the smallest repair and its blast radius;
4. validation and rollback commands; and
5. confirmation that no deployment or active runner job will be disturbed.

Allowed actions:

- restart only the self-hosted runner service when it is proven unhealthy and
  proven idle; do not reinstall, upgrade, re-register, or reveal its credentials;
- make a minimal workflow-only repair on a new
  `feat/repair-production-cicd` branch;
- validate YAML, reusable-workflow semantics, shell syntax, workflow contract
  tests, and any available `actionlint` checks;
- commit and push only that repair branch and open a reviewable PR when available.

Forbidden actions:

- direct push or merge to `master`, force push, history rewrite, workflow dispatch,
  deploy rerun, or manual Coolify deployment;
- changing application/domain code, production configuration, secrets, database,
  Telegram state, Docker/Coolify versions, host packages, or runner credentials;
- broad refactors or speculative hardening without a reproduced failure.

If repair needs broader authority, stop and request it. A stale or unhealthy
production release does not itself authorize a CI/CD edit.

## Stop conditions

Stop mutations and report immediately for any of the following:

- ambiguous application, container, database, repository, or runner target;
- inability to prove the running SHA;
- a command would expose secrets or user data;
- active runner job or deployment;
- migration mismatch or database degradation;
- Telegram webhook mismatch, backlog, or recent delivery errors;
- multiple scheduler leaders or locks; or
- required repair outside the narrow CI/CD policy.

## Required report

Return:

1. Executive verdict: `healthy`, `degraded`, `broken`, or `unverified`.
2. An evidence matrix with component, expected value, observed value, UTC
   timestamp/source, pass/fail, and confidence.
3. The exact SHA chain: supplied expected SHA -> GitHub master/run -> Coolify
   deployment -> running container.
4. Sanitized findings ordered by severity and bounded event counts.
5. CI/CD repair evidence, files/service touched, branch/commit/PR, validation, and
   rollback, or the explicit statement `No changes made`.
6. Remaining unknowns and the next safe action requiring operator authorization.
7. Explicit attestations that you output no secrets, performed no database writes
   or migrations, made no Telegram writes, sent no duplicate updates, and did not
   deploy/restart production. Mention an allowed idle runner restart separately if
   one occurred.

Do not claim success from absence of obvious errors. Every passing verdict must be
supported by current production evidence.

---

