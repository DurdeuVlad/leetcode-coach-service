# promptfoo — local prompt evaluations

**LOCAL ONLY. Never run in CI/CD — burns OpenAI credits.**

These configs test the prompts used by the LeetCode Coach service using
[promptfoo](https://www.promptfoo.dev/). They are intentionally excluded
from the GitHub Actions workflows (no `promptfoo` step in `ci.yml` or
`deploy.yml`). Run them manually when you change a prompt.

## What's tested

| Config | Prompt | Source | What it checks |
|--------|--------|--------|----------------|
| `promptfooconfig.sol.yaml` | Sol advisor | `src/leetcode_coach/agent/advisor.py` | 4 fields, read-only, no commands |

The Terra agent prompt (`CACHEABLE_TERRA_CONTEXT` in
`src/leetcode_coach/agent/orchestrator.py`) is tool-based and not
tested here — it needs the full Agents SDK + tool stack. The acceptance
suite (`tests/`) covers Terra behavior end-to-end.

## Model note

The configs use `gpt-4o` (not `gpt-5.6-sol`) because promptfoo's standard
`openai:chat:` provider needs a model available via the chat completions
API. The production app uses `gpt-5.6-sol` via the same API, but that
model name may not be available to all API keys. To test with the
production model, change the `providers:` line in any config to
`openai:chat:gpt-5.6-sol`.

## Prerequisites

- Node.js 22+ (install separately or use `npx` which downloads on demand)
- `OPENAI_API_KEY` set in `.env` (already required by the app)

## Running

```bash
# Set the API key (promptfoo reads from env, not .env)
# On Windows (PowerShell):
$env:OPENAI_API_KEY = (findstr /I "OPENAI_API_KEY=" .env).Split("=",2)[1]

# Run the Sol advisor eval (burns ~$0.02-0.05 in OpenAI credits per run)
npx promptfoo@latest eval -c promptfoo/promptfooconfig.sol.yaml

# View results in the web UI
npx promptfoo@latest show

# Or run a single test case for quick iteration
npx promptfoo@latest eval -c promptfoo/promptfooconfig.sol.yaml --filter-first-n 1

# Output to JSON for scripting
npx promptfoo@latest eval -c promptfoo/promptfooconfig.sol.yaml --output results.json
```

## When to run

- **Before committing a prompt change** — verify the new prompt still
  passes all assertions.
- **After a model upgrade** — re-run to catch regressions from model
  behavior changes.
- **Not on every push** — that's what `tests/` (deterministic, mocked)
  is for. promptfoo is for prompt-level regression testing against real
  model output.

## Files

```
promptfoo/
  promptfooconfig.sol.yaml      # Sol advisor eval config
  prompts/                      # Prompt JSON files (system + user messages)
    sol_advisor.json
  tests/                        # Test cases with vars + assertions
    sol_tests.yaml
  README.md                     # This file
```

## Keeping prompts in sync

The prompt JSON files in `prompts/` are extracted from the Python source.
If you change a prompt in `src/leetcode_coach/agent/advisor.py` or
`src/leetcode_coach/agent/orchestrator.py`, update the corresponding
`.json` file here too. The source of truth is the Python code; these files
exist only because promptfoo reads external files.

## CI/CD exclusion

promptfoo is **deliberately excluded** from CI/CD:
- No `promptfoo` step in `.github/workflows/ci.yml` or `deploy.yml`
- `.promptfoo/` cache directory is in `.gitignore`
- `node_modules/` is in `.gitignore`
- `promptfoo_results/` is in `.gitignore`

Running promptfoo in CI would burn OpenAI credits on every push for tests
that are non-deterministic (LLM output varies) and better suited to
manual pre-deploy verification.
