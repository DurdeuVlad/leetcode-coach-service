# #033 — CI/CD pipeline to Coolify homeserver (Tailscale-gated)

**Milestone:** M6 deploy · **Labels:** `type:infra` `area:ops` `risk:high` `prio:P1`
**Depends on:** #001, #007, #014, #030

## Summary
Automated CI/CD: run lint + tests on every push/PR (CI), then on merge to the
default branch trigger a Coolify deploy on the homeserver. The homeserver's
Coolify control plane is **not publicly reachable** — it lives on a Tailscale
tailnet — so the deploy step must join the tailnet before it can reach Coolify.

## Context
- Deploy target and topology are defined in #030 / `docs/architecture.md` §9
  (single container on Coolify, Postgres managed by Coolify, `alembic upgrade
  head` on startup, `/health` gate).
- The Coolify instance is only reachable over Tailscale. A stock GitHub-hosted
  runner cannot reach it without first joining the tailnet.
- Security rules (NFR-4 / repo policy): no secrets in the repo; do not weaken
  security controls to make CI pass. All credentials via GitHub Actions
  secrets/environments.

## Decisions to lock in this issue
- [ ] **Tailnet access method** — pick one and document it:
  1. **Ephemeral tailnet node in the workflow** via the official
     `tailscale/github-action` (Tailscale OAuth client or ephemeral auth key,
     tagged e.g. `tag:ci`), then call Coolify over its tailnet address. Keeps
     using GitHub-hosted runners.
  2. **Self-hosted runner already on the tailnet** (or on the homeserver),
     which can reach Coolify directly with no per-run tailnet join.
  - Default recommendation: option 1 (ephemeral node) — no always-on runner to
    maintain; least standing attack surface.
- [ ] **Coolify deploy trigger** — pick one:
  1. Coolify per-resource **deploy webhook** (`POST` with a secret token) — the
     resource is configured to build from the Git repo. Simplest.
  2. Coolify **API token** + API call to trigger a deployment.
  - Default recommendation: the deploy webhook.

## Tasks
- [ ] `.github/workflows/ci.yml` — on `push` + `pull_request`:
      `uv sync`, `ruff check`, `ruff format --check`, `pytest` (uses the
      testcontainers Postgres from #007/#014). Must be green before deploy.
- [ ] `.github/workflows/deploy.yml` — on push to the default branch (after CI
      passes, e.g. `workflow_run`/needs gating):
  1. Join the tailnet via the chosen method (ephemeral node, `tag:ci`).
  2. Trigger the Coolify deploy (webhook or API) over the tailnet address.
  3. Poll `/health` on the tailnet address until 200 (bounded timeout) to
     confirm the new revision is live; fail the job if it doesn't come up.
- [ ] Use a **GitHub Environment** (e.g. `production`) holding the secrets and
      (optionally) a manual approval gate before deploy.
- [ ] Document required secrets in `README.md` / `.env.example` notes (names
      only): `TS_OAUTH_CLIENT_ID`/`TS_OAUTH_SECRET` (or `TS_AUTHKEY`),
      `COOLIFY_DEPLOY_WEBHOOK` (or `COOLIFY_API_URL` + `COOLIFY_API_TOKEN`).
- [ ] Restrict the ACLs so the CI tag (`tag:ci`) can reach **only** the Coolify
      control-plane host/port on the tailnet, nothing else.

## Acceptance criteria
- [ ] CI runs lint + full test suite on every push/PR and blocks merge on
      failure.
- [ ] A merge to the default branch automatically joins the tailnet, triggers a
      Coolify deploy, and the job only succeeds once `/health` returns 200 on
      the tailnet address.
- [ ] No secret values are committed; all live in GitHub Actions
      secrets/environment.
- [ ] The ephemeral tailnet node is removed/expired after the run (if option 1).
- [ ] A failed deploy or a `/health` that never turns 200 fails the pipeline
      loudly (no silent success).

## Notes
- This is deploy **automation** layered on top of the manual #030 bring-up;
  do #030 first so the Coolify resource, env vars, and webhook already exist.
- Keep the tailnet ACL for `tag:ci` least-privilege — control-plane host only.
- Out of scope: multi-environment (staging) pipelines, blue/green — single
  homeserver, single environment in v1.

## Principles (how they apply here)
See [#034](034-engineering-principles-layers.md). KISS is the tie-breaker.
- **KISS:** one CI workflow + one deploy workflow, single environment. No
  matrix builds, no staging tier, no blue/green — architecture §12 forbids
  them and they'd violate #1 here. The default recommendations (ephemeral
  tailnet node + Coolify deploy webhook) are the simplest viable options.
- **Fail loud:** the deploy job only succeeds when `/health` returns 200
  on the tailnet address within a bounded timeout — a deploy that "looks
  done" but isn't healthy is a hard failure, never silent success.
- **Security / explicit over implicit:** no secret values in the repo; all
  via GitHub Actions secrets/environments (NFR-4). The `tag:ci` ACL is
  least-privilege — Coolify control-plane host/port only, nothing else on
  the tailnet.
- **YAGNI:** do not add a self-hosted runner if the ephemeral-node option
  works — no always-on runner to maintain (less standing attack surface).
  Pick the second option only if option 1 is shown insufficient.
- **Do not weaken security controls to make CI pass** — if a gate fails,
  escalate to the user, don't bypass it (repo policy).
