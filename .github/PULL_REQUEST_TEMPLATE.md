## Summary

<!-- What does this PR do, and why? One or two paragraphs. Reference the
     issue(s) it closes: "Closes #014", "Refs #010". -->

## Linked issues

<!-- e.g. Closes #014, Refs #010. Use "Refs" (not "Closes") if the issue
     has more work after this PR. -->

## Test plan

<!-- What did you run to verify this works? Be specific — "ran
     `uv run pytest tests/test_telegram.py`" not "tested it". -->

- [ ] `uv run ruff check src tests` is clean
- [ ] `uv run ruff format --check src tests` is clean
- [ ] `uv run pytest` is green
- [ ] If new integration client: added a `respx`-mocked test
- [ ] If new flow: added a behavior or golden-output test

## Scope check

<!-- Confirm you did NOT add anything from the out-of-scope list
     (docs/architecture.md §12). If you did, stop and explain why in the
     linked issue first. -->

- [ ] No Celery / Redis / task queue / separate worker
- [ ] No multi-user support
- [ ] No web UI
- [ ] No Browserless / SearXNG integration
- [ ] No LLM tool-calling loop in the coach pass
- [ ] No prompt rewrites (AI Agent prompts are ported verbatim from n8n)
- [ ] No new dependency without justification in the linked issue

## Docs

- [ ] Updated `docs/roadmap.md` checkboxes if a phase item was completed
- [ ] Updated `docs/business-requirements.md` first if this is a behavioral change
- [ ] Updated `AGENTS.md` if the operating rules changed
