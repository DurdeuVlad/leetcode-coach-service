# Telegram Trigger node

Node type: `n8n-nodes-base.telegramTrigger`
Docs: https://docs.n8n.io/integrations/builtin/trigger-nodes/n8n-nodes-base.telegramtrigger

Used once, in Flow B. Flow B owns the only Telegram Trigger in the system and routes every
incoming reply — both the "2 5" pick reply (to the 5-list message Flow A sent) and the code/status
reply (to a per-problem message). Routing is data-driven, not text-driven: the first node after
the trigger looks up `message.reply_to_message.message_id` in `pending_review`. Found → coach pass path.
Not found → pick-parse path (the 5-list message ID is never stored in `pending_review`, so a miss
there means "this is a reply to the list, parse it as picks"). See `08-code.md` and
`09-switch-if.md` for the routing Code and IF nodes.

## Node config

```json
{
  "type": "n8n-nodes-base.telegramTrigger",
  "typeVersion": 1.1,
  "name": "Telegram Trigger (incoming reply)",
  "position": [240, 300],
  "parameters": {
    "updates": [
      "message"
    ],
    "additionalFields": {
      "download": false,
      "chatIds": "{{YOUR_TELEGRAM_CHAT_ID}}"
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
- `updates`: array of update types to fire on. `["message"]` covers text, photo, sticker, etc. Don't add `edited_message`, `channel_post`, etc. — you don't want edits to re-trigger the tutor.
- `additionalFields.chatIds`: **set this.** Without it, anyone who finds the bot can trigger your workflow. Per the n8n Telegram Trigger docs, this is a comma-separated string of numeric chat IDs (e.g. `"123456789"` for one user, `"123,456"` for two). Don't paste a username — Telegram chat IDs are numbers. Get yours by messaging `@userinfobot` or by sending any message to your bot and reading `message.chat.id` from the trigger output (the Telegram update object wraps the message in a `message` field — see the output shape below). The field name in older n8n versions was `restrictToChatIds`; current builds use `chatIds`. If you're on an older build and `chatIds` doesn't appear in the node UI, fall back to `restrictToChatIds` with the same value.
- `additionalFields.download`: `false`. You're not handling images in v2. If you later want photo evidence of a solution, flip to `true` and pick an `imageSize`.

## What the output looks like

For a text message "2 5" sent as a reply, the trigger outputs one item. The Telegram update
object wraps the message payload in a `message` field — every downstream expression that reads
the trigger must go through `.json.message.*`, not `.json.*` directly:

```json
{
  "json": {
    "update_id": 123456789,
    "message": {
      "message_id": 4221,
      "from": { "id": 123456789, "first_name": "..." },
      "chat": { "id": 123456789, "type": "private" },
      "date": 1785027900,
      "text": "2 5",
      "reply_to_message": {
        "message_id": 4218,
        "text": "Today's 5 candidates:\n1. ...",
        "date": 1785018900
      }
    }
  }
}
```

The two fields Flow B cares about (both nested under `message`):
- `message.message_id` — the new message's ID. Not used for correlation; pass it through to the pending_review insert so the confirmation reply can target it. In expressions: `$('Telegram Trigger (incoming reply)').first().json.message.message_id`.
- `message.reply_to_message.message_id` — the ID of the message the user replied to. **This is the correlation key.** When Flow B's pick branch sent the per-problem message, it stored that `message_id` in `pending_review.message_id`. Now the coach branch looks up the row where `pending_review.message_id = message.reply_to_message.message_id`. If the lookup misses, the reply was to the 5-list message (whose ID was never stored in `pending_review`) → route to pick-parse. In expressions: `$('Telegram Trigger (incoming reply)').first().json.message.reply_to_message.message_id`.

Other fields Flow B reads via the same `.message.` prefix: `message.chat.id` (Telegram send
`chatId`), `message.text` (the user's submission text passed to the coach agent), and
`message.reply_to_message` itself (the IF `has reply_to?` checks this object's existence).

If `message.reply_to_message` is absent (user sent a standalone message, not a reply), the Code
node downstream must fuzzy-match against today's open pending_review rows. See `08-code.md`.

## Settings tab

- **Retry On Fail**: off. If Telegram's webhook call fails, Telegram retries per its own backoff. Adding n8n-level retry on top double-fires.
- **On Error**: `Stop Workflow`. A Telegram trigger failure usually means the bot token rotated or the webhook URL changed — retrying won't help and you want the Error Trigger workflow to notify you.

## Webhook setup — n8n handles this for you

When you activate the workflow, n8n calls Telegram's `setWebhook` automatically using `WEBHOOK_URL` from the n8n env. You don't manually run `setWebhook`. If the trigger silently stops, check:
1. `WEBHOOK_URL` env var is set and reachable from the public internet (homelab needs a tunnel or reverse proxy).
2. The bot token in the credential is still valid (message `@BotFather`, `/revoke` if unsure).
3. The workflow is Active.

## Common issue: trigger stuck listening in test mode

When you click "Execute node" to test the trigger, n8n holds the connection open waiting for a message. If you close the tab without stopping, the test execution can stay stuck. Fix: close and reopen the workflow. Don't use "Execute node" on this trigger for normal testing — use "Listen for event" then send a real message from Telegram.
