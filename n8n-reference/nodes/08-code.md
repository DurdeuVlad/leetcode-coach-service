# Code node

Node type: `n8n-nodes-base.code`
Docs: https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.code

Used wherever logic is too gnarly for expressions but too small to justify an AI Agent call. JavaScript mode, not Python — the homelab n8n supports both, but JS is more idiomatic in n8n and has better `$` syntax support.

## Two modes — pick per node

- **Run Once for All Items** (default): code runs once, receives all input items in `$input.all()`. Use when you're aggregating, filtering, or producing a different number of output items than input.
- **Run Once for Each Item**: code runs per item, receives `$json` for the current item. Use when output count equals input count and each item is independent.

## Node 1 — parse selection (Flow B pick branch, after reply-to-5-list detected)

Turns the user's "2 5" reply into 2 items for the per-problem loop. **Run Once for All Items.**
This node lives in Flow B, not Flow A — Flow A is schedule-only and ends after sending the
5-candidate list. Flow B's Telegram Trigger routes the reply here when `message.reply_to_message.message_id`
is NOT found in `pending_review` (meaning the reply was to the 5-list message, whose ID was never
stored in `pending_review`). See `09-switch-if.md` for the routing IF.

```json
{
  "type": "n8n-nodes-base.code",
  "typeVersion": 2,
  "name": "Code (parse selection)",
  "position": [640, 300],
  "parameters": {
    "jsCode": "const trigger = $('Telegram Trigger (incoming reply)').first().json;\nconst reply = (trigger.message?.text || '').trim();\nconst nums = reply.match(/\\d+/g) || [];\nconst picks = nums.slice(0, 2).map(Number);\nconst candidates = $('AI Agent (propose 5)').first().json.candidates || [];\nconst chosen = picks.filter(n => n >= 1 && n <= candidates.length).map(n => candidates[n - 1]);\nif (chosen.length === 0) {\n  return [{ json: { _skip: true, reason: 'no valid picks' } }];\n}\nreturn chosen.map(c => ({ json: c }));"
  }
}
```

Notes:
- No LLM call — regex is enough. Don't burn tokens parsing "2 5".
- `slice(0, 2)` caps at 2 picks (v3). If the user replies "1 2 3 4", only the first 2 are taken. If they reply "1", only 1 is taken — that's allowed, the loop just runs once.
- `_skip: true` is a sentinel; the next node (Telegram send) checks for it and skips. Alternatively, wire this output to an IF node that routes `_skip` items to a "no valid picks" Telegram message.
- `candidates` must be a JSON array field on the AI Agent's output. If the agent returns a string, parse it first: `JSON.parse($('AI Agent (propose 5)').first().json.candidates)`.
- The chosen items carry through `reasoning` and `coaching_hint` from the agent's output — these are passed downstream to Telegram per-problem message and Google Task notes.

## Node 2 — correlate reply (Flow B coach branch, after lookup-found routing)

Reads the matched `pending_review` row from the upstream `Data Table (lookup by message_id)` node
(the routing IF in `09-switch-if.md` already guaranteed the lookup found a row). Falls back to
fuzzy match on problem title only for the no-reply_to path. **Run Once for All Items.**

```json
{
  "type": "n8n-nodes-base.code",
  "typeVersion": 2,
  "name": "Code (correlate reply)",
  "position": [820, 300],
  "parameters": {
    "jsCode": "const trigger = $('Telegram Trigger (incoming reply)').first().json;\nconst replyTo = trigger.message?.reply_to_message;\n\n// Path 1: exact match — the routing IF already confirmed the lookup found a row.\n// Read it directly from the Data Table node; don't re-do the lookup.\nif (replyTo && replyTo.message_id) {\n  const exact = $('Data Table (lookup by message_id)').first().json;\n  if (exact && exact.message_id) {\n    return [{ json: { ...exact, correlation: 'exact', user_text: trigger.message?.text || '' } }];\n  }\n}\n\n// Path 2: fuzzy match by problem title in today's open pending_review\n// (only reached when the trigger had no reply_to — the no-reply_to branch of IF (has reply_to?))\nconst openRows = $('Data Table (get today open)').all().map(i => i.json);\nconst text = (trigger.message?.text || '').toLowerCase();\nconst matches = openRows.filter(r => {\n  const t = (r.problem_title || '').toLowerCase();\n  return text.includes(t) || t.includes(text);\n});\n\nif (matches.length === 1) {\n  return [{ json: { ...matches[0], correlation: 'fuzzy', user_text: trigger.message?.text || '' } }];\n}\n\n// Path 3: ambiguous or no match — ask user\nreturn [{ json: { _ask: true, candidates: matches.map(r => r.problem_title), user_text: trigger.message?.text || '' } }];"
  }
}
```

Notes:
- `$('Data Table (lookup by message_id)').first().json` — references the upstream Data Table node by name. The Data Table node must run before this Code node. The routing IF `IF (lookup found?)` guarantees this branch only runs when the lookup returned a row.
- Fuzzy match is intentionally simple: substring either direction. Don't reach for Levenshtein here — problem titles are distinctive enough that substring works.
- `_ask: true` sentinel routes to the "which problem?" Telegram message. The user replies again, this time hopefully with a clear reply-to.
- Path 1 and Path 2 are mutually exclusive in practice: Path 1 only runs when `replyTo` exists (the `has reply_to?` IF true branch + `lookup found?` IF true branch), Path 2 only runs when `replyTo` is absent (the `has reply_to?` IF false branch). The `if (replyTo && replyTo.message_id)` guard is defensive — if a future refactor wires things differently, the code still does the right thing.

