# AGENTS.md

Entry point for AI agents (Claude Code, Codex, Cursor, etc.) working on this
repo. Read this first, then the docs it points at.

## Current status

**Implemented and locally verified; production deployment pending.**
The app boots, `/health` works (including the degraded state when the DB
is down), migrations run, and Docker compose brings up app + Postgres
together. The scheduler runs in-process via the FastAPI lifespan. The
next action is the **Cutover** section of
[`docs/roadmap.md`](./docs/roadmap.md): deploy to Coolify and verify
production.

If you are picking up this repo cold, your job is almost certainly to
continue the roadmap from where the checkboxes leave off. Do not redesign
the system — the spec, architecture, and phasing are already decided.
Implement what the docs say.

## Reading order (do not skip)

1. [`README.md`](./README.md) — 5-minute orientation: what this is,
   status, stack, doc index.
2. [`docs/business-requirements.md`](./docs/business-requirements.md) — the
   behavioral contract. What the system must do. **This is the source of
   truth for behavior.** If code and this doc disagree, the doc wins.
3. [`docs/architecture.md`](./docs/architecture.md) — the design. How the
   system does it. Stack, repo layout, LLM client design, schema, env vars,
   deploy.
4. [`docs/roadmap.md`](./docs/roadmap.md) — the delivery plan. Each
   section has explicit exit criteria.
5. [`docs/live-proof.md`](./docs/live-proof.md) — the live acceptance
   evidence and how to reproduce it.

## Operating rules

### Source of truth

- **Behavior:** `docs/business-requirements.md`.
- **Design:** `docs/architecture.md`.
- **Plan:** `docs/roadmap.md`.
- **Code:** `src/leetcode_coach/`.

### Do not

- **Do not add Celery, Redis, a task queue, a separate worker process,
  multi-user support, a web UI, or Browserless/SearXNG integrations.**
  `docs/architecture.md` §12 and `docs/business-requirements.md` §7
  explain why each is out of scope. Adding them is scope creep, not
  initiative.
- **Do not commit secrets.** All secrets are env vars (see
  `docs/architecture.md` §8). `.env.example` has keys only, never values.
- **Do not "log with estimated defaults"** when an external call fails.
  This anti-pattern from the old Discord workflow is explicitly forbidden
  (NFR-1 layer 2). Fail loudly and alert.

### Do

- **Follow the roadmap order.** Each section's exit criteria are
  non-negotiable. If you can't satisfy them, you're not done.
- **Write tests with the code, not after.** The coach pass is the highest-
  risk piece (non-deterministic LLM output); it gets a golden-output test.
- **Use the stack from `docs/architecture.md` §2.** Python 3.12, FastAPI,
  APScheduler, SQLModel, Alembic, httpx, tenacity, pydantic-settings,
  pytest + pytest-asyncio + respx + testcontainers-postgres, uv, Docker.
  Do not substitute (e.g., "I'll use Celery because I know it better") —
  the choices are documented and deliberate.
- **Update `docs/roadmap.md` checkboxes** as you complete items. Mark a
  todo `[x]` only when its exit criteria are met.

## Key gotchas a new agent must know

1. **The unsolved-pool bug.** The original n8n Flow A AI Agent was never
   given the unsolved problem pool to choose from — it only saw
   `solved = true` rows. The Python port reads
   `leetcode_problems WHERE solved = false` and passes that into the
   propose prompt. Regression test covers this.
2. **Lesson graduation is double-gated.** Coach says
   `lesson_should_graduate = true` **AND** the DB row's
   `times_reinforced >= 5`. Read the count from the DB, not from the
   coach. The coach hallucinating a count is a known failure mode.
3. **The 5-list candidate array must be persisted somewhere the webhook
   can read it.** The system uses `pending_review` rows pre-inserted with
   `status = proposed`.
4. **Timezone is Europe/Bucharest.** All cron jobs use it. All dates in
   the DB are `DATE` (not `TIMESTAMPTZ`) because the system is
   single-timezone. See `docs/architecture.md` §7.

## Open decisions (do not resolve without data)

`docs/business-requirements.md` §8 lists open decisions. They are
**intentionally unresolved** — they need real runtime data to settle. Do
not pick a value just to "close the loop."

## When you finish a roadmap section

1. Verify every checkbox in that section of `docs/roadmap.md` is `[x]`.
2. Verify the section's **Exit criteria** block is satisfied.
3. Run the test suite. It must be green.
4. Commit with a message that names the section.
5. Tell the user which section is done and what the next one is.

## When you're blocked

- If a doc feels wrong, say so explicitly. Don't silently work around it.
- If an exit criterion can't be met, stop and report. Don't declare the
  section done.
- If you're tempted to add scope (a feature, a tool, a service), check
  `docs/architecture.md` §12 and `docs/business-requirements.md` §7 first.
  If it's listed there as out of scope, the answer is no.
