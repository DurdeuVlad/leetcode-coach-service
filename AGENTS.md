# AGENTS.md

Entry point for AI agents (Claude Code, Codex, Cursor, etc.) working on this
repo. Read this first, then the docs it points at.

## Current status

**Phase 0 complete (FastAPI + Postgres + Alembic + Docker bootstrap).
Phases 1–7 in progress.** The app boots, `/health` works (including the
degraded state when the DB is down), migrations run, and Docker compose
brings up app + Postgres together. The next action is **Phase 1** of
[`docs/roadmap.md`](./docs/roadmap.md): the integration clients (issues
#009–#014 in [`plan/issues/`](./plan/issues/)).

If you are picking up this repo cold, your job is almost certainly to
continue the roadmap from where the checkboxes leave off. Do not redesign
the system — the spec, architecture, and phasing are already decided.
Implement what the docs say.

## Reading order (do not skip)

1. [`README.md`](./README.md) — 5-minute orientation: what this is, why a
   port, status, stack, doc index.
2. [`docs/business-requirements.md`](./docs/business-requirements.md) — the
   behavioral contract. What the system must do. **This is the source of
   truth for behavior.** If code and this doc disagree, the doc wins.
3. [`docs/architecture.md`](./docs/architecture.md) — the design. How the
   system does it. Stack, repo layout, LLM client design, Google auth
   branch, schema, env vars, deploy.
4. [`docs/roadmap.md`](./docs/roadmap.md) — the 8-phase plan. Each phase
   has explicit exit criteria. Do not skip phases; each one ships something
   the next phase depends on.
5. [`n8n-reference/README.md`](./n8n-reference/README.md) — the original n8n
   v3 spec. **Read only when porting prompts or flow logic.** The prompts
   in the n8n AI Agent nodes are ported **verbatim** into
   `src/leetcode_coach/prompts/` — no rewriting, no "improving."

## Operating rules

### Source of truth

- **Behavior:** `docs/business-requirements.md`.
- **Design:** `docs/architecture.md`.
- **Plan:** `docs/roadmap.md`.
- **Prompts and flow logic (verbatim source):** the AI Agent node `text`
  fields in `n8n-reference/workflows/flow-a-schedule-and-expiry.json` and
  `n8n-reference/workflows/flow-b-telegram-and-coach.json`.
- **Code:** `src/leetcode_coach/` (does not exist yet — Phase 0 creates it).

### Do not

- **Do not edit `n8n-reference/`.** It is frozen reference material. If the
  spec feels wrong, the fix is to update `docs/business-requirements.md`
  with an explicit decision, not to silently rewrite the reference.
- **Do not rewrite the prompts.** Port them verbatim. If a prompt has a
  bug, fix it in `docs/business-requirements.md` first, then in
  `src/leetcode_coach/prompts/`, with a commit message that names the bug.
  The n8n audit already identified the prompt-adjacent bugs (missing
  unsolved pool, missing Google Task notes append); those are called out
  in `docs/roadmap.md` Phase 2 and Phase 3b.
- **Do not add Celery, Redis, a task queue, a separate worker process, an
  LLM tool-calling loop, multi-user support, a web UI, or Browserless/
  SearXNG integrations in v1.** `docs/architecture.md` §12 and
  `docs/business-requirements.md` §7 explain why each is out of scope.
  Adding them is scope creep, not initiative.
- **Do not commit secrets.** All secrets are env vars (see
  `docs/architecture.md` §8). `.env.example` has keys only, never values.
- **Do not "log with estimated defaults"** when an external call fails.
  This anti-pattern from the old Discord workflow is explicitly forbidden
  (NFR-1 layer 2). Fail loudly and alert.

### Do

- **Follow the phase order.** Each phase's exit criteria are non-negotiable.
  If you can't satisfy them, you're not done with the phase.
- **Write tests with the code, not after.** The coach pass is the highest-
  risk piece (non-deterministic LLM output); it gets a golden-output test
  in Phase 3b. The two n8n business-logic bugs get regression tests in
  Phase 2 (unsolved pool) and Phase 3b (Google Task notes append).
- **Use the stack from `docs/architecture.md` §2.** Python 3.12, FastAPI,
  APScheduler, SQLModel, Alembic, httpx, tenacity, pydantic-settings,
  pytest + pytest-asyncio + respx + testcontainers-postgres, uv, Docker.
  Do not substitute (e.g., "I'll use Celery because I know it better") —
  the choices are documented and deliberate.
- **Port prompts verbatim.** See above.
- **Close the three n8n error-handling gaps for free** by relying on
  `tenacity` (retries default-on), typed exceptions (the Google auth
  branch), and FastAPI's normal error handling (the Telegram webhook
  route). These are not separate work items.
- **Update `docs/roadmap.md` checkboxes** as you complete phase items.
  Mark the phase's todo `[x]` only when its exit criteria are met.

## Key gotchas a new agent must know

1. **The unsolved-pool bug.** The n8n Flow A AI Agent was never given the
   unsolved problem pool to choose from — it only saw `solved = true`
   rows. The Python port must read `leetcode_problems WHERE solved = false`
   and pass that into the propose prompt. Regression test in Phase 2.
2. **The Google Task notes-append bug.** The n8n Flow B `mark complete`
   node dropped the coach feedback from the task notes. The Python port
   must call `google_tasks.mark_complete(task_id, notes_append=feedback)`
   — append, not replace. Regression test in Phase 3b.
3. **The Google auth 7-day expiry.** Root cause is the GCP OAuth consent
   screen being in `Testing` status. The fix is to flip it to
   `In production` (single-user personal use, no verification needed).
   Documented in `n8n-reference/README.md` lines 325-353 and
   `docs/architecture.md` §6. Phase 6 includes this flip.
4. **Lesson graduation is double-gated.** Coach says
   `lesson_should_graduate = true` **AND** the DB row's
   `times_reinforced >= 5`. Read the count from the DB, not from the
   coach. The coach hallucinating a count is a known failure mode.
5. **The 5-list candidate array must be persisted somewhere Flow B can
   read it.** The n8n version used a Data Table; the Python port needs
   either a `daily_candidates` table or `pending_review` rows pre-inserted
   with `status = proposed`. Phase 3a of the roadmap flags this as a
   decision to make in that phase. Don't punt it.
6. **Timezone is Europe/Bucharest.** All cron jobs use it. All dates in
   the DB are `DATE` (not `TIMESTAMPTZ`) because the system is
   single-timezone. See `docs/architecture.md` §7.

## Open decisions (do not resolve without data)

`docs/business-requirements.md` §8 lists 5 open decisions. They are
**intentionally unresolved** — they need real runtime data to settle. Do
not pick a value just to "close the loop." Phase 7 of the roadmap is where
calibration happens.

## When you finish a phase

1. Verify every checkbox in that phase's section of `docs/roadmap.md` is
   `[x]`.
2. Verify the phase's **Exit criteria** block is satisfied.
3. Run the test suite. It must be green.
4. Commit with a message that names the phase: e.g.,
   `phase 0: bootstrap (fastapi + postgres + alembic + docker)`.
5. Tell the user which phase is done and what the next phase is.

## When you're blocked

- If a doc feels wrong, say so explicitly. Don't silently work around it.
- If an exit criterion can't be met, stop and report. Don't declare the
  phase done.
- If you're tempted to add scope (a feature, a tool, a service), check
  `docs/architecture.md` §12 and `docs/business-requirements.md` §7 first.
  If it's listed there as out of scope, the answer is no.
