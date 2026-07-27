# Telegram (send) node

Node type: `n8n-nodes-base.telegram`
Docs: https://docs.n8n.io/integrations/builtin/app-nodes/n8n-nodes-base.telegram

Used multiple times across both flows. Same node type, different operations and chat targets.

## Operations used

| Operation | Where | Purpose |
| --- | --- | --- |
| `sendMessage` | Flow A | Send the 5-candidate list |
| `sendMessage` | Flow B pick branch | Send each per-problem message (loop) |
| `sendMessage` | Flow A expiry | Send "2 problems expired" summary |
| `sendMessage` | Flow B | Ask which problem (when fuzzy match ambiguous) |
| `sendMessage` | Flow B | Send confirmation + coach feedback |
| `sendMessage` | Error workflow | Send "something broke" notification |

## Node config — send 5-candidate list (Flow A)

```json
{
  "type": "n8n-nodes-base.telegram",
  "typeVersion": 1.2,
  "name": "Telegram (send 5-list)",
  "position": [820, 300],
  "parameters": {
    "chatId": "{{YOUR_TELEGRAM_CHAT_ID}}",
    "text": "={{ $json.candidate_list_markdown }}",
    "additionalFields": {
      "parseMode": "MarkdownV2",
      "disableWebPagePreview": true
    }
  },
  "credentials": {
    "telegramApi": {
      "id": "__CREDENTIAL_ID__",
      "name": "Telegram — LeetCode Coach bot"
    }
  }
}
```

Field reference:
- `chatId`: your personal chat ID (same number as in the trigger's `chatIds`). Don't use a username — IDs are stable, usernames can change.
- `text`: an expression that pulls the AI Agent's formatted list. The agent should output a string field `candidate_list_markdown` with lines like `1. *Two Sum* — array, hash map — easy\n2. ...`.
- `additionalFields.parseMode`: `MarkdownV2`. The agent's output must escape reserved chars (`_ * [ ] ( ) ~ \` > # + - = | { } . !`) per Telegram's MarkdownV2 rules. Easier alternative: set `parseMode` to `HTML` and have the agent emit `<b>1. Two Sum</b> — ...`. HTML escaping is more forgiving.
- `additionalFields.disableWebPagePreview`: `true`. LeetCode problem links generate huge previews that wreck the list layout.

## Node config — send per-problem message (Flow B pick branch, inside loop)

```json
{
  "type": "n8n-nodes-base.telegram",
  "typeVersion": 1.2,
  "name": "Telegram (send per-problem)",
  "position": [1100, 300],
  "parameters": {
    "chatId": "{{YOUR_TELEGRAM_CHAT_ID}}",
    "text": "=Problem: {{ $json.title }}\nURL: {{ $json.url }}\nDifficulty: {{ $json.difficulty }}\nTags: {{ $json.tags }}\n\n<i>Coach hint:</i> {{ $json.coaching_hint || 'No active lesson for this one — focus on clean execution.' }}\n\nReply with your code or a status note.",
    "additionalFields": {
      "parseMode": "HTML",
      "disableWebPagePreview": false
    }
  },
  "credentials": {
    "telegramApi": {
      "id": "__CREDENTIAL_ID__",
      "name": "Telegram — LeetCode Coach bot"
    }
  }
}
```

This node runs inside a loop (one item per chosen problem). The output `message_id` is what Flow B uses to correlate replies — capture it:

```
{{ $json.message_id }}
```

Wire this node's output to the Data Table insert for `pending_review`, and pass `message_id` through. See `06-data-table.md`.

## Node config — expiry summary (Flow A expiry sweep end)

Sends one aggregated message listing every problem that expired without a reply. Sits after the
expiry loop (Data Table mark expired + Google Tasks expiry note). A final Code node before this
one should build the summary string from all processed items.

```json
{
  "type": "n8n-nodes-base.telegram",
  "typeVersion": 1.2,
  "name": "Telegram (expiry summary)",
  "position": [1100, 900],
  "parameters": {
    "chatId": "{{YOUR_TELEGRAM_CHAT_ID}}",
    "text": "={{ $json.expiry_summary }}",
    "additionalFields": {
      "parseMode": "HTML",
      "disableWebPagePreview": true
    }
  },
  "credentials": {
    "telegramApi": {
      "id": "__CREDENTIAL_ID__",
      "name": "Telegram — LeetCode Coach bot"
    }
  }
}
```

Field reference:
- `chatId`: hardcoded to your chat ID (same as the 5-list send). The expiry sweep has no Telegram Trigger to pull a chat ID from.
- `text`: expects an upstream Code node to produce `expiry_summary`, e.g. `"2 problems expired without reply:\n• Two Sum\n• Longest Substring"`. Build that in a `Code (build expiry summary)` node after the loop.
- `parseMode: HTML` — same forgiving choice as the confirmation message.

## Node config — confirmation + coach feedback (Flow B end)

```json
{
  "type": "n8n-nodes-base.telegram",
  "typeVersion": 1.2,
  "name": "Telegram (send confirmation)",
  "position": [1800, 300],
  "parameters": {
    "chatId": "={{ $('Telegram Trigger (incoming reply)').first().json.message.chat.id }}",
    "text": "={{ $json.tutor_feedback }}",
    "additionalFields": {
      "parseMode": "HTML",
      "replyToMessageId": "={{ $('Telegram Trigger (incoming reply)').first().json.message.message_id }}"
    }
  },
  "credentials": {
    "telegramApi": {
      "id": "__CREDENTIAL_ID__",
      "name": "Telegram — LeetCode Coach bot"
    }
  }
}
```

Field reference:
- `chatId`: pull from the trigger's output, not hardcoded — if you ever message the bot from a different chat (e.g., a test chat), the reply goes to the right place.
- `additionalFields.replyToMessageId`: makes the confirmation a Telegram reply to your original code message. Visually groups the conversation.
- `text`: starts with `=` so n8n treats the rest as an expression. The tutor feedback should name any lesson saved: "Logged. Saved new lesson: *check empty input before binary search*."

## Settings tab (all Telegram send nodes)

- **Retry On Fail**: on, 3 tries, 2000ms wait. Telegram's API occasionally 429s; retry handles it.
- **On Error**: `continue (using error output)`. Wire the error output to a Code node that logs to a `error_log` data table so a single failed send doesn't kill the whole flow.

## Common issue: `Bad Request: message text is empty`

The expression resolved to an empty string. Usually means the upstream AI Agent returned no `candidate_list_markdown` field (model returned a different shape). Fix the agent's system prompt to enforce the output schema, or add a Code node before the Telegram node that asserts the field is non-empty and falls back to a hardcoded "Agent returned nothing — check execution" message.

## Common issue: `Bad Request: can't parse entities`

You set `parseMode: MarkdownV2` but the text has unescaped `_` or `*`. Two fixes:
1. Switch to `parseMode: HTML` and use `<b>` / `<i>` / `<code>` — much more forgiving.
2. Keep MarkdownV2 and run the agent output through a Code node that escapes the reserved chars with `String.replace`.

Pick HTML. It's not worth the escaping headache.
