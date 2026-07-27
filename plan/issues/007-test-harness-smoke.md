# #007 — Test harness + smoke test

**Milestone:** M0 bootstrap · **Labels:** `type:test` `area:db` `prio:P0`
**Depends on:** #003, #004, #005, #006

## Summary
Establish the test infrastructure (real Postgres via testcontainers, async
support) and one end-to-end smoke test that proves the skeleton works.

## Context
- `docs/architecture.md` §10: DB tests use a throwaway Postgres via
  `testcontainers-postgres` — **no mocking the DB**. HTTP is mocked with
  `respx` (used from #014 onward).
- Roadmap Phase 0 exit criteria: app + postgres healthy, `/health` 200,
  `alembic upgrade head` creates all 4 tables.

## Tasks
- [ ] `tests/conftest.py` — session-scoped `testcontainers` Postgres fixture
      that runs `alembic upgrade head` against it and yields a session factory.
- [ ] Configure `pytest-asyncio` (auto mode).
- [ ] Smoke test:
  - app boots and `/health` returns 200 against the test DB;
  - all 4 tables exist after migration;
  - a `LeetCodeProblem` row can be inserted and read back.

## Acceptance criteria
- [ ] `uv run pytest` is green from a clean checkout (Docker available).
- [ ] The smoke test fails loudly if a table is missing or `/health` is not 200.

## Principles (how they apply here)
See [#034](034-engineering-principles-layers.md). KISS is the tie-breaker.
- **KISS:** one smoke test proving boot + migrate + round-trip; no elaborate
  fixtures before there's code to exercise.
- **Dependency Inversion:** DB is real (testcontainers), HTTP will be mocked via
  injected clients — the seam that makes later flow tests possible.
- **Fail loud:** the smoke test asserts hard (missing table or non-200
  `/health` fails), never skips silently.

## Notes
- This fixture is reused by every later DB-touching test suite.
