# #015 — Port `propose` prompt verbatim

**Milestone:** M2 flow-a · **Labels:** `type:feature` `area:prompts` `risk:high` `prio:P0`
**Depends on:** #001

## Summary
Port the Flow A candidate-selection prompt **verbatim** from the n8n reference
into `prompts/propose.py`.

## Context
- **Verbatim port rule** (AGENTS.md): the prompt text lives in the AI Agent node
  `text` field of
  `n8n-reference/workflows/flow-a-schedule-and-expiry.json`. Do **not** rewrite
  or "improve" it. If it has a bug, fix it in
  `docs/business-requirements.md` first, then here, with a commit that names
  the bug.
- Behavioral requirements the prompt must satisfy: FR-1.2 (draw from unsolved
  pool, ≤1 solved for spaced repetition), FR-1.3 (2-3 hard + 2-3 medium, never
  5 of one), FR-1.4 (bias by active lessons + recent log), FR-1.5 (`reasoning`
  + `coaching_hint` per candidate), FR-1.7 (never invent titles/URLs).

## Tasks
- [ ] `prompts/propose.py` — system + user prompt strings, ported verbatim.
- [ ] Define the expected JSON output contract the flow will parse:
      `candidate_list_markdown` + `candidates[]` (each with title, slug, url,
      difficulty, reasoning, coaching_hint).
- [ ] Placeholders for injected data: unsolved pool, recent log (30), active
      lessons.

## Acceptance criteria
- [ ] Prompt text matches the n8n source (diff-checked); only templating
      placeholders differ.
- [ ] The documented output JSON contract is captured next to the prompt for
      #016 to parse against.

## Principles (how they apply here)
See [#034](034-engineering-principles-layers.md). KISS is the tie-breaker.
- **Single Responsibility:** `prompts/propose.py` holds *text + output contract*
  only. It does not call the LLM (#016) or parse side effects.
- **KISS + verbatim rule:** port the prompt as-is; do not "improve" it. The
  simplest faithful port is the correct one.
- **Explicit over implicit:** the expected JSON contract is written down next to
  the prompt so #016 parses against a stated shape, not a guess.

## Notes
- Do **not** edit `n8n-reference/` — it is frozen.
