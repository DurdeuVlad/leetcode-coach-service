# Data Table node

Node type: `n8n-nodes-base.dataTable`
Docs:
- Node: https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.datatable
- Row ops: https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.datatable/rows
- Table ops: https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.datatable/tables

Source-of-truth for the JSON shapes below: `packages/nodes-base/nodes/DataTable/` in n8n-io/n8n (verified 2026-07-26). The `get`/`update` operations use `matchType` + `filters.conditions[]` with `keyName`/`condition`/`keyValue`; `insert`/`update` use a `columns` resourceMapper. Don't use the older `mustMatch`/`conditions`/`mapping` field names — they don't match the current node.

This is the project's data store. Four tables, all created once via the Data tables tab in n8n UI (or via the node's `table.create` operation — same result).

## Why Data Tables (not Postgres, not Google Sheets)

Per the project README: keep the existing Data Table platform. Data Tables live inside n8n, no external DB to maintain, no Google Sheets rate limits. Trade-off: queries are condition-based row scans, not SQL. Fine for our row counts (hundreds, not millions).

## The four tables — schema to create up front

Create these in the n8n **Data tables tab** before importing the workflows. Column names are exact — the workflow JSON references them by name.

### `leetcode_problems`

| Column | Type | Notes |
| --- | --- | --- |
| `title` | String | LeetCode problem title |
| `slug` | String | URL slug, e.g. `two-sum` |
| `url` | String | Full URL |
| `difficulty` | String | `easy` / `medium` / `hard` |
| `tags` | String | Comma-separated, e.g. `array,hash-map` |
| `solved` | Boolean | Default `false` |
| `last_attempted` | Date | Nullable |
| `times_attempted` | Number | Default `0` |

### `leetcode_log`

| Column | Type | Notes |
| --- | --- | --- |
| `problem_slug` | String | FK to leetcode_problems.slug |
| `date` | Date | When the attempt happened |
| `status` | String | `solved` / `reviewed` / `skipped` / `saw_solution` |
| `time_spent_min` | Number | Nullable |
| `tutor_feedback` | String | Nullable; HTML |
| `lesson_title` | String | Nullable; lesson saved this attempt |

### `pending_review`

| Column | Type | Notes |
| --- | --- | --- |
| `message_id` | Number | Telegram message_id of the per-problem msg — correlation key |
| `google_task_id` | String | Google Task ID — for Flow B update |
| `problem_slug` | String | FK |
| `problem_title` | String | Denormalized for fuzzy match |
| `proposed_at` | Date | When Flow B's pick branch sent the per-problem msg |
| `status` | String | `open` / `done` / `expired` |

### `tutor_lessons`

| Column | Type | Notes |
| --- | --- | --- |
| `title` | String | Short, e.g. `check empty input before binary search` |
| `category` | String | e.g. `binary-search`, `dp`, `graphs` |
| `created_at` | Date | First seen |
| `times_reinforced` | Number | Default `1` |
| `active` | Boolean | Default `true`; set `false` when mastered |

## Operations used

| Operation | Where | Purpose |
| --- | --- | --- |
| `row.get` | Flow A start | Pull recent `leetcode_log` rows for the agent prompt |
| `row.get` | Flow A start | Pull `solved=true` problems |
| `row.get` | Flow A start | Pull `active=true` lessons |
| `row.get` | Flow A expiry | Pull today's open `pending_review` rows |
| `row.insert` | Flow B pick branch | Insert one `pending_review` row per chosen problem |
| `row.get` | Flow B | Look up `pending_review` by `message_id` |
| `row.get` | Flow B | Look up `tutor_lessons` by `title` (for bump-vs-insert decision) |
| `row.update` | Flow B | Mark `pending_review.status = done` (with `status=open` guard) |
| `row.update` | Flow B | Bump `leetcode_problems.solved = true` if solved |
| `row.insert` | Flow B | Insert `leetcode_log` row |
| `row.insert` | Flow B | Insert new `tutor_lessons` row (if new lesson) |
| `row.update` | Flow B | Bump `tutor_lessons.times_reinforced` (if recurring) |
| `row.update` | Flow B | Graduate `tutor_lessons.active = false` (if reinforced 5+ and demonstrated correctly — new in v3) |
| `row.update` | Flow A expiry | Mark `pending_review.status = expired` |

## Field-name reference (read once, applies to every config below)

The Data Table node's parameter shape comes from the source files linked above. The non-obvious fields:

