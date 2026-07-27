# Contributing to LeetCode Coach Service

Thanks for your interest in contributing. This is a small, single-maintainer
project with a strong opinionated spec — please read this file before opening
a PR so neither of us wastes time.

## Read first

1. [`README.md`](./README.md) — what this is and why it exists.
2. [`AGENTS.md`](./AGENTS.md) — the operating rules. **Read this before
   touching code.** It defines the source of truth (business-requirements
   wins over code), the do-nots (no Celery, no multi-user, no prompt
   rewrites), and the gotchas.
3. [`docs/business-requirements.md`](./docs/business-requirements.md) — the
   behavioral contract. If your change contradicts this doc, the doc wins;
   either update the doc with an explicit decision first, or don't make the
   change.
4. [`docs/architecture.md`](./docs/architecture.md) §12 — what is explicitly
   out of scope for v1. If your PR adds Celery, Redis, a task queue, a
   separate worker, multi-user support, a web UI, or Browserless/SearXNG,
   it will be rejected. These are documented decisions, not oversights.

## Dev setup

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
# Clone and install (dev extras + ruff + pytest)
git clone https://github.com/DurdeuVlad/leetcode-coach-service.git
cd leetcode-coach-service
uv sync --extra dev

# Copy env template and fill in real values for local dev
cp .env.example .env
#   TELEGRAM_BOT_TOKEN, OPENAI_API_KEY, DATABASE_URL, etc.
#   For local Postgres: docker compose up -d postgres

# Run migrations + start the app
docker compose up -d postgres
uv run alembic upgrade head
uv run uvicorn leetcode_coach.main:app --reload

# Health check
curl localhost:8000/health   # -> {"status":"ok", ...}
```

## Lint and test

Run these before pushing. CI runs the same commands.

```bash
# Lint (must be clean)
uv run ruff check src tests
uv run ruff format --check src tests

# Tests
uv run pytest
```

`ruff check` uses the config in `pyproject.toml` (ruleset: E/F/W/I/UP/B/SIM/RUF,
line length 100, target py312). `ruff format --check` verifies formatting
without rewriting — run `uv run ruff format src tests` to fix.

## Before you open a PR

1. **Branch from `main`.** Name it `feat/<short>`, `fix/<short>`, or
   `docs/<short>`.
2. **Lint and tests pass locally.** CI will run the same; don't push red.
3. **One concern per PR.** A lint fix + a feature + a refactor in one PR
   makes review slow. Split them.
4. **Don't rewrite the prompts.** The AI Agent prompts in
   `n8n-reference/workflows/*.json` are ported **verbatim** into
   `src/leetcode_coach/prompts/`. If you think a prompt has a bug, open an
   issue first with the bug description; the fix goes into
   `docs/business-requirements.md` and then the prompt, with a commit
   message that names the bug. See `AGENTS.md` "Do not" section.
5. **Don't add scope.** Check `docs/architecture.md` §12 before adding a
   dependency, a service, or a feature. If it's listed as out of scope, the
   answer is no for v1 — open an issue marked `phase:2` if you want to
   propose it for later.
6. **Tests with the code, not after.** New integration client → add a
   `respx`-mocked test in `tests/`. New flow → add a golden-output or
   behavior test. The coach pass is the highest-risk piece; it gets a
   golden-output test (see `docs/roadmap.md` Phase 3b).
7. **Update `docs/roadmap.md` checkboxes** if your PR completes a phase
   item. Mark the phase's todo `[x]` only when its exit criteria are met.

## Commit message style

Conventional Commits, lowercase, imperative:

```
feat(telegram): add webhook secret_token verification
fix(google-tasks): append notes instead of replacing (BUG-2)
docs(roadmap): mark phase 0 exit criteria met
test(llm): cover auth-error → no-retry → fallback branch
chore(deps): bump openai to 1.55.0
```

Reference issues by number: `Closes #014`, `Refs #010`.

## PR template

When you open a PR, the template will prompt you for:
- Summary (what + why)
- Linked issue(s)
- Test plan (what you ran to verify)
- Scope check (confirm you didn't add anything from the §12 out-of-scope list)

## Issue triage

Issues are tracked in two places:

- **GitHub Issues** — for bug reports, feature requests, and external
  contributor-facing work. Use the issue templates (`.github/ISSUE_TEMPLATE/`).
- **`plan/issues/`** — the internal phased implementation plan
  (`001-project-scaffolding.md` through `034-...`). These are the
  maintainer's working set; external contributors don't need to read them
  unless an issue references one.

If you're an external contributor, **open a GitHub issue first** before
starting work on anything non-trivial — it avoids duplicate work and
ensures the change is in scope.

## License

By contributing, you agree that your contributions are licensed under the
project's [MIT License](./LICENSE).
