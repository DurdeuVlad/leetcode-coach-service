# n8n-reference — FROZEN

**Do not edit anything in this directory.**

This directory contains the original n8n v3 implementation, preserved as
the authoritative behavioral spec for the Python port. It is reference
material, not working code.

## What's here

- `README.md` — the n8n v3 behavioral spec. The Python port's
  `docs/business-requirements.md` is extracted from this, stripped of
  n8n-specific framing.
- `nodes/` — per-node documentation. Useful when porting flow logic.
- `workflows/flow-a-schedule-and-expiry.json` — Flow A (daily candidates).
  The AI Agent node's `text` field is the **verbatim source** for
  `src/leetcode_coach/prompts/propose.py`.
- `workflows/flow-b-telegram-and-coach.json` — Flow B (reply router +
  coach pass). The AI Agent node's `text` field is the **verbatim source**
  for `src/leetcode_coach/prompts/coach.py`.

## How to use it

When implementing a phase of `docs/roadmap.md`:

1. Read the relevant workflow JSON to find the exact node `text` / params.
2. Port prompts **verbatim** — no rewriting, no "improving."
3. Port flow logic as Python functions. The node graph maps to function
   calls; the connections map to data flow.
4. The n8n audit (see `docs/roadmap.md` and `AGENTS.md` "Key gotchas")
   identified two business-logic bugs and three error-handling gaps in
   these workflows. The Python port fixes them. Do not re-introduce them
   by copying the n8n behavior blindly.

## If the spec feels wrong

Open a decision in `docs/business-requirements.md` (§8 "Open decisions"
or a new section). Do **not** edit this directory to "fix" the spec —
that destroys the audit trail.