- `dataTableId`: resource-locator object. `{ "__rl": true, "value": "<name or id>", "mode": "name" | "id" }`. `mode: "name"` is more readable; n8n resolves to the ID internally.
- `matchType`: `"anyCondition"` or `"allConditions"`. Replaces the older `mustMatch` field. With zero conditions, `get` returns all rows (limited by `limit`).
- `filters`: a fixedCollection. Inside it, `filters.conditions[]` is the array of condition objects. Each condition: `{ "keyName": "<column>", "condition": "<op>", "keyValue": "<value>" }`. The `keyValue` field is omitted for `isEmpty`/`isNotEmpty`/`isTrue`/`isFalse`.
- Valid `condition` operators: `eq`, `neq`, `like`, `ilike`, `gt`, `gte`, `lt`, `lte`, `isEmpty`, `isNotEmpty`, `isTrue`, `isFalse`. Note `eq` — not `equals`.
- `returnAll`: boolean. `true` = return all matches; `false` = use `limit`.
- `limit`: number, only used when `returnAll: false`. Default 50.
- `orderBy`: boolean toggle. When `true`, also set `orderByColumn` (string) and `orderByDirection` (`"ASC"` or `"DESC"`).
- `columns`: resourceMapper, used by `insert` and `update`. Shape: `{ "mappingMode": "defineBelow" | "autoMapInputData", "value": { "<column>": "<expression or fixed value>" } }`. The older top-level `mappingMode`/`mapping` field names don't match the current node.

## Node config — get recent log rows (Flow A start)

```json
{
  "type": "n8n-nodes-base.dataTable",
  "typeVersion": 1.1,
  "name": "Data Table (get recent log)",
  "position": [240, 460],
  "parameters": {
    "resource": "row",
    "operation": "get",
    "dataTableId": {
      "__rl": true,
      "value": "leetcode_log",
      "mode": "name"
    },
    "matchType": "allConditions",
    "filters": { "conditions": [] },
    "returnAll": false,
    "limit": 30,
    "orderBy": true,
    "orderByColumn": "date",
    "orderByDirection": "DESC"
  }
}
```

Field reference:
- `matchType: "allConditions"` with empty `filters.conditions` returns all rows (limited by `limit`). `allConditions` vs `anyCondition` only matters once you add conditions.
- `orderBy: true` + `orderByColumn: "date"` + `orderByDirection: "DESC"` — most recent first. The agent sees your latest attempts at the top of the prompt.
- `returnAll: false` + `limit: 30` — cap the prompt size. 30 rows is enough history for the agent to bias selection.

## Node config — get solved problems (Flow A start)

```json
{
  "type": "n8n-nodes-base.dataTable",
  "typeVersion": 1.1,
  "name": "Data Table (get solved)",
  "position": [240, 580],
  "parameters": {
    "resource": "row",
    "operation": "get",
    "dataTableId": {
      "__rl": true,
      "value": "leetcode_problems",
      "mode": "name"
    },
    "matchType": "allConditions",
    "filters": {
      "conditions": [
        { "keyName": "solved", "condition": "isTrue" }
      ]
    },
    "returnAll": true
  }
}
```

Note: `isTrue` is the boolean-equality operator. Don't pass `"true"` as a string `keyValue` — booleans use the dedicated operators.

## Node config — get active lessons (Flow A start, also reused in Flow B)

```json
{
  "type": "n8n-nodes-base.dataTable",
  "typeVersion": 1.1,
  "name": "Data Table (get active lessons)",
  "position": [240, 700],
  "parameters": {
    "resource": "row",
    "operation": "get",
    "dataTableId": {
      "__rl": true,
      "value": "tutor_lessons",
      "mode": "name"
    },
    "matchType": "allConditions",
    "filters": {
      "conditions": [
        { "keyName": "active", "condition": "isTrue" }
      ]
    },
    "returnAll": true
  }
}
```

This node is referenced by name from both Flow A's agent prompt (bias selection toward active lessons) and Flow B's `Code (lesson decision)` (decide bump-vs-insert). Same node, two consumers — don't rename it.

## Node config — get today's open pending_review (Flow A expiry + Flow B fuzzy match)

```json
{
  "type": "n8n-nodes-base.dataTable",
  "typeVersion": 1.1,
  "name": "Data Table (get today open)",
  "position": [320, 700],
  "parameters": {
    "resource": "row",
    "operation": "get",
    "dataTableId": {
      "__rl": true,
      "value": "pending_review",
      "mode": "name"
    },
    "matchType": "allConditions",
    "filters": {
      "conditions": [
        { "keyName": "status", "condition": "eq", "keyValue": "open" },
        { "keyName": "proposed_at", "condition": "gte", "keyValue": "={{ $today.toISO() }}" }
      ]
    },
    "returnAll": true
  }
}
```

