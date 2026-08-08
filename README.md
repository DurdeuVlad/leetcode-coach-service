# LeetCode Coach Service

A Python service that coaches you through one LeetCode problem per day.

Every morning at 09:05 (configurable timezone) it proposes 5 problems; you
reply with which two to attempt; you send code for each; an LLM coach
reviews the code, decides whether to save a new lesson, and closes the
loop. The scheduler runs in-process within the webhook app (single
container).

## Status

**Implemented and verified locally; production deployment pending.**
See [`docs/roadmap.md`](./docs/roadmap.md) for the delivery sequence
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

# 3. Start Postgres and run migrations
docker compose up -d postgres
uv run alembic upgrade head
uv run uvicorn leetcode_coach.main:app --reload

# 4. Health check
curl localhost:8000/health   # -> {"status":"ok", ...}
```

Or run `docker compose up` for the app and Postgres together. Exercise the
real Terra loop locally with `uv run coach-terminal`; use
`/callback <callback_data>` to press a printed button and
`/reply <bot_message_id> yes|no` to test contextual approvals. This
simulator uses the configured OpenAI key and database but does not call
Telegram.

For the repeatable real-model acceptance run, migrate a fresh disposable
SQLite or guarded localhost PostgreSQL proof database, set
`PROOF_DATABASE_URL`, and run `uv run coach-prove`. See
[`docs/live-proof.md`](./docs/live-proof.md) for covered flows,
assertions, and the latest evidence.

## Documentation index

| Doc | Audience | Purpose |
|---|---|---|
| [`AGENTS.md`](./AGENTS.md) | AI agents + contributors | Entry point. Reading order, operating rules, gotchas. **Read first.** |
| [`docs/business-requirements.md`](./docs/business-requirements.md) | everyone | Behavioral contract. |
| [`docs/architecture.md`](./docs/architecture.md) | implementers | System design. |
| [`docs/roadmap.md`](./docs/roadmap.md) | implementers | Delivery plan and progress. |
| [`CONTRIBUTING.md`](./CONTRIBUTING.md) | contributors | Dev setup, lint/test commands, PR process, scope rules. |
| [`SECURITY.md`](./SECURITY.md) | everyone | How to report a vulnerability. **Do not file public issues for secrets.** |
| [`CODE_OF_CONDUCT.md`](./CODE_OF_CONDUCT.md) | everyone | Contributor Covenant 2.1. |

## Contributing

PRs welcome — please read [`CONTRIBUTING.md`](./CONTRIBUTING.md) first.
The short version:

- Lint (`uv run ruff check`) and tests (`uv run pytest`) must pass; CI
  enforces both.
- One concern per PR. Don't bundle a refactor with a feature.
- Don't add anything from the out-of-scope list
  ([`docs/architecture.md`](./docs/architecture.md) §12).

## License

[MIT](./LICENSE) — © 2026 DurdeuVlad.
