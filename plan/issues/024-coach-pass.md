# #024 — Coach pass call + response parsing

**Milestone:** M3 flow-b · **Labels:** `type:feature` `area:flow-b` `risk:high` `prio:P0`
**Depends on:** #010, #013, #021, #023

## Summary
Run the coach LLM call for a submission and parse its structured response into
typed fields for the lesson decision and post-coach updates.

## Context
- `docs/business-requirements.md` FR-2.5: the coach reads submission text +
  problem metadata + active `tutor_lessons`. Code pasted → full coaching;
  status note ("skipped"/"saw solution") → log status only (+ one-line takeaway
  for "saw solution"), no review.
- Optional YouTube enrichment runs **before** the LLM and is passed in the
  prompt (architecture §12); disabled cleanly if no key (#013).

## Tasks
- [ ] Detect submission kind (code vs status note) before/within the call per
      FR-2.5.
- [ ] Gather inputs: submission, problem metadata (from `pending_review` +
      `leetcode_problems`), active lessons; optionally YouTube links.
- [ ] Call `LLMClient.complete(coach prompt)`.
- [ ] Parse structured response into typed object: `tutor_feedback`,
      `lesson_title`, `lesson_category`, `lesson_should_graduate`, `status`,
      `time_spent_min`.
- [ ] Hand the parsed result to #025 (lesson decision) and #026 (updates).

## Acceptance criteria
- [ ] Code submission → full `tutor_feedback` parsed.
- [ ] "skipped" → status-only result, no review text required.
- [ ] "saw solution" → status + one-line takeaway.
- [ ] Malformed LLM JSON is handled without fabricating data (fail loudly →
      alert), never "log with estimated defaults".
- [ ] Covered by #027.

## Principles (how they apply here)
See [#034](034-engineering-principles-layers.md). KISS is the tie-breaker.
- **Single Responsibility:** this step *calls + parses* only. The lesson
  decision is #025 and the DB/Task/Telegram writes are #026 — keep them apart.
- **Dependency Inversion / Liskov:** uses the injected `LLMClient`; the fallback
  model is transparently substitutable.
- **Fail loud:** malformed LLM JSON fails and alerts — it must never fabricate
  fields or "log estimated defaults" (NFR-1 layer 2).
- **KISS:** the code-vs-status-note branch is a simple check, not a classifier.

## Notes
- Latency target <60s (NFR-3), dominated by the LLM call.
