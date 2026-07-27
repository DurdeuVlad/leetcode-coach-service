# General: workflow JSON, connections, credentials, error handling

Applies to every node in this directory. Source: n8n docs (2026-07-26).
Ref: https://docs.n8n.io/build/manage-workflows/export-and-import, https://docs.n8n.io/connect/n8n-api/models

## Workflow JSON skeleton

Every exported workflow is a single JSON object. Minimum required keys: `name`, `nodes`, `connections`, `settings`. When you import this back into n8n, n8n re-creates the canvas from these four keys.

```json
{
  "name": "LeetCode Coach — Flow A (Schedule + Expiry)",
  "active": false,
  "nodes": [ /* see per-node docs */ ],
  "connections": { /* see below */ },
  "settings": {
    "executionOrder": "v1",
    "timezone": "Europe/Bucharest"
  },
  "pinData": {},
  "meta": { "templateCredsSetupCompleted": true },
  "tags": []
}
```

Two workflows get exported as two separate JSON files. Do not merge them — Flow A (schedule-only: daily 5-list send + 05:05 expiry sweep) and Flow B (single Telegram Trigger routing both pick replies and code replies) have different triggers and must stay independent so each can be toggled active/inactive on its own.

## Workflow settings that matter for us

- **`timezone: "Europe/Bucharest"`** — set per-workflow, not just at instance level. Schedule Trigger reads this first, then falls back to `GENERIC_TIMEZONE` env var. If unset, n8n defaults to `America/New_York` and your 09:05 trigger fires at 16:05 Bucharest. Set it on both workflows.
- **`executionOrder: "v1"`** — the only supported value today; keep it explicit so a future n8n upgrade doesn't silently change execution order.
- **`saveManualExecutions: true`** — keep manual runs in the executions list while debugging; flip to `false` once stable to keep the list clean.

## The `connections` object

n8n stores edges separately from nodes. A node never knows what's downstream — the `connections` object maps `sourceNodeName -> outputIndex -> [targetNodeName, inputIndex]`. Get a name wrong here and the canvas shows a broken wire.

```json
"connections": {
  "Schedule Trigger (daily 09:05)": {
    "main": [
      [
        { "node": "AI Agent (propose 5)", "type": "main", "index": 0 }
      ]
    ]
  },
  "AI Agent (propose 5)": {
    "main": [
      [
        { "node": "Telegram (send 5-list)", "type": "main", "index": 0 }
      ]
    ]
  },
  "Telegram (send 5-list)": {
    "main": [
      [
        { "node": "Data Table (mark today expired)", "type": "main", "index": 0 }
      ]
    ]
  },
  "Schedule Trigger (daily 05:05 expiry sweep)": {
    "main": [
      [
        { "node": "Data Table (mark today expired)", "type": "main", "index": 0 }
      ]
    ]
  },
  "Telegram Trigger (incoming reply)": {
    "main": [
      [
        { "node": "IF (has reply_to?)", "type": "main", "index": 0 }
      ]
    ]
  },
  "IF (has reply_to?)": {
    "main": [
      [ { "node": "Data Table (lookup by message_id)", "type": "main", "index": 0 } ],
      [ { "node": "Data Table (get today open)", "type": "main", "index": 0 } ]
    ]
  },
  "Data Table (lookup by message_id)": {
    "main": [
      [ { "node": "IF (lookup found?)", "type": "main", "index": 0 } ]
    ]
  },
  "IF (lookup found?)": {
    "main": [
      [ { "node": "Code (correlate reply)", "type": "main", "index": 0 } ],
      [ { "node": "Code (parse selection)", "type": "main", "index": 0 } ]
    ]
  },
  "Data Table (get today open)": {
    "main": [
      [ { "node": "Code (correlate reply)", "type": "main", "index": 0 } ]
    ]
  },
  "Code (parse selection)": {
    "main": [
      [ { "node": "IF (skip?)", "type": "main", "index": 0 } ]
    ]
  },
  "IF (skip?)": {
    "main": [
      [ { "node": "Telegram (no valid picks)", "type": "main", "index": 0 } ],
      [ { "node": "Loop Over Items", "type": "main", "index": 0 } ]
    ]
  }
}
```

Two things to notice in the routing above:

1. **Flow A ends at `Telegram (send 5-list)`** plus a parallel `Schedule Trigger (daily 05:05 expiry sweep)` → `Data Table (mark today expired)`. There is no Telegram Trigger in Flow A. The 5-list message goes out, the day's open `pending_review` rows get expired at 05:05 the next morning, and the workflow is done.
2. **Flow B's single Telegram Trigger fans out via two IFs in series.** The first IF (`has reply_to?`) splits standalone messages from reply-to messages. The reply-to branch then hits `Data Table (lookup by message_id)` and a second IF (`lookup found?`) that splits coach-pass (found a `pending_review` row → the user replied to a per-problem message) from pick-parse (no row → the user replied to the 5-list message, whose ID was never stored in `pending_review`). The standalone branch goes straight to fuzzy match via `Data Table (get today open)`. Both branches converge on `Code (correlate reply)` — but with different upstream Data Table nodes populated, so the Code node's `if (replyTo && replyTo.message_id)` guard picks the right path.

