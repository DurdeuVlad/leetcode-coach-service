# Switch and IF nodes

Switch type: `n8n-nodes-base.switch`
IF type: `n8n-nodes-base.if`
Docs:
- Switch: https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.switch
- IF: https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.if

Used for branching. Pick by output count: 2 branches → IF. 3+ branches → Switch.

## Where they're used

| Node | Type | Where | Branches |
| --- | --- | --- | --- |
| Reply has reply_to? | IF | Flow B, after trigger | yes / no |
| Lookup found? | IF | Flow B, after reply_to lookup | found (coach pass) / not found (pick parse) |
| Lesson decision | Switch | Flow B, after coach pass | none / bump / insert / graduate |
| Code node output _skip? | IF | Flow B pick branch, after parse | skip / continue |
| Code node output _ask? | IF | Flow B fuzzy branch, after correlate | ask / proceed |

## IF node — reply has reply_to?

Routes Flow B based on whether the Telegram message is a reply (has `reply_to_message`) or a standalone message (needs fuzzy match).

```json
{
  "type": "n8n-nodes-base.if",
  "typeVersion": 2.2,
  "name": "IF (has reply_to?)",
  "position": [480, 300],
  "parameters": {
    "conditions": {
      "options": { "caseSensitive": true, "leftValue": "", "typeValidation": "strict" },
      "conditions": [
        {
          "id": "uuid-1",
          "leftValue": "={{ $('Telegram Trigger (incoming reply)').first().json.message.reply_to_message }}",
          "rightValue": "",
          "operator": { "type": "object", "operation": "exists" }
        }
      ],
      "combinator": "and"
    },
    "options": {}
  }
}
```

Outputs:
- `main[0]` (true): wire to `Data Table (lookup by message_id)`
- `main[1]` (false): wire to `Data Table (get today open)` for fuzzy match

Field reference:
- `conditions.conditions[0].operator.type: "object"` + `operation: "exists"` — checks the `message.reply_to_message` object is present and not null. **Check the parent, not a nested field.** If you point the expression at `message.reply_to_message.message_id` and `reply_to_message` is undefined (standalone message), n8n's expression engine throws "Cannot read properties of undefined" before the `exists` operator ever runs. Checking `message.reply_to_message` itself returns undefined cleanly, which `exists` then evaluates as false. The downstream Code node is responsible for reading `.message_id` once it knows the parent exists. (The Telegram Trigger wraps the message payload in a `message` field — see `02-telegram-trigger.md`.)
- `combinator: "and"` — only one condition here, but if you add more, AND is what you want (all must hold).

## IF node — lookup found? (Flow B router: coach pass vs pick parse)

Sits after `Data Table (lookup by message_id)`. This is the v3 router that lets Flow B own both
the pick reply and the code reply through a single Telegram Trigger. If the lookup found a
`pending_review` row, the user replied to a per-problem message → coach pass. If not, the user
replied to the 5-list message → pick parse.

```json
{
  "type": "n8n-nodes-base.if",
  "typeVersion": 2.2,
  "name": "IF (lookup found?)",
  "position": [720, 300],
  "parameters": {
    "conditions": {
      "options": { "caseSensitive": true, "typeValidation": "strict" },
      "conditions": [
        {
          "id": "uuid-lookup-found",
          "leftValue": "={{ $('Data Table (lookup by message_id)').first().json.message_id }}",
          "rightValue": "",
          "operator": { "type": "number", "operation": "notEmpty" }
        }
      ],
      "combinator": "and"
    },
    "options": {}
  }
}
```

Outputs:
- `main[0]` (true, lookup found): wire to `Code (correlate reply)` → coach pass path. The Code node reads the matched row from `Data Table (lookup by message_id)` directly — it no longer needs to re-do the exact-match lookup itself.
- `main[1]` (false, lookup miss): wire to `Code (parse selection)` → pick-parse path. The reply was to the 5-list message (whose ID is never stored in `pending_review`), so the user is picking problems.

Field reference:
- `operator.type: "number"` + `operation: "notEmpty"` — checks the `message_id` field on the lookup result is a non-empty number. Data Table `get` with zero matches returns an empty item set, so `$('Data Table (lookup by message_id)').first().json` is `{}` and `.message_id` is undefined → notEmpty evaluates false → pick-parse branch. With a match, `.message_id` is a number → notEmpty true → coach-pass branch.
- Don't use `operation: "exists"` here — that checks object presence, not field value. An empty `{}` exists, which would wrongly route to coach pass. `notEmpty` on a number field is the right check.
- The `status = open` condition on `Data Table (lookup by message_id)` already filters out expired rows. If the row was expired by the sweep, the lookup returns zero rows → pick-parse branch. This is a minor leak: an expired per-problem reply would be misrouted to pick-parse and likely fail the regex (since the user pasted code, not numbers), ending in the `_skip` Telegram message. Acceptable — the expiry sweep is the source of truth and the user gets a clear "no valid picks" message rather than a silent drop.

## Switch node — lesson decision

Routes Flow B based on the Code node's `action` field. Four branches.