The `status = open` + `proposed_at >= today` filter is the same scope the expiry sweep and the fuzzy-match fallback both need. `$today` is n8n's midnight-today Luxon DateTime — `gte` against it gives "anything proposed today." If you backfill `pending_review` with rows from prior days, this filter excludes them, which is what you want.

## Node config — insert pending_review row (Flow B pick branch, in the per-problem loop)

```json
{
  "type": "n8n-nodes-base.dataTable",
  "typeVersion": 1.1,
  "name": "Data Table (insert pending_review)",
  "position": [1340, 300],
  "parameters": {
    "resource": "row",
    "operation": "insert",
    "dataTableId": {
      "__rl": true,
      "value": "pending_review",
      "mode": "name"
    },
    "columns": {
      "mappingMode": "defineBelow",
      "value": {
        "message_id": "={{ $('Telegram (send per-problem)').first().json.message_id }}",
        "google_task_id": "={{ $('Google Tasks (create per-problem)').first().json.id }}",
        "problem_slug": "={{ $json.slug }}",
        "problem_title": "={{ $json.title }}",
        "proposed_at": "={{ $now.toISO() }}",
        "status": "open"
      }
    }
  }
}
```

Field reference:
- `columns.mappingMode: "defineBelow"` — explicit field mapping. The alternative `"autoMapInputData"` requires incoming field names to exactly match column names; ours don't (the loop item has `slug`, the column is `problem_slug`).
- `columns.value`: each column mapped to an expression. `message_id` and `google_task_id` come from the upstream Telegram and Google Tasks nodes — this is why those nodes must run before this insert.

## Node config — look up pending_review by message_id (Flow B)

```json
{
  "type": "n8n-nodes-base.dataTable",
  "typeVersion": 1.1,
  "name": "Data Table (lookup by message_id)",
  "position": [560, 300],
  "parameters": {
    "resource": "row",
    "operation": "get",
    "dataTableId": {
      "__rl": true,
      "value": "pending_review",
      "mode": "name"
    },
    "matchType": "allConditions",
    "filters": {
      "conditions": [
        {
          "keyName": "message_id",
          "condition": "eq",
          "keyValue": "={{ $('Telegram Trigger (incoming reply)').first().json.message.reply_to_message.message_id }}"
        },
        {
          "keyName": "status",
          "condition": "eq",
          "keyValue": "open"
        }
      ]
    },
    "returnAll": false,
    "limit": 1
  }
}
```

The `status = open` condition is the race-condition guard from `00-connections-and-general.md`. If the expiry sweep already marked it `expired`, this returns zero rows and the Code node downstream skips the Google Task update.

## Node config — look up tutor_lessons by title (Flow B, before lesson decision)

```json
{
  "type": "n8n-nodes-base.dataTable",
  "typeVersion": 1.1,
  "name": "Data Table (lookup lesson)",
  "position": [1180, 460],
  "parameters": {
    "resource": "row",
    "operation": "get",
    "dataTableId": {
      "__rl": true,
      "value": "tutor_lessons",
      "mode": "name"
    },
    "matchType": "allConditions",
    "filters": {
      "conditions": [
        {
          "keyName": "title",
          "condition": "ilike",
          "keyValue": "={{ $('AI Agent (coach pass)').first().json.lesson_title }}"
        },
        { "keyName": "active", "condition": "isTrue" }
      ]
    },
    "returnAll": false,
    "limit": 1
  }
}
```

`ilike` (case-insensitive like) is the right operator here — the agent's `lesson_title` won't always match the stored title character-for-character. If your Data Tables build doesn't expose `ilike`, fall back to `eq` and rely on the Code node's substring check instead.

## Node config — update pending_review status (Flow B end, with race guard)

```json
{
  "type": "n8n-nodes-base.dataTable",
  "typeVersion": 1.1,
  "name": "Data Table (mark pending done)",
  "position": [1700, 300],
  "parameters": {
    "resource": "row",
    "operation": "update",
    "dataTableId": {
      "__rl": true,
      "value": "pending_review",
      "mode": "name"
    },
    "matchType": "allConditions",
    "filters": {
      "conditions": [
        {
          "keyName": "message_id",
          "condition": "eq",
          "keyValue": "={{ $('Code (correlate reply)').first().json.message_id }}"
        },
        { "keyName": "status", "condition": "eq", "keyValue": "open" }
      ]
    },
    "columns": {
      "mappingMode": "defineBelow",
      "value": {
        "status": "done"
      }
    }
  }
}
```

