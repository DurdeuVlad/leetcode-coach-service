# #019 — Telegram webhook route

**Milestone:** M3 flow-b · **Labels:** `type:feature` `area:flow-b` `prio:P0`
**Depends on:** #005, #009

## Summary
The single inbound HTTP surface: `POST /telegram/webhook` that parses the
update and dispatches to `flow_b.handle_update`.

## Context
- `docs/architecture.md` §4: the Telegram webhook is the only inbound HTTP
  surface. `docs/business-requirements.md` FR-2.1: one webhook, routing is
  **data-driven, not text-driven**.
- NFR-4: allowlist — only `TELEGRAM_CHAT_ID` may drive the bot.
- Normal FastAPI error handling closes the n8n "missing Telegram trigger
  onError" gap for free.

## Tasks
- [ ] `webhooks/telegram.py` — `POST /telegram/webhook`:
  - Parse the Telegram `Update` (python-telegram-bot typed object).
  - **Allowlist check:** ignore/return 200 for any chat id ≠ configured id.
  - Dispatch to `flow_b.handle_update(update)` (implemented across #021–#026).
- [ ] Return 200 quickly; do heavy work without blocking the response longer
      than necessary (still single-process, but avoid Telegram retries).
- [ ] Errors escaping the handler go through the global handler → alert (#008).

## Acceptance criteria
- [ ] A well-formed update from the allowlisted chat reaches
      `handle_update` (tested with a stubbed handler).
- [ ] An update from a non-allowlisted chat is ignored (no handler call,
      200 returned).
- [ ] Malformed payloads return a clean 4xx/200 without crashing the app.

## Principles (how they apply here)
See [#034](034-engineering-principles-layers.md). KISS is the tie-breaker.
- **Single Responsibility / layer:** the route parses the update, enforces the
  allowlist, and dispatches to `flow_b`. Zero coach/pick logic here.
- **KISS:** a thin FastAPI route; normal framework error handling closes the
  n8n "no trigger onError" gap for free — no custom middleware.
- **Security (least privilege):** non-allowlisted chats are dropped before any
  work happens (NFR-4).
- **Fail loud:** escaped errors go through the global handler → alert (#008).

## External API reference (read before implementing)

**Primary source:** Telegram Bot API — `Update` object
https://core.telegram.org/bots/api#update
**Webhook guide:** https://core.telegram.org/bots/webhooks
**Python SDK:** `python-telegram-bot` v21+ `Update` class —
https://docs.python-telegram-bot.org/en/stable/telegram.update.html

### Inbound payload shape (the `Update` object)

Telegram POSTs a JSON-serialized `Update` to our webhook URL. The fields
we care about for Flow B:

- `update_id` (Integer) — monotonic; dedupe with it (Telegram retries on
  non-2xx, so we may see the same `update_id` twice).
- `message` (optional `Message`) — a text/command message. Sub-fields:
  - `message.message_id` (Integer) — needed by #009 `send_reply`'s
    `reply_to_message_id` parameter.
  - `message.chat.id` (Integer) — **the allowlist check field** (compare
    against `TELEGRAM_CHAT_ID`).
  - `message.text` (String) — the user's input (e.g. "1", "skip",
    "/start").
  - `message.from.id` (Integer) — the user id (same person always for
    single-user; redundant with chat id for 1:1 chats but useful for
    sanity-check logging).
- `callback_query` (optional `CallbackQuery`) — fired when the user taps
  an inline keyboard button (the 5-problem pick UI from Flow B).
  - `callback_query.id` (String) — needed to call `answerCallbackQuery`
    (acknowledge the tap; otherwise the button stays "spinning" in the
    user's UI for ~10 sec).
  - `callback_query.message.message_id` — the message holding the
    keyboard; use this if Flow B edits it to remove buttons after pick.
  - `callback_query.data` (String) — the payload we set on the button
    (e.g. `"pick:3"` for "candidate index 3"). Flow B parses this.
  - `callback_query.from.id` — the tapping user.

Full field reference: https://core.telegram.org/bots/api#update

### Webhook security (the `secret_token` header)

- `setWebhook` (#009) lets us pass a `secret_token` (1-256 chars,
  `[A-Za-z0-9_-]`). Telegram sends it back in the
  `X-Telegram-Bot-Api-Secret-Token` header on **every** webhook POST.
- **Verify it on every inbound request.** If missing or mismatched →
  return 200 (don't leak that we noticed) and drop. This blocks spoofed
  POSTs from anyone who doesn't know the token.
- Configure as `TELEGRAM_WEBHOOK_SECRET` env var; #009 uses the same
  value when calling `setWebhook`.

### Response contract (Telegram's expectations)

- **Return 200 within ~60 seconds.** Telegram's webhook timeout is
  documented as "a reasonable amount of attempts" — in practice if we
  don't 200 quickly, Telegram retries, causing duplicate processing.
- Heavy work (LLM call in Flow B coach, #023) can take 10-30 sec. Two
  options:
  1. **Block and 200 after** (simplest, fine if coach stays under ~50 sec
     — KISS, start here).
  2. **200 immediately, do work in a background task** (`asyncio.create_task`
     or APScheduler's async executor) — only if we observe Telegram
     retries in production logs.
- On any exception escaping the handler: still return 200 (so Telegram
  doesn't retry and re-trigger) **but** route through the global error
  handler → `send_alert` (#008). The alert is the operator's signal that
  Flow B is broken; the 200 prevents duplicate processing.

### Allowlist enforcement (NFR-4)

- Compare `update.message.chat.id` OR `update.callback_query.message.chat.id`
  against `int(TELEGRAM_CHAT_ID)`.
- Non-match → return 200, log `info`-level "ignored non-allowlisted chat
  id={...}", do **not** dispatch to `flow_b.handle_update`.
- Never echo the rejected chat id back to Telegram (no `send_message` to
  it) — silent drop only.

### Malformed payload handling

- If the JSON doesn't parse or doesn't conform to `Update`'s required
  fields → return 200 (not 4xx — 4xx triggers Telegram retries) and log
  `warning`-level with the raw payload truncated to 500 chars. Do **not**
  alert on every malformed payload (could be probe traffic) — alert only
  if the rate exceeds a threshold (Phase 7 calibration; for now, just
  log).

### Open questions to resolve during implementation
- [ ] Use `python-telegram-bot`'s `Update.de_json(data, bot)` to parse,
      or `pydantic` models generated from the Bot API spec? PTB's parser
      is the path of least resistance — use it.
- [ ] Block-and-200 vs background-task: start with block-and-200 (KISS);
      revisit only if Telegram retries appear in logs.
- [ ] Should we dedupe by `update_id` (cache the last N seen)? Telegram
      retries are rare in practice; start without dedupe, add a small
      LRU only if duplicates appear.

## Notes
- `handle_update` is fleshed out by later M3 issues; this issue may land with a
  thin dispatcher.