```json
{
  "type": "n8n-nodes-base.switch",
  "typeVersion": 3.2,
  "name": "Switch (lesson action)",
  "position": [1380, 300],
  "parameters": {
    "mode": "rules",
    "rules": {
      "values": [
        {
          "conditions": {
            "options": { "caseSensitive": false },
            "conditions": [
              {
                "leftValue": "={{ $('Code (lesson decision)').first().json.action }}",
                "rightValue": "none",
                "operator": { "type": "string", "operation": "equals" }
              }
            ],
            "combinator": "and"
          },
          "renameOutput": true,
          "outputKey": "none"
        },
        {
          "conditions": {
            "options": { "caseSensitive": false },
            "conditions": [
              {
                "leftValue": "={{ $('Code (lesson decision)').first().json.action }}",
                "rightValue": "bump",
                "operator": { "type": "string", "operation": "equals" }
              }
            ],
            "combinator": "and"
          },
          "renameOutput": true,
          "outputKey": "bump"
        },
        {
          "conditions": {
            "options": { "caseSensitive": false },
            "conditions": [
              {
                "leftValue": "={{ $('Code (lesson decision)').first().json.action }}",
                "rightValue": "insert",
                "operator": { "type": "string", "operation": "equals" }
              }
            ],
            "combinator": "and"
          },
          "renameOutput": true,
          "outputKey": "insert"
        },
        {
          "conditions": {
            "options": { "caseSensitive": false },
            "conditions": [
              {
                "leftValue": "={{ $('Code (lesson decision)').first().json.action }}",
                "rightValue": "graduate",
                "operator": { "type": "string", "operation": "equals" }
              }
            ],
            "combinator": "and"
          },
          "renameOutput": true,
          "outputKey": "graduate"
        }
      ]
    },
    "options": {
      "allMatchingOutputs": false,
      "fallbackOutput": "extra"
    }
  }
}
```

Outputs:
- `main[0]` (none): skip lesson persistence, go to leetcode_log insert
- `main[1]` (bump): go to Data Table update times_reinforced
- `main[2]` (insert): go to Data Table insert tutor_lessons
- `main[3]` (graduate): go to Data Table update active=false on the matched lesson row
- `main[4]` (fallback / extra): unexpected action value — wire to a Telegram "lesson decision returned unexpected: <value>" message so you notice the bug

Field reference:
- `mode: "rules"` — UI-driven rules. The alternative `"expression"` lets you return an output index programmatically; use that only if the rules get unwieldy.
- `renameOutput: true` + `outputKey`: names the output "none" / "bump" / "insert" / "graduate" instead of "Output 0" / "Output 1" / "Output 2" / "Output 3". Makes the canvas readable.
- `fallbackOutput: "extra"` — unmatched items go to a separate extra output. Always wire this somewhere visible; an unmatched Switch case is a silent bug.
- `allMatchingOutputs: false` — first match wins. Set to `true` only if you want an item to flow to multiple outputs (you don't, here).
- The graduate branch is new in v3. Without it, lessons that hit the reinforcement threshold would keep getting bumped forever and the student would keep seeing the same coaching hints. The graduation path sets `active=false` on the lesson row, which removes it from Flow A's selection bias and Flow B's coaching reference pool.

## IF node — Code output _skip?

Used after the parse-selection Code node in Flow B's pick branch. If the user replied with no
valid numbers (or pasted code by mistake to the 5-list message), skip the per-problem loop.

```json
{
  "type": "n8n-nodes-base.if",
  "typeVersion": 2.2,
  "name": "IF (skip?)",
  "position": [760, 300],
  "parameters": {
    "conditions": {
      "options": { "caseSensitive": true, "typeValidation": "strict" },
      "conditions": [
        {
          "id": "uuid-skip",
          "leftValue": "={{ $('Code (parse selection)').first().json._skip }}",
          "rightValue": true,
          "operator": { "type": "boolean", "operation": "true" }
        }
      ],
      "combinator": "and"
    }
  }
}
```

- `main[0]` (true, _skip present): wire to a Telegram "no valid picks, skipping today" message. End of flow.
- `main[1]` (false): wire to the per-problem loop.

The operator `boolean / true` checks the value is truthy-true, not just present. This avoids matching `_skip: false` (which the Code node doesn't emit, but defensive).

## Settings tab (both node types)

- **Retry On Fail**: off. These are pure logic; they don't fail transiently.
- **On Error**: `stop workflow`. A Switch/IF failure means the input shape was unexpected — you want to see it, not route around it.

## Common issue: item goes to fallback when it shouldn't

The condition value type doesn't match. Switch's `operator.type: "string"` requires both sides to be strings. If `action` is returned as a non-string by the Code node (e.g., you wrote `return [{ json: { action: 'none' } }]` — that's a string, fine — but `return [{ json: { action: 0 } }]` is a number and won't match `"none"`). Either fix the Code node output or change the operator type to match.

## Common issue: IF passes both branches

You wired both outputs to the same downstream node by accident. IF's `main[0]` is true, `main[1]` is false — they're different outputs. Check the connections in the canvas; if both wires go to the same target, delete one.

## When to use neither

If the branch decision is "is this field empty?" and the downstream behavior is "send a default message vs send the field," you don't need an IF node. Use an expression in the Telegram node's text: `={{ $json.field || 'default text' }}`. Saves a node.
