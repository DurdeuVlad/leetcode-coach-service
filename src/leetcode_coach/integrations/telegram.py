"""Telegram Bot API client — webhook setup + send_message + send_reply.

Mock-aware: if `TELEGRAM_BOT_TOKEN` is the placeholder `mock` or empty,
all calls log instead of hitting the API. This lets the app run end-to-end
in development without real credentials.

Per NFR-1 layer 1, transient failures (429, 5xx, timeouts) retry via
tenacity; auth/4xx do not retry and bubble up as `TelegramError`.

Uses `httpx` directly (not the `python-telegram-bot` SDK) for the outbound
calls in this module, for symmetry with the other integration clients and
so `respx` can mock the transport in tests (#014). `python-telegram-bot`
remains the dependency used for typed `Update` parsing on the inbound
webhook side (#019).
"""

from __future__ import annotations

import time

import httpx
import structlog
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from leetcode_coach.config import get_settings
from leetcode_coach.errors import TelegramError

log = structlog.get_logger("telegram")

_BASE = "https://api.telegram.org/bot{token}/{method}"


class _TransientTelegramError(TelegramError):
    """429/5xx/timeout — retried by tenacity. Never escapes `_call`."""


def _is_mock() -> bool:
    token = get_settings().telegram_bot_token
    return not token or token == "mock"


async def set_webhook(webhook_url: str) -> None:
    """Tell Telegram to POST updates to `webhook_url`.

    Called once on app startup (#030). Passes `secret_token` (verified on
    inbound in #019 via the `X-Telegram-Bot-Api-Secret-Token` header) and
    is explicit about `allowed_updates` (message, callback_query only).
    Pass `webhook_url=""` to clear the webhook. No-op in mock mode.
    """
    if _is_mock():
        log.info("set_webhook_mock", webhook_url=webhook_url)
        return
    secret = get_settings().telegram_webhook_secret
    payload: dict = {"url": webhook_url, "allowed_updates": ["message", "callback_query"]}
    if secret:
        payload["secret_token"] = secret
    await _call("setWebhook", payload)


async def send_message(chat_id: str, text: str, *, reply_markup: dict | None = None) -> int:
    """Send a message, return the resulting message_id.

    Used by Flow A (the 5-candidate list), the expiry sweep (summary), and
    `errors.send_alert` (alerts). `reply_markup` accepts an
    `InlineKeyboardMarkup`-shaped dict for the 5-button pick UI in Flow B.
    Returns -1 in mock mode.
    """
    if _is_mock():
        log.info("send_message_mock", chat_id=chat_id, text=text[:200])
        return -1
    payload: dict = {"chat_id": chat_id, "text": text}
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    data = await _call("sendMessage", payload)
    return int(data["result"]["message_id"])


async def send_reply(
    chat_id: str, reply_to_message_id: int, text: str, *, reply_markup: dict | None = None
) -> int:
    """Send a message replying to a specific prior message.

    Used by Flow B's per-problem coach feedback. `reply_to_message_id` is
    the inbound `Message.message_id` being replied to (not `update_id`).
    Returns -1 in mock mode.
    """
    if _is_mock():
        log.info(
            "send_reply_mock",
            chat_id=chat_id,
            reply_to=reply_to_message_id,
            text=text[:200],
        )
        return -1
    payload: dict = {
        "chat_id": chat_id,
        "reply_to_message_id": reply_to_message_id,
        "text": text,
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    data = await _call("sendMessage", payload)
    return int(data["result"]["message_id"])


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=2, max=10),
    retry=retry_if_exception_type(_TransientTelegramError),
    reraise=True,
)
async def _call(method: str, payload: dict) -> dict:
    """Call a Telegram Bot API method with retry on transient failures only.

    Retries on HTTP 429/5xx and `httpx` timeout/network errors. Does NOT
    retry on 400/401/403 — those are config errors or non-transient
    (bad token, chat not found, bot blocked) and bubble up as `TelegramError`
    for the caller to alert on.
    """
    token = get_settings().telegram_bot_token
    url = _BASE.format(token=token, method=method)
    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=payload)
    except httpx.TimeoutException as e:
        raise _TransientTelegramError(f"telegram {method} timeout") from e
    except httpx.HTTPError as e:
        raise _TransientTelegramError(f"telegram {method} http error: {e}") from e

    duration_ms = int((time.monotonic() - start) * 1000)
    if resp.status_code == 429 or resp.status_code >= 500:
        raise _TransientTelegramError(f"telegram {method} HTTP {resp.status_code}")
    if resp.status_code >= 400:
        # Non-retryable client error (bad token, chat not found, bot blocked).
        raise TelegramError(f"telegram {method} HTTP {resp.status_code}: {resp.text}")
    data = resp.json()
    if not data.get("ok"):
        raise TelegramError(f"telegram {method} not ok: {data}")
    log.info(
        "telegram_call",
        method=method,
        chars_sent=len(payload.get("text", "")),
        message_id=data.get("result", {}).get("message_id"),
        duration_ms=duration_ms,
    )
    return data
