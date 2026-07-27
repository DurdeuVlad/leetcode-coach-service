# Node docs — LeetCode Coach n8n revamp

One file per node type used in the workflow JSON. Read `00` first — it covers the workflow skeleton, connections object, credentials, and error handling that every other file assumes.

## Files

| File | Node(s) | Used in |
| --- | --- | --- |
| `00-connections-and-general.md` | (cross-cutting) | Both flows |
| `01-schedule-trigger.md` | Schedule Trigger | Flow A start, Flow A expiry |
| `02-telegram-trigger.md` | Telegram Trigger | Flow B start |
| `03-telegram-send.md` | Telegram (send) | Both flows, multiple ops |
| `04-ai-agent.md` | AI Agent + OpenAI Chat Model + Google Gemini Chat Model + tool sub-nodes | Flow A propose, Flow B coach |
| `05-google-tasks.md` | Google Tasks + OAuth2 credential | Flow B create, Flow B update, Flow A expiry |
| `06-data-table.md` | Data Table | Both flows, all 4 tables |
| `07-http-request.md` | HTTP Request | Flow W (weekly LeetCode refresh) |
| `08-code.md` | Code | Both flows, 4 logic nodes |
| `09-switch-if.md` | Switch, IF | Both flows, 4 branch points |

## How to use these docs

Each file has:
1. **Node type and docs link** — the canonical n8n URL.
2. **Why this node, not the alternative** — e.g., Schedule Trigger vs Cron, HTTP Request vs GraphQL.
3. **Node config as JSON** — paste-ready into the workflow JSON's `nodes` array. Replace `__CREDENTIAL_ID__` with the actual credential ID after import (or let n8n match by credential name).
4. **Field reference** — what each parameter does and the gotchas.
5. **Settings tab** — retry/onError config.
6. **Common issues** — the failure modes you'll actually hit.

## Build order

Suggested order if you're building the workflow in n8n UI:

1. Create the 4 credentials (see `00`).
2. Create the 4 data tables (see `06`).
3. Build Flow A: Schedule Trigger → Data Table reads → AI Agent → Telegram send (5-list). Flow A ends here.
4. Build Flow A expiry: Schedule Trigger → Data Table get → Code → Data Table update + Google Tasks update + Telegram summary.
5. Build Flow B: Telegram Trigger → IF (has reply_to?) → IF (lookup found?) → two branches:
   - **Pick branch** (lookup miss): Code (parse selection) → IF (skip?) → loop (Telegram per-problem + Google Tasks create + Data Table insert pending_review).
   - **Coach branch** (lookup found): Code (correlate reply) → AI Agent (coach pass) → Code (lesson decision) → Switch → Data Table writes + Google Tasks update + Telegram confirmation.
6. Build the Error Trigger workflow (see `00`).
7. Build Flow W (weekly refresh): Schedule Trigger → Code (slug list) → loop HTTP Request → Code (flatten) → Data Table upsert.
8. Apply the Google OAuth "In production" fix (see `05`).
9. Activate Flow A, Flow B, Error workflow. Leave Flow W inactive until you've tested it manually once.

## What's not in these docs

- The actual system prompts for the AI Agent — those live in `04-ai-agent.md` as starting points, but you'll tune them after the first week of real runs.
- The LeetCode GraphQL query library — `07-http-request.md` has one example query; the full set (problem list, user profile, submission history) is a separate doc to write when you build Flow W.
- Cost monitoring — not a node, an n8n setting. Turn on execution data retention and check the executions list weekly for runaway AI Agent calls.
