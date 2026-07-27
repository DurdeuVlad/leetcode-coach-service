# #009 — Telegram client

**Milestone:** M1 integrations · **Labels:** `type:feature` `area:integrations` `prio:P0`
**Depends on:** #002, #008

## Summary
A thin Telegram client for setting the webhook and sending messages/replies,
with retries on transient failures.

## Context
- `docs/architecture.md` §2: `python-telegram-bot` v21+; §3 lists
  `integrations/telegram.py` with `set_webhook`, `send_message`, `send_reply`.
- NFR-1 layer 1: retry on 5xx/timeout with `tenacity` (max 2-3, short backoff).
- NFR-4: only `TELEGRAM_CHAT_ID` is a valid target/allowlist.

## Tasks
- [ ] `integrations/telegram.py`:
  - `set_webhook(url)` — calls `setWebhook` on startup (used by #030).
  - `send_message(text, *, reply_markup=None) -> message_id`.
  - `send_reply(text, reply_to_message_id) -> message_id`.
- [ ] All sends return the Telegram `message_id` (Flow B needs it, FR-2.4).
- [ ] `tenacity` retry on `httpx` timeout / 5xx / 429 only.
- [ ] Structured logging per call (chars sent, message_id, duration).

## Acceptance criteria
- [ ] `send_message` / `send_reply` return the integer `message_id`.
- [ ] Retries fire on 500 and stop after the configured attempts (tested with
      `respx` in #014).
- [ ] Non-allowlisted chat is never targeted (target is always the configured
      chat id).

## Principles (how they apply here)
See [#034](034-engineering-principles-layers.md). KISS is the tie-breaker.
- **Single Responsibility:** this client only knows *how* to talk to Telegram
  (HTTP + retries). It never decides *when* to send — flows do.
- **Interface Segregation:** exposes exactly `set_webhook`, `send_message`,
  `send_reply`; flows depend on those narrow methods, not a god-object.
- **Open/Closed:** adding a new outbound channel later means a new client, not
  edits to flow logic.
- **KISS:** retries only on transient failures via `tenacity`; no custom
  backoff engine.

## External API reference (read before implementing)

**Primary source:** Telegram Bot API — https://core.telegram.org/bots/api
**Webhook guide:** https://core.telegram.org/bots/webhooks
**Python SDK:** `python-telegram-bot` v21+ — https://docs.python-telegram-bot.org/

### Methods to call (exact names + response shapes)

- **`setWebhook(url, *, allowed_updates, secret_token)`** → returns `True`
  on success.
  - Doc: https://core.telegram.org/bots/api#setwebhook
  - `secret_token` (1-256 chars, `[A-Za-z0-9_-]`) is sent back to us in the
    `X-Telegram-Bot-Api-Secret-Token` header on every webhook POST — verify
    it on inbound (#019) to confirm the request is from Telegram, not a
    spoofed POST. Configure it as `TELEGRAM_WEBHOOK_SECRET` env var.
  - `allowed_updates`: explicitly pass `["message", "callback_query"]` — we
    do not need `chat_member`, `message_reaction`, etc. (default excludes
    them, but be explicit).
  - Pass an empty `url=""` to clear the webhook (used in local dev / on
    shutdown if needed).

- **`sendMessage(chat_id, text, *, reply_to_message_id, reply_markup)`**
  → returns a `Message` object.
  - Doc: https://core.telegram.org/bots/api#sendmessage
  - The integer we return to callers is `Message.message_id` (unique inside
    this chat; FR-2.4 needs it for Flow B).
  - `reply_markup` accepts `InlineKeyboardMarkup` (for the 5-button pick
    UI in Flow B) — pass through as-is from the flow.

- **`reply_to_message_id`** is the inbound `Message.message_id` we are
  replying to. Not `update_id`. Verify the field path in #019.

### Error / retry surface

The Bot API returns JSON `{"ok": false, "error_code": N, "description": ...}`
with HTTP 4xx/5xx. `python-telegram-bot` v21 raises `TelegramError`
subclasses — retry only on:
- **HTTP 429** `RetryAfter` (rate-limited; respect `parameters.retry_after`
  seconds — but tenacity's fixed backoff is fine for our volume).
- **HTTP 5xx** server errors.
- `httpx.TimeoutException` / `httpx.NetworkError` from the underlying
  transport.

Do **not** retry on 400 `BadRequest` (e.g. "chat not found"), 401
`Unauthorized` (bad token — config error, alert and stop), or 403
`Forbidden` (bot blocked by user). These are non-transient; alert via #008.

### Open questions to resolve during implementation
- [ ] Confirm `python-telegram-bot` v21 exposes `Bot.set_webhook`,
      `Bot.send_message` directly (it does — confirm against the version
      pinned in `pyproject.toml`).
- [ ] Decide whether to use `python-telegram-bot`'s built-in `httpx`
      transport or pass our own `httpx.AsyncClient` (for shared connection
      pooling / test injection via `respx`).

## Notes
- Inbound handling (webhook parsing) is #019, not here.
