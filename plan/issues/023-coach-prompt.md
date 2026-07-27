# #023 — Port `coach` prompt verbatim

**Milestone:** M3 flow-b · **Labels:** `type:feature` `area:prompts` `risk:high` `prio:P0`
**Depends on:** #001

## Summary
Port the long Flow B coach prompt **verbatim** into `prompts/coach.py` — the one
with the 5 coaching dimensions + lesson-decision instructions.

## Context
- **Verbatim port rule** (AGENTS.md): source is the AI Agent node `text` field
  in `n8n-reference/workflows/flow-b-telegram-and-coach.json`. No rewriting.
- Behavioral contract: `docs/business-requirements.md` FR-2.5 (coaching
  dimensions: correctness, complexity, style/idiom, pattern coaching, next
  step; honest, no false praise; status-note handling) and FR-2.6 (lesson
  decision + double-gated graduation language).

## Tasks
- [ ] `prompts/coach.py` — system + user prompt strings, ported verbatim.
- [ ] Define the structured output contract #024 will parse:
      `tutor_feedback`, `lesson_title`, `lesson_category`,
      `lesson_should_graduate`, `status`, `time_spent_min`.
- [ ] Placeholders for injected data: submission text, problem metadata, active
      `tutor_lessons`, optional YouTube links (#013).

## Acceptance criteria
- [ ] Prompt text matches the n8n source (diff-checked); only placeholders
      differ.
- [ ] Output-contract fields documented alongside the prompt.
- [ ] Prompt instructs "saw solution" → one-line takeaway; "skipped" → status
      only (FR-2.5).

## Principles (how they apply here)
See [#034](034-engineering-principles-layers.md). KISS is the tie-breaker.
- **Single Responsibility:** `prompts/coach.py` holds *text + output contract*
  only — no LLM call, no DB, no lesson logic.
- **KISS + verbatim rule:** port the long coach prompt as-is; the simplest
  faithful port is correct. Fixes go through business-requirements first.
- **Explicit over implicit:** the structured output fields
  (`tutor_feedback`, `lesson_*`, `status`, `time_spent_min`) are documented so
  #024 parses a stated contract.

## Notes
- Do **not** edit `n8n-reference/`. If the prompt has a real bug, fix
  `docs/business-requirements.md` first, then port, naming the bug in the
  commit.