The `status = open` condition here is the second half of the race-condition guard. Without it, if the expiry sweep marked the row `expired` between the Flow B lookup and this update, this update would overwrite `expired` back to `done` — silently resurrecting a row the user already abandoned. With the guard, the update matches zero rows and the row stays `expired`. Pair this with the "update writes zero rows" check in the Common Issues section below.

## Node config — bump tutor_lessons.times_reinforced (Flow B, recurring lesson)

```json
{
  "type": "n8n-nodes-base.dataTable",
  "typeVersion": 1.1,
  "name": "Data Table (bump lesson)",
  "position": [1500, 560],
  "parameters": {
    "resource": "row",
    "operation": "update",
    "dataTableId": {
      "__rl": true,
      "value": "tutor_lessons",
      "mode": "name"
    },
    "matchType": "allConditions",
    "filters": {
      "conditions": [
        {
          "keyName": "title",
          "condition": "eq",
          "keyValue": "={{ $('AI Agent (coach pass)').first().json.lesson_title }}"
        }
      ]
    },
    "columns": {
      "mappingMode": "defineBelow",
      "value": {
        "times_reinforced": "={{ $('Data Table (lookup lesson)').first().json.times_reinforced + 1 }}"
      }
    }
  }
}
```

The mapping expression pulls the current value and adds 1. Requires a preceding `get` node (`Data Table (lookup lesson)`) to read the current `times_reinforced`. Data Table `update` doesn't support atomic increment — you must read-then-write. For our scale this is fine; for high concurrency it would race.

## Node config — graduate tutor_lessons (Flow B, graduate branch — new in v3)

Target of the `graduate` output on `Switch (lesson action)`. Sets `active=false` on the matched lesson row, removing it from Flow A's selection bias and Flow B's coaching reference pool.

```json
{
  "type": "n8n-nodes-base.dataTable",
  "typeVersion": 1.1,
  "name": "Data Table (graduate lesson)",
  "position": [1500, 660],
  "parameters": {
    "resource": "row",
    "operation": "update",
    "dataTableId": {
      "__rl": true,
      "value": "tutor_lessons",
      "mode": "name"
    },
    "matchType": "allConditions",
    "filters": {
      "conditions": [
        {
          "keyName": "title",
          "condition": "eq",
          "keyValue": "={{ $('Code (lesson decision)').first().json.title }}"
        },
        { "keyName": "active", "condition": "isTrue" }
      ]
    },
    "columns": {
      "mappingMode": "defineBelow",
      "value": {
        "active": false
      }
    }
  }
}
```

Field reference:
- `filters.conditions` matches by `title` (carried through from `Code (lesson decision)` output) **and** `active=true`. The `active=true` guard is the second half of the double-gate from `08-code.md`: even if a second reply races and bumps the same lesson between the Code node's check and this update, this update will only retire an still-active lesson. If the row was already retired by a parallel run, this matches zero rows — which is the correct outcome.
- `columns.value.active: false` — boolean literal, not the string `"false"`. Booleans use the dedicated literal; n8n will coerce `"false"` to truthy if you pass a string.
- Don't also bump `times_reinforced` here — graduation is a state change, not a reinforcement event. The final `times_reinforced` value stays at whatever it was when the graduation threshold was crossed (5+), which is correct for any future "did I master this?" audit.

Wire this node's output to the same `leetcode_log` insert that the other three branches reach — graduation doesn't skip logging the attempt.

## Node config — insert new tutor_lessons row (Flow B, insert branch)

Target of the `insert` output on `Switch (lesson action)`. Files a brand-new lesson under `tutor_lessons` with `active=true` and `times_reinforced=1` (first sighting counts as reinforcement 1, not 0 — the lesson surfaced from a real attempt).

```json
{
  "type": "n8n-nodes-base.dataTable",
  "typeVersion": 1.1,
  "name": "Data Table (insert lesson)",
  "position": [1500, 460],
  "parameters": {
    "resource": "row",
    "operation": "insert",
    "dataTableId": {
      "__rl": true,
      "value": "tutor_lessons",
      "mode": "name"
    },
    "columns": {
      "mappingMode": "defineBelow",
      "value": {
        "title": "={{ $('Code (lesson decision)').first().json.title }}",
        "category": "={{ $('Code (lesson decision)').first().json.category }}",
        "created_at": "={{ $now.toISO() }}",
        "times_reinforced": 1,
        "active": true
      }
    }
  }
}
```

