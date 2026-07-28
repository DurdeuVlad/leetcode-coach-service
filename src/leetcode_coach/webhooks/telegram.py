"""Telegram webhook route — the single inbound HTTP surface.

POST /telegram/webhook: parse the Telegram `Update`, enforce the allowlist
(NFR-4) and the `secret_token` header, then dispatch to
`flow_b.handle_update`. Returns 200 quickly so Telegram doesn't retry;
heavy work (LLM coach pass) happens inline (KISS — start with block-and-200,
revisit only if Telegram retries appear in logs).

Per docs/business-requirements.md FR-2.1: routing is **data-driven, not
text-driven** — the route itself does zero routing logic; it just parses,
allowlists, and hands off to `flow_b.handle_update`, which is fleshed out
across #021-#026.

Security:
- `X-Telegram-Bot-Api-Secret-Token` header verified against
  `TELEGRAM_WEBHOOK_SECRET` (set by `setWebhook` in #009). Mismatch → 200
  silent drop (don't leak that we noticed).
- Allowlist: only `TELEGRAM_CHAT_ID` may drive the bot. Non-match → 200
  silent drop, no `send_message` echo back.

Error handling:
- Malformed payload (JSON doesn't parse, or doesn't conform to `Update`)
  → 200 + warning log (truncated to 500 chars). Not alerted (could be probe
  traffic); rate-threshold alerting is a Phase 7 calibration item.
- Any exception escaping `flow_b.handle_update` → still 200 (so Telegram
  doesn't retry and re-trigger duplicate processing) **but** routed through
  `errors.send_alert` (#008). The alert is the operator's signal that
  Flow B is broken; the 200 prevents duplicate work.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Header, Request, Response
from telegram import Update

from leetcode_coach.config import get_settings
from leetcode_coach.errors import send_alert

log = structlog.get_logger("webhook")

router = APIRouter(tags=["telegram"])


def _extract_chat_id(update: Update) -> int | None:
    """Pull the chat id from either a `message` or `callback_query` update.

    Returns None for update types we don't handle (edited_message,
    channel_post, etc.) — the allowlist check then drops them.
    """
    if update.message is not None and update.message.chat is not None:
        return update.message.chat.id
    if update.callback_query is not None and update.callback_query.message is not None:
        return update.callback_query.message.chat.id
    return None


@router.post("/telegram/webhook")
async def telegram_webhook(
    request: Request,
    response: Response,
    x_telegram_bot_api_secret_token: str | None = Header(
        default=None, alias="X-Telegram-Bot-Api-Secret-Token"
    ),
) -> Response:
    """Inbound Telegram Update endpoint.

    Returns 200 in all cases where we want Telegram to stop retrying:
    - allowlist mismatch (silent drop, no echo)
    - secret_token mismatch (silent drop)
    - malformed payload (warning log)
    - successful dispatch (after `handle_update` returns)
    - exception escaping `handle_update` (alert + 200, no retry)

    Only returns non-200 if the body isn't valid JSON at all (extremely
    rare; Telegram always sends JSON). Even then we lean toward 200 to
    suppress retries — see the malformed-payload branch.
    """
    settings = get_settings()

    # 1. secret_token check (NFR-4 / FR-2.1 security). If a secret is
    #    configured, every inbound POST must carry it. Mismatch → 200
    #    silent drop. Don't leak that we noticed.
    if settings.telegram_webhook_secret and (
        x_telegram_bot_api_secret_token != settings.telegram_webhook_secret
    ):
        log.info("webhook_secret_mismatch")
        return Response(status_code=200)

    # 2. Parse body. We accept raw bytes and let telegram.Update.de_json do
    #    the typed parse — it raises on truly broken JSON, and silently
    #    drops unknown fields. We catch both cases below.
    try:
        data = await request.json()
    except Exception:
        log.warning("webhook_malformed_json")
        return Response(status_code=200)

    # 3. Typed parse via python-telegram-bot. `bot=None` is fine — we don't
    #    call any methods on the Update that need a Bot bound to it; we only
    #    read fields. If the payload is missing required `Update` fields
    #    (e.g. `update_id`), de_json still returns an object but with None
    #    fields — the allowlist check then drops it.
    try:
        update = Update.de_json(data, bot=None)
    except Exception as e:
        log.warning("webhook_malformed_update", error=str(e), raw=str(data)[:500])
        return Response(status_code=200)

    if update is None or update.update_id is None:
        log.warning("webhook_no_update_id", raw=str(data)[:500])
        return Response(status_code=200)

    # 4. Allowlist (NFR-4). Only TELEGRAM_CHAT_ID may drive the bot.
    chat_id = _extract_chat_id(update)
    if chat_id is None:
        # Update type we don't handle (edited_message, channel_post, etc.)
        # — silent drop, no log spam (these are common on Telegram).
        return Response(status_code=200)
    try:
        allowed = int(settings.telegram_chat_id)
    except ValueError:
        # Misconfigured allowlist (TELEGRAM_CHAT_ID not an int). Fail loud —
        # this is a config error, not a runtime one. Alert + 200.
        log.error("webhook_allowlist_misconfigured", value=settings.telegram_chat_id)
        await send_alert(
            f"TELEGRAM_CHAT_ID misconfigured (not an int): {settings.telegram_chat_id!r}"
        )
        return Response(status_code=200)
    if chat_id != allowed:
        log.info("webhook_ignored_non_allowlisted", chat_id=chat_id)
        # Silent drop — never echo back to the rejected chat.
        return Response(status_code=200)

    # 5. Dispatch to flow_b. Import here to avoid a circular import at module
    #    load time (flow_b imports webhooks for nothing, but be defensive —
    #    the dispatch boundary is the only place we need it).
    from leetcode_coach.flows.flow_b import handle_update

    try:
        await handle_update(update)
    except Exception as e:
        # Layer 3 (errors.py): alert the operator, but still 200 so Telegram
        # doesn't retry and re-trigger duplicate processing.
        log.error("webhook_handle_failed", error=str(e), update_id=update.update_id)
        await send_alert(f"Flow B handle_update failed: {e!r}")
    return Response(status_code=200)