Rules that bite if you ignore them:

- The outer key is the **source node's `name` field exactly**, including spaces and emoji if you put them in. Renaming a node without updating `connections` orphans it.
- The first array index is the **output index** (0 for the only output, 0/1 for IF true/false, 0..N for Switch). The inner array holds all targets on that output — multiple targets run in parallel.
- AI Agent's model and tool sub-nodes connect via `ai_language_model` and `ai_tool` keys, **not** `main`. See `04-ai-agent.md` for the exact shape.
- Telegram Trigger has only one output. There is no "error output" by default — wire the trigger's `main[0]` to a Code node that wraps the whole flow in try/catch if you want workflow-level error capture.

## Credentials — what to create once, reuse everywhere

Create these four credentials in n8n's Credentials tab before importing any workflow. n8n matches by **name** on import, so use these exact names:

| Credential name | Type | Used by |
| --- | --- | --- |
| `Telegram — LeetCode Coach bot` | `telegramApi` | Telegram Trigger, Telegram send node |
| `Google Tasks — personal` | `googleTasksOAuth2Api` | Google Tasks node (Flow A create, Flow B update) |
| `OpenAI — gpt-5.6` | `openAiApi` | OpenAI Chat Model sub-node (both flows) |
| `Google Gemini — flash` | `googleGeminiApi` | Google Gemini Chat Model sub-node (fallback, both flows) |

If you import a workflow and n8n shows "Credential not found," it's because the name in the JSON doesn't match. Fix the name in the Credentials tab, don't reselect in the node — reselecting breaks the next import.

## Error handling — three layers, applied per flow

n8n has three independent error mechanisms. Use all three; each catches a different failure mode.

1. **Node-level: `retryOnFail` + `onError: "continue (using error output)"`**
   Set on flaky external calls only: HTTP Request to LeetCode GraphQL, Google Tasks create/update, AI Agent. Settings live under each node's **Settings** tab (not Parameters).
   ```json
   {
     "name": "HTTP Request (LeetCode GraphQL)",
     "retryOnFail": true,
     "maxTries": 3,
     "waitBetweenTries": 5000,
     "onError": "continue (using error output)"
   }
   ```
   The error output is `main[1]` on nodes that support it. Wire it to a Telegram "something broke" message so you actually notice.

2. **Workflow-level: Error Trigger workflow**
   Create a third tiny workflow with an Error Trigger node. In Flow A and Flow B settings, set **On Error → Error Workflow** to that workflow. It catches anything that escapes node-level handling (uncaught throws, OOM, credential expiry mid-run). The Error Trigger payload includes `execution.id`, `node.name`, and the error message — send all three to Telegram so you can jump to the failed execution.

3. **Credential-level: Google OAuth "In production" status**
   This is the root cause of the "randomly disconnects" symptom. While the Google Cloud OAuth consent screen is in **Testing** status, refresh tokens expire after 7 days and n8n silently loses access. Fix: Google Cloud Console → APIs & Services → OAuth consent screen → **Publish to production**. You don't need verification (no sensitive scopes — Google Tasks is a non-sensitive scope). After publishing, refresh tokens last indefinitely unless revoked. See `05-google-tasks.md` for the full setup.

## Naming convention used across these docs

Every node `name` is `<Type> (<role>)`. The type is what n8n calls it; the role is what it does in this flow. This makes the canvas readable and makes `$("Node Name").first()` expressions in Code nodes self-documenting.

Examples: `Schedule Trigger (daily 09:05)`, `AI Agent (propose 5)`, `Data Table (insert pending_review)`, `Telegram (send 5-list)`.

Don't shorten to `Schedule` or `AI Agent` alone — you'll end up with two `Schedule Trigger` nodes in the same workflow once the expiry sweep is added, and n8n will refuse to wire them correctly.

## Two gotchas to know before building

- **Race between Flow A's expiry sweep and Flow B's reply correlation.** Both read `pending_review`. If a reply lands at 04:55 Bucharest and the expiry sweep runs at 05:05, the sweep could mark the row `expired` while Flow B is mid-tutor-pass. Mitigation: in Flow B's update cascade, set the `WHERE` clause to `status = 'open' AND message_id = ?` (not just `message_id = ?`). If zero rows update, the row was already expired — skip the Google Task update and tell the user "marked expired, log anyway."
- **Sub-node expression resolution.** OpenAI Chat Model and Google Gemini Chat Model are sub-nodes. Per n8n docs, expressions in sub-node parameters always resolve to the **first item** of the input, even when the parent AI Agent is processing many items. Don't try to make the model name dynamic per-item — set it as a fixed string.
