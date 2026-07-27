# #014 — Integration test suite

**Milestone:** M1 integrations · **Labels:** `type:test` `area:integrations` `prio:P0`
**Depends on:** #009, #010, #011, #012, #013

## Summary
`respx`-mocked test suites for every integration client, with special focus on
the two reliability-critical branches.

## Context
- `docs/roadmap.md` Phase 1 exit criteria: every client has a passing test
  suite; the Google auth branch is verified to send the **distinct** alert, not
  a generic crash.

## Tasks
- [ ] `tests/test_telegram.py` — send returns `message_id`; retry on 500.
- [ ] `tests/test_llm_fallback.py` — primary 500 → fallback fires; auth error →
      no retry → fallback; token counts populated.
- [ ] `tests/test_google_auth_branch.py` — `invalid_grant` →
      `GoogleAuthExpiredError` → distinct alert message (not global handler);
      `mark_complete` appends notes (BUG-2 unit-level).
- [ ] `tests/test_leetcode.py` — GraphQL parse; Browserless stub re-raises.
- [ ] `tests/test_youtube.py` — disabled without key; parses with mock.

## Acceptance criteria
- [ ] `uv run pytest tests/test_*` for all M1 clients is green.
- [ ] The auth-branch test asserts the exact alert text and that a generic
      500-style crash path is **not** taken.
- [ ] The fallback test asserts the fallback model was actually called.

## Principles (how they apply here)
See [#034](034-engineering-principles-layers.md). KISS is the tie-breaker.
- **Dependency Inversion:** clients are tested with `respx` mocks because they
  take their transport/config injected — the seam #034 requires.
- **Fail loud:** the auth-branch test asserts the *exact* alert text and that a
  generic crash path is NOT taken; the fallback test asserts the fallback ran.
- **KISS:** one focused suite per client; no shared mega-fixture that couples
  unrelated tests.

## Notes
- HTTP is mocked (`respx`); no live external calls in CI.