Field reference:
- `title` and `category` come from `Code (lesson decision)` output, which in turn carried them from the coach agent's `lesson_title` and `lesson_category` fields. The Code node is the source of truth — never read directly from `$('AI Agent (coach pass)')` here, because the Code node may have applied the `'general'` fallback for a missing `lesson_category`.
- `times_reinforced: 1` — number literal. Don't use `0`; a lesson that just surfaced has already been demonstrated once (incorrectly, which is why it became a lesson).
- `active: true` — boolean literal. Same caveat as the graduate node: don't pass `"true"` as a string.
- `created_at`: ISO timestamp from `$now`. Don't use `$today` here — `$today` is midnight today, which would lose the time-of-day signal that's useful for "when did this lesson first appear?" queries.

## Node config — insert leetcode_log row (Flow B end, all branches converge here)

Every Flow B run logs an attempt row, regardless of lesson decision (none / bump / insert / graduate). Wire all four Switch outputs (and the fallback, after a Telegram warning) into this node.

```json
{
  "type": "n8n-nodes-base.dataTable",
  "typeVersion": 1.1,
  "name": "Data Table (insert log row)",
  "position": [1900, 300],
  "parameters": {
    "resource": "row",
    "operation": "insert",
    "dataTableId": {
      "__rl": true,
      "value": "leetcode_log",
      "mode": "name"
    },
    "columns": {
      "mappingMode": "defineBelow",
      "value": {
        "problem_slug": "={{ $('Code (correlate reply)').first().json.problem_slug }}",
        "date": "={{ $now.toISO() }}",
        "status": "={{ $('AI Agent (coach pass)').first().json.status }}",
        "time_spent_min": "",
        "tutor_feedback": "={{ $('AI Agent (coach pass)').first().json.tutor_feedback }}",
        "lesson_title": "={{ $('AI Agent (coach pass)').first().json.lesson_title || '' }}"
      }
    }
  }
}
```

Field reference:
- `problem_slug`: pulled from the correlated `pending_review` row via `Code (correlate reply)`, not from the coach agent — the agent never sees the slug, only the title and URL. The Code node is the authority on which problem this attempt was for.
- `status`: one of `solved` / `reviewed` / `skipped` / `saw_solution`, as emitted by the coach. The column is a free string — the constraint is enforced by the agent's prompt, not by the table schema. If you want schema-level validation, switch the column to an enum-like setup (Data Tables doesn't natively support enums; use a separate `status_enum` table and FK if it matters).
- `time_spent_min`: empty string for now — the bot doesn't time the user. If you add a `/start <slug>` command later, populate this from the elapsed time between `/start` and the reply. Leaving the column nullable in the schema (it is — see the schema section above) means an empty string is fine; the column just stays empty.
- `tutor_feedback`: full HTML-formatted feedback, same string that went to Telegram. Storing it lets you query "show me all my reviews on binary-search problems" later without re-running the agent.
- `lesson_title`: empty string if no lesson surfaced (`action: none`). Don't omit the field — Data Tables `insert` with `mappingMode: "defineBelow"` writes NULL for unmapped columns, and `NULL` vs `''` behaves differently in `ilike` filters (NULL never matches).

## Node config — bump leetcode_problems.solved (Flow B, if solved)

Runs only when the coach set `solved: true`. Wire this through an IF node on `$('AI Agent (coach pass)').first().json.solved === true` between the log insert and this update. Skipping the IF and always running the update would set `solved=false` on problems the user got wrong — destructive.

```json
{
  "type": "n8n-nodes-base.dataTable",
  "typeVersion": 1.1,
  "name": "Data Table (mark problem solved)",
  "position": [2050, 300],
  "parameters": {
    "resource": "row",
    "operation": "update",
    "dataTableId": {
      "__rl": true,
      "value": "leetcode_problems",
      "mode": "name"
    },
    "matchType": "allConditions",
    "filters": {
      "conditions": [
        {
          "keyName": "slug",
          "condition": "eq",
          "keyValue": "={{ $('Code (correlate reply)').first().json.problem_slug }}"
        },
        { "keyName": "solved", "condition": "isFalse" }
      ]
    },
    "columns": {
      "mappingMode": "defineBelow",
      "value": {
        "solved": true,
        "last_attempted": "={{ $now.toISO() }}"
      }
    }
  }
}
```

