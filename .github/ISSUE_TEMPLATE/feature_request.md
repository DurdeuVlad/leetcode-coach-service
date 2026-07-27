---
name: Feature request
about: Suggest something that should be in a future version
title: "[feat] "
labels: enhancement
body:
  - type: markdown
    attributes:
      value: |
        Thanks for the suggestion. A few things to check first:

        - **Is this in scope for v1?** Read
          [`docs/architecture.md` §12](../../docs/architecture.md) and
          [`docs/business-requirements.md` §7](../../docs/business-requirements.md).
          Multi-user, web UI, Celery/Redis, Browserless, SearXNG, LLM
          tool-calling loops, Anki export, and automated mock interviews
          are **explicitly out of scope for v1**. Propose them anyway, but
          mark them as `phase:2` — they'll be considered after v1 ships.
        - **Is this a behavioral change?** If yes, the change goes into
          `docs/business-requirements.md` first, then code. Reference the
          section you'd be changing.
  - type: textarea
    id: problem
    attributes:
      label: What problem does this solve?
      description: The user-facing pain point, not the solution. "I can't X" rather than "add a Y button".
    validations:
      required: true
  - type: textarea
    id: proposal
    attributes:
      label: What would you like to see?
      description: Your proposed solution. Rough is fine.
    validations:
      required: true
  - type: textarea
    id: alternatives
    attributes:
      label: Alternatives considered
      description: Other ways to solve the same problem, and why they're worse.
  - type: dropdown
    id: scope
    attributes:
      label: Scope
      options:
        - "In scope for v1 (core flow / integration / reliability)"
        - "Phase 2+ (out of v1 scope per architecture.md §12, but worth proposing)"
        - "Not sure"
    validations:
      required: true
  - type: checkboxes
    id: checks
    attributes:
      label: Checklist
      options:
        - label: I read docs/architecture.md §12 and confirmed this isn't already rejected for v1
          required: true
        - label: I'm not asking to rewrite the AI Agent prompts (those are ported verbatim from n8n)
          required: true
