# LeetCode Coach Service

A Python service that coaches you through one LeetCode problem per day.

Every morning at 09:05 (configurable timezone) it proposes 5 problems; you
reply with which two to attempt; you send code for each; an LLM coach
reviews the code, decides whether to save a new lesson, and closes the
loop. The scheduler service handles timed work independently from the webhook app.

This is a rebuild of an earlier n8n/Python port. The active product contract is
the **Agentic V2** specification in [`docs/agentic-v2.md`](./docs/agentic-v2.md).
The n8n material and marked V1 sections in the older documents are historical
reference only; they are not the source of truth for new work.

## Status

**V2 is implemented and verified locally, but not deployed.** Production
cutover and the seven-day rollback window still require operator action;
staging is explicitly outside the current delivery scope, and the existing V1 deployment remains authoritative
until those steps are completed.
See [`docs/roadmap.md`](./docs/roadmap.md) for the active V2 delivery sequence
and current checkboxes.

**If you are an AI agent picking up this repo cold, start with
[`AGENTS.md`](./AGENTS.md).** It tells you the reading order, the
operating rules, and the gotchas you'll hit.

## Stack

- **Runtime:** Python 3.12, FastAPI, APScheduler
- **DB:** PostgreSQL via SQLModel + Alembic
- **LLM:** OpenAI Agents SDK: `gpt-5.6-terra` controller with an optional,
  one-shot read-only `gpt-5.6-sol` advisor; no provider fallback
- **Integrations:** Telegram Bot API, PostgreSQL canonical problem data, and
  bounded walkthrough lookup
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

# 3. Start the isolated V2 stack
docker compose -f docker-compose.v2.yml up -d postgres_v2
uv run alembic -c alembic-v2.ini upgrade head
uv run uvicorn leetcode_coach_v2.main:app --reload

# 4. Health check
curl localhost:8000/health   # -> {"status":"ok", ...}
```

Or run `docker compose -f docker-compose.v2.yml up` for app, scheduler, and the
fresh V2 Postgres database. Exercise the real Terra loop locally with
`uv run coach-v2-terminal`; use
`/callback <callback_data>` to press a printed button and
`/reply <bot_message_id> yes|no` to test contextual approvals. This simulator
uses the configured OpenAI key and V2 database but does not call Telegram.

For the repeatable real-model acceptance run, migrate a fresh disposable SQLite
or guarded localhost PostgreSQL proof database, set `V2_PROOF_DATABASE_URL`, and
run `uv run coach-v2-prove`. See
[`docs/live-proof.md`](./docs/live-proof.md) for covered flows, assertions, and
the latest evidence.

## Documentation index

| Doc | Audience | Purpose |
|---|---|---|
| [`AGENTS.md`](./AGENTS.md) | AI agents + contributors | Entry point. Reading order, operating rules, gotchas. **Read first.** |
| [`docs/agentic-v2.md`](./docs/agentic-v2.md) | everyone | **Agentic V2 source of truth:** behavior, architecture, safety boundaries, migration, and acceptance. |
| [`docs/business-requirements.md`](./docs/business-requirements.md) | everyone | V1 behavioral record; superseded for new work. |
| [`docs/architecture.md`](./docs/architecture.md) | implementers | V1 design record; superseded for new work. |
| [`docs/roadmap.md`](./docs/roadmap.md) | implementers | Active V2 delivery plan plus V1 history. |
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