## Node 3 — lesson persistence decision (Flow B, after coach pass)

Decides insert-new vs bump-existing vs graduate vs no-lesson. **Run Once for All Items.**

```json
{
  "type": "n8n-nodes-base.code",
  "typeVersion": 2,
  "name": "Code (lesson decision)",
  "position": [1300, 300],
  "parameters": {
    "jsCode": "const coach = $('AI Agent (coach pass)').first().json;\nconst title = (coach.lesson_title || '').trim();\nif (!title) {\n  return [{ json: { action: 'none' } }];\n}\nconst existing = $('Data Table (lookup lesson)').first().json;\nif (existing && existing.title) {\n  if (coach.lesson_should_graduate === true && existing.times_reinforced >= 5) {\n    return [{ json: { action: 'graduate', lesson_id: existing.id, title: existing.title } }];\n  }\n  return [{ json: { action: 'bump', lesson_id: existing.id, current_count: existing.times_reinforced } }];\n}\nreturn [{ json: { action: 'insert', title, category: coach.lesson_category || 'general' } }];"
  }
}
```

Wire the four outputs (via a Switch node, see `09-switch-if.md`) to:
- `none` → skip straight to leetcode_log insert
- `bump` → Data Table update times_reinforced (increment by 1)
- `insert` → Data Table insert new tutor_lessons row (active=true, times_reinforced=0)
- `graduate` → Data Table update active=false on the matched lesson row

Notes:
- The graduation guard is double-gated: the coach must set `lesson_should_graduate=true` AND the existing lesson must have `times_reinforced >= 5`. The Code node is the source of truth — even if the coach hallucinates graduation on a 2-reinforcement lesson, the Code node refuses. This prevents premature retirement of lessons.
- The `existing.times_reinforced >= 5` check uses the value fetched from the Data Table lookup, not the coach's claim. Always trust the database over the LLM.
- Variable renamed from `tutor` to `coach` to match the v3 node name `AI Agent (coach pass)`. If you have older workflows referencing `$('AI Agent (tutor pass)')`, update them — n8n node name changes break `$()` references.

## Node 4 — expiry sweep (Flow A, scheduled at 05:05)

Pulls today's open pending_review rows and marks them expired. **Run Once for All Items.**

```json
{
  "type": "n8n-nodes-base.code",
  "typeVersion": 2,
  "name": "Code (expiry sweep)",
  "position": [480, 700],
  "parameters": {
    "jsCode": "const open = $('Data Table (get today open)').all().map(i => i.json);\nreturn open.map(r => ({ json: { ...r, _expire: true } }));"
  }
}
```

Each output item goes to: Data Table update (status=expired) → Google Tasks update (expiry note) → Telegram summary. The Telegram summary should aggregate — don't send 3 separate "expired" messages. Add a final Code node after the loop that builds one summary string from all processed items.

## Built-in variables worth knowing

These work inside `jsCode`:

| Variable | What it gives you |
| --- | --- |
| `$input.all()` | All input items (Run Once for All Items mode) |
| `$json` | Current item's json (Run Once for Each Item mode) |
| `$('Node Name').first().json` | First output item of a named upstream node |
| `$('Node Name').all()` | All output items of a named upstream node |
| `$now` | Current DateTime (Luxon) |
| `$workflow.id` | Current workflow ID |

## Gotcha: Luxon vs n8n expression extensions

Per n8n docs, the Code node runs **native Luxon**, not n8n's expression engine. Methods tagged "Custom n8n functionality" in the docs (e.g., `DateTime.format()`) don't work in Code nodes. Use native Luxon equivalents:
- `DateTime.toFormat('yyyy-MM-dd')` instead of `DateTime.format('yyyy-MM-dd')`
- `DateTime.plus({ days: 1 })` instead of `DateTime.plus(1, 'days')`

If a date method silently returns wrong output, this is the cause.

## Gotcha: item linking

When your Code node outputs a different number of items than it received, n8n can't automatically link output items to input items for the `$('Node').item` syntax. This is fine for our nodes — we always pull via `.first()` or `.all()`, never `.item`. If you add a node that needs item linking, you must include `pairedItem` in each output item.

## Settings tab

- **Retry On Fail**: off. Code nodes fail because of logic bugs, not transient errors. Retry just hides the bug.
- **On Error**: `stop workflow`. You want to see the error in the executions list and fix the code, not silently continue.

## Common issue: `Cannot read property 'x' of undefined`

You referenced a field that doesn't exist on the upstream node's output. Add optional chaining: `$('Node').first().json?.field?.subfield`. Or guard with `const x = $('Node').first().json || {};` then access `x.field`.

## Common issue: `Item lists must be objects`

You returned an array of non-objects. Each output item must be `{ json: {...} }`, not a bare object or array. `return [{ json: { foo: 1 } }]` is correct; `return [{ foo: 1 }]` is not.
