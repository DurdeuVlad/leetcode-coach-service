# #001 — Project scaffolding (uv, pyproject, ruff)

**Milestone:** M0 bootstrap · **Labels:** `type:infra` `prio:P0`
**Depends on:** none

## Summary
Create the Python project skeleton and dependency manifest so every later issue
has a place to write code and a single command to install deps.

## Context
- Stack is fixed in `docs/architecture.md` §2. Do not substitute tools.
- Layout is fixed in `docs/architecture.md` §3. Create the directory tree and
  empty `__init__.py` packages now; files get filled in by later issues.

## Tasks
- [ ] `pyproject.toml` with runtime deps: `fastapi`, `uvicorn[standard]`,
      `apscheduler`, `python-telegram-bot>=21`, `openai`, `google-genai`,
      `google-api-python-client`, `google-auth`, `httpx`, `tenacity`,
      `sqlmodel`, `alembic`, `psycopg[binary]`, `pydantic-settings`, `structlog`.
- [ ] Dev deps: `pytest`, `pytest-asyncio`, `respx`, `testcontainers[postgres]`,
      `ruff`.
- [ ] Pin to versions published ≥7 days ago; no floating `latest`/`*`.
- [ ] Configure `ruff` (lint + format) in `pyproject.toml`.
- [ ] Create the `src/leetcode_coach/` package tree from architecture §3
      (`db/`, `integrations/`, `flows/`, `prompts/`, `scheduling/`, `webhooks/`)
      with `__init__.py` files.
- [ ] Create empty `tests/` package.
- [ ] `README.md` at repo root already exists — link the new local-dev commands
      (architecture §10) if missing.

## Acceptance criteria
- [ ] `uv sync` installs cleanly from a clean checkout.
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass on the
      (empty) tree.
- [ ] `python -c "import leetcode_coach"` succeeds.

## Principles (how they apply here)
See [#034](034-engineering-principles-layers.md). KISS is the tie-breaker.
- **KISS:** one `pyproject.toml`, one lint/format tool (`ruff`), one package
  manager (`uv`). No task runners, no plugin zoo.
- **YAGNI:** pin exactly the deps in `architecture.md` §2 — no "might need it"
  libraries, no optional extras added speculatively.
- **Layer responsibility:** this issue creates *structure only*; every package
  is an empty shell. No logic leaks into `__init__.py` files.

## Notes / out of scope
- No application logic here — just structure and tooling.
- No Celery/Redis/queue (architecture §12).