Field reference:
- `solved: isFalse` in the filter is the idempotency guard: if the problem was already marked solved (re-attempt for spaced repetition), this update matches zero rows and doesn't waste a write. Without the guard, you'd re-write the same `solved=true` and bump `last_attempted` — harmless but noisy in audit logs.
- `last_attempted`: updated on every solved attempt, not just the first. This is the field Flow A's "bias toward spaced repetition" reads (via a separate `get` node not shown here — add one if you want the agent to see when you last solved each problem).
- Don't bump `times_attempted` here — that belongs in a separate update that runs on **every** attempt, not just solved ones. If you want per-problem attempt counts, add a second update node before this one with `times_attempted: ={{ $('Data Table (lookup problem by slug)').first().json.times_attempted + 1 }}` (and add the corresponding `get` node upstream).

## Node config — mark pending_review expired (Flow A expiry sweep)

Target of `Code (expiry sweep)`'s per-item output. Marks each open row `expired` so Flow B's race-guard sees `status != open` and skips it.

```json
{
  "type": "n8n-nodes-base.dataTable",
  "typeVersion": 1.1,
  "name": "Data Table (mark expired)",
  "position": [640, 700],
  "parameters": {
    "resource": "row",
    "operation": "update",
    "dataTableId": {
      "__rl": true,
      "value": "pending_review",
      "mode": "name"
    },
    "matchType": "allConditions",
    "filters": {
      "conditions": [
        {
          "keyName": "message_id",
          "condition": "eq",
          "keyValue": "={{ $json.message_id }}"
        },
        { "keyName": "status", "condition": "eq", "keyValue": "open" }
      ]
    },
    "columns": {
      "mappingMode": "defineBelow",
      "value": {
        "status": "expired"
      }
    }
  }
}
```

Field reference:
- `$json.message_id` — the current item from `Code (expiry sweep)`, which iterated over `Data Table (get today open)`'s output. Each loop iteration sees one row's `message_id`.
- `status = open` guard: same race-condition pattern as Flow B's `mark pending done`. If the user replied between the sweep's read and this write, Flow B already set `status = done` — this update matches zero rows and the row stays `done`, which is correct. The user's reply wins over the sweep.
- This node runs inside the expiry loop — one update per row. Data Tables doesn't support bulk update in one call, so a 5-row sweep is 5 sequential updates. Fine for our scale; for hundreds of rows you'd want a different pattern (e.g., a Code node that builds a single SQL statement against an external DB).

## Settings tab (all Data Table nodes)

- **Retry On Fail**: on, 2 tries, 1000ms wait. Data Tables is in-process, failures are rare, but retry covers transient n8n hiccups.
- **On Error**: `continue (using error output)` for write nodes; `stop workflow` for read nodes at the start of Flow A (if you can't read the log, the agent can't propose).

## Common issue: `Column not found`

You referenced a column that doesn't exist in the table. Check the table schema in the Data tables tab — column names are case-sensitive and must match exactly. If you renamed a column in the UI, every node referencing the old name breaks. The Data Table node also throws this if you switch `dataTableId` to a different table without updating the `filters.conditions[].keyName` and `columns.value` fields — the schema validation runs against the *current* table, not the one the node was originally configured for.

## Common issue: `update` writes zero rows

The `filters` block didn't match any row. The node succeeds silently (no error) but updates nothing. To catch this, after any `update` node, add a Code node that checks the output row count (`$('Data Table (mark pending done)').all().length` — if zero, the update matched nothing) and Telegram-notifies if zero. Otherwise Flow B reports "done" to you while the row is still `open` (or, with the race guard in place, still `expired` — which is correct, but you should still surface it so you know the user's reply landed after the expiry sweep).

## Common issue: condition matches nothing because of `eq` vs type

`condition: "eq"` against a Number column requires a numeric `keyValue`. If you pass `"={{ ... }}"` and the expression resolves to a string, the comparison silently fails. For booleans use `isTrue`/`isFalse`, not `eq: "true"`. For dates use `gt`/`gte`/`lt`/`lte` with ISO strings.

## Bulk insert option

For the weekly LeetCode refresh (Flow W), use `row.insert` with `options.optimizeBulk: true` — n8n skips returning the inserted rows, ~5x faster. You don't need the rows back; you just inserted them. Note: `optimizeBulk` is under `options`, not `additionalFields`, and it can't be used with expression-driven `dataTableId` (per the source — bulk insert falls back to single-row mode if `dataTableId` is an expression).
