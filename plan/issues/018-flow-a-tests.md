# #018 — Flow A tests (+ BUG-1 regression)

**Milestone:** M2 flow-a · **Labels:** `type:test` `area:flow-a` `prio:P0`
**Depends on:** #016, #017

## Summary
Test Flow A end-to-end with a mocked LLM and Telegram, including the BUG-1
regression test.

## Context
- `docs/roadmap.md` Phase 2 exit criteria: trigger `propose_5()` locally →
  Telegram receives a 5-candidate message with reasoning per candidate; the
  unsolved-pool regression test passes.

## Tasks
- [ ] `tests/test_flow_a.py`:
  - Seed the test DB (testcontainers) with a mix of solved/unsolved problems,
    30 log rows, and active lessons.
  - Mock `LLMClient` to return a canned 5-candidate JSON.
  - Assert the Telegram send is called once with the markdown.
  - **BUG-1 regression:** assert the data passed to the prompt includes the
    unsolved pool (`solved = false` rows), not solved rows.
- [ ] Defensive-validation tests: difficulty mix and ≤1-solved guardrails.

## Acceptance criteria
- [ ] Suite is green.
- [ ] Removing the unsolved-pool query makes the regression test fail (verify
      the test actually guards BUG-1).

## Principles (how they apply here)
See [#034](034-engineering-principles-layers.md). KISS is the tie-breaker.
- **Dependency Inversion:** the mocked `LLMClient` + Telegram client prove the
  flow depends on abstractions, not concrete SDKs.
- **Fail loud:** the BUG-1 regression must *fail* if the unsolved-pool query is
  removed — the test guards the behavior, not just runs it.
- **KISS:** one seeded DB + one canned LLM response; assert the observable
  outputs, don't over-mock internals.

## Notes
- LLM is mocked; no live calls.
