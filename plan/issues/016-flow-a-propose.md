# #016 — `flow_a.propose_5()` (+ BUG-1 fix)

**Milestone:** M2 flow-a · **Labels:** `type:feature` `type:bug-fix` `area:flow-a` `prio:P0`
**Depends on:** #003, #010, #015

## Summary
The daily proposal function: gather data, call the LLM, parse candidates, and
send the numbered Telegram message.

## Context
- `docs/business-requirements.md` FR-1 (whole section). One message, numbered
  list, reasoning per candidate; the flow then **ends** (FR-1.6) — it does not
  wait for the reply.
- **BUG-1:** the n8n version only fetched `solved = true`; Flow A never saw the
  unsolved pool. The port **must** read `leetcode_problems WHERE solved = false`
  and pass it into the prompt.
- **Candidate persistence is deliberately NOT here.** Per `docs/roadmap.md`
  Phase 3a, storing the 5-candidate array (so Flow B can map picks) is decided
  and wired in #020, which extends this function. Phase 2 exit needs only the
  message sent + the BUG-1 regression — do not pre-empt #020.

## Tasks
- [ ] `flows/flow_a.py` `propose_5()`:
  1. Read recent `leetcode_log` (last 30 rows), **unsolved** problems
     (`solved = false`), active `tutor_lessons`.
  2. Call `LLMClient.complete(propose prompt)` with those injected.
  3. Parse JSON (`candidate_list_markdown` + `candidates`).
  4. Send `candidate_list_markdown` to Telegram (single message).
- [ ] Enforce/validate difficulty mix (FR-1.3) and ≤1 solved (FR-1.2) defensively
      after parsing.

## Acceptance criteria
- [ ] Unsolved pool is queried and passed to the prompt (BUG-1 fixed).
- [ ] Exactly one Telegram message is sent with a numbered 5-item list.
- [ ] Flow returns after sending; no waiting for replies.
- [ ] Covered by #018 (incl. BUG-1 regression).

## Principles (how they apply here)
See [#034](034-engineering-principles-layers.md). KISS is the tie-breaker.
- **Single Responsibility / layer:** `propose_5()` *orchestrates* — it reads DB
  via `db/`, calls the LLM via `LLMClient`, sends via the Telegram client. It
  builds no raw HTTP and writes no SQL strings; it holds no prompt text.
- **KISS:** a straight-line gather → call → parse → send. No candidate
  persistence here (that's #020) — resist scope creep.
- **Dependency Inversion:** clients are injected so #018 swaps mocks.
- **Fail loud:** defensive validation of difficulty mix / ≤1-solved rejects bad
  LLM output rather than silently shipping it.

## Notes
- Latency target <30s (NFR-3) — dominated by the single LLM call.
