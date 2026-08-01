# LeetCode Coach Service

A Python service that coaches you through one LeetCode problem per day.

Every morning at 09:05 (configurable timezone) it proposes 5 problems; you
reply with which two to attempt; you send code for each; an LLM coach
reviews the code, decides whether to save a new lesson, and closes the
loop. The scheduler service handles timed work independently from the webhook app.

This is a **port** of an existing n8n implementation. The original n8n
workflows and their documentation live in [`n8n-reference/`](./n8n-reference)
and are treated as the authoritative behavioral spec. The port exists
because n8n's error handling, retry semantics, and Google auth branch
support were insufficient for the workflow's own documented requirements.

## Status

**Phase 0 complete (FastAPI + Postgres + Alembic + Docker bootstrap).
Phases 1–7 in progress.** See [`docs/roadmap.md`](./docs/roadmap.md) for
the 8-phase plan and current checkboxes.

**If you are an AI agent picking up this repo cold, start with
[`AGENTS.md`](./AGENTS.md).** It tells you the reading order, the
operating rules, and the gotchas you'll hit.

## Stack

- **Runtime:** Python 3.12, FastAPI, APScheduler
- **DB:** PostgreSQL via SQLModel + Alembic
- **LLM:** OpenAI SDK (primary, `gpt-5.6-sol`) + Google GenAI SDK (fallback,
  `gemini-3.6-flash`) — explicit fallback logic, no n8n Fallback Model bugs
- **Integrations:** Telegram Bot API, LeetCode GraphQL (via Browserless),
  YouTube search (via SearXNG)
- **Deploy:** Docker → Coolify on a homelab
- **Tests:** pytest, pytest-asyncio, testcontainers-postgres, respx

See [`docs/architecture.md`](./docs/architecture.md) §2 for the full
dependency list and rationale.

## Quickstart

```bash
# 1. Install uv (https://docs.astral.sh/uv/) and Python 3.12+
uv sync --extra dev

# 2. Configure env vars (Telegram bot token, OpenAI key, Postgres URL, etc.)
cp .env.example .env
#   edit .env — never commit real values

# 3. Start Postgres + run migrations + start the app
docker compose up -d postgres
uv run alembic upgrade head
uv run uvicorn leetcode_coach.main:app --reload

# 4. Health check
curl localhost:8000/health   # -> {"status":"ok", ...}
```

Or just `docker compose up` to run app + scheduler + Postgres together. The
app entrypoint runs `alembic upgrade head`; the scheduler intentionally does not.

## Documentation index

| Doc | Audience | Purpose |
|---|---|---|
| [`AGENTS.md`](./AGENTS.md) | AI agents + contributors | Entry point. Reading order, operating rules, gotchas. **Read first.** |
| [`docs/business-requirements.md`](./docs/business-requirements.md) | everyone | The behavioral contract. n8n-agnostic. What the system must do. **Source of truth for behavior.** |
| [`docs/architecture.md`](./docs/architecture.md) | implementers | The design. FastAPI + Postgres + Coolify. How the system does it. |
| [`docs/roadmap.md`](./docs/roadmap.md) | implementers | The phased port plan. 8 phases from bootstrap to hardening. |
| [`n8n-reference/`](./n8n-reference) | porters | The original n8n workflows + per-node documentation. Frozen. Source for verbatim prompt porting. See [`n8n-reference/NOTICE.md`](./n8n-reference/NOTICE.md). |
| [`CONTRIBUTING.md`](./CONTRIBUTING.md) | contributors | Dev setup, lint/test commands, PR process, scope rules. |
| [`SECURITY.md`](./SECURITY.md) | everyone | How to report a vulnerability. **Do not file public issues for secrets.** |
| [`CODE_OF_CONDUCT.md`](./CODE_OF_CONDUCT.md) | everyone | Contributor Covenant 2.1. |

## Why a port

The n8n audit (see [`docs/roadmap.md`](./docs/roadmap.md) "What we
explicitly defer") found three classes of problems:

1. **Business-logic bugs.** Flow A's AI Agent was never given the unsolved
   problem pool to choose from.
2. **Error-handling gaps vs. the spec.** The original README required
   `retryOnFail` on all AI/Data Table nodes (18 nodes lacked it) and
   `onError: "Stop Workflow"` on the Telegram Trigger (not set).
3. **Operational limits.** n8n Cloud's 250-workflow-run/month cap and a
   preference for homelab hosting made a self-hosted Python service the
   lower-friction long-term option.

In Python: retries are default-on via `tenacity`, and the Telegram webhook
is a normal FastAPI route with normal error handling. The error-handling
gaps from the n8n audit close for free.

## Scope

**In scope (v1):** single user, 3 flows (proposal, reply router + coach
pass, expiry sweep), weekly LeetCode pool refresh, Telegram
+ LLM integrations, homelab deploy via Coolify.

**Out of scope (v1):** multi-user, web UI, photo evidence, LLM tool-calling
loop in the coach pass, Anki export, automated mock interviews. See
[`docs/business-requirements.md`](./docs/business-requirements.md) §7 and
[`docs/roadmap.md`](./docs/roadmap.md) "What we explicitly defer" for the
full list and rationale.

## Contributing

PRs welcome — please read [`CONTRIBUTING.md`](./CONTRIBUTING.md) first.
The short version:

- Lint (`uv run ruff check`) and tests (`uv run pytest`) must pass; CI
  enforces both.
- One concern per PR. Don't bundle a refactor with a feature.
- Don't add anything from the out-of-scope list
  ([`docs/architecture.md`](./docs/architecture.md) §12).
- Don't rewrite the AI Agent prompts — they're ported verbatim from n8n.
  If a prompt has a bug, fix it in `docs/business-requirements.md` first.

## License

[MIT](./LICENSE) — © 2026 DurdeuVlad.
