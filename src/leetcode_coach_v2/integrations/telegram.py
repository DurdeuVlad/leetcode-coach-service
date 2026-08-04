"""Minimal Telegram Bot API adapter with retries and a strict chat allowlist."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from leetcode_coach_v2.config import get_settings


class TelegramV2Error(RuntimeError):
    pass


class _TransientTelegramError(TelegramV2Error):
    pass


_WEBHOOK_SECRET = re.compile(r"^[A-Za-z0-9_-]{1,256}$")


class _VisibleHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.length = 0

    def handle_data(self, data: str) -> None:
        self.length += len(data)


def _message_text(text: str, parse_mode: str | None) -> str:
    if not text:
        raise TelegramV2Error("Telegram message text must not be empty")
    if parse_mode is None:
        return text[:4096]
    if parse_mode.upper() != "HTML":
        raise TelegramV2Error("Only Telegram HTML parse mode is supported")
    parser = _VisibleHTML()
    try:
        parser.feed(text)
        parser.close()
    except Exception as exc:
        raise TelegramV2Error("Invalid Telegram HTML") from exc
    if parser.length > 4096:
        raise TelegramV2Error(
            "Telegram HTML exceeds 4096 visible characters after entity parsing"
        )
    return text


def _validate_markup(reply_markup: dict[str, Any] | None) -> None:
    if reply_markup is None:
        return
    for row in reply_markup.get("inline_keyboard", []):
        for button in row:
            callback_data = button.get("callback_data")
            if callback_data is not None and not 1 <= len(str(callback_data).encode("utf-8")) <= 64:
                raise TelegramV2Error("Telegram callback_data must be 1-64 UTF-8 bytes")


def _assert_chat(chat_id: str | int) -> str:
    value = str(chat_id)
    allowed = str(get_settings().telegram_chat_id)
    if value != allowed:
        raise TelegramV2Error(f"chat_id {value} is not allowlisted")
    return value


def _is_mock() -> bool:
    return get_settings().telegram_bot_token in {"", "mock", "dummy"}


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=1, max=8),
    retry=retry_if_exception_type(_TransientTelegramError),
    reraise=True,
)
async def _call(method: str, payload: dict[str, Any]) -> Any:
    if _is_mock():
        return {"message_id": -1} if method == "sendMessage" else True
    url = f"https://api.telegram.org/bot{get_settings().telegram_bot_token}/{method}"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(url, json=payload)
    except httpx.HTTPError as exc:
        raise _TransientTelegramError(f"Telegram {method} transport failure") from exc
    if response.status_code == 429 or response.status_code >= 500:
        raise _TransientTelegramError(f"Telegram {method} HTTP {response.status_code}")
    if response.status_code >= 400:
        raise TelegramV2Error(f"Telegram {method} HTTP {response.status_code}: {response.text}")
    body = response.json()
    if not body.get("ok"):
        raise TelegramV2Error(f"Telegram {method} rejected request: {body}")
    return body.get("result")


async def set_webhook(url: str) -> None:
    if not url.startswith("https://"):
        raise TelegramV2Error("Telegram cloud webhooks require an HTTPS URL")
    payload: dict[str, Any] = {
        "url": url,
        "allowed_updates": ["message", "callback_query"],
    }
    secret = get_settings().telegram_webhook_secret
    if secret:
        if not _WEBHOOK_SECRET.fullmatch(secret):
            raise TelegramV2Error("Invalid Telegram webhook secret_token")
        payload["secret_token"] = secret
    await _call("setWebhook", payload)


async def send_message(
    chat_id: str | int,
    text: str,
    *,
    reply_to_message_id: int | None = None,
    reply_markup: dict[str, Any] | None = None,
    parse_mode: str | None = None,
) -> int:
    _validate_markup(reply_markup)
    payload: dict[str, Any] = {
        "chat_id": _assert_chat(chat_id),
        "text": _message_text(text, parse_mode),
    }
    if reply_to_message_id is not None:
        payload["reply_parameters"] = {"message_id": reply_to_message_id}
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    if parse_mode is not None:
        payload["parse_mode"] = parse_mode
    result = await _call("sendMessage", payload)
    return int(result["message_id"])


async def edit_message(
    chat_id: str | int,
    message_id: int,
    *,
    text: str | None = None,
    reply_markup: dict[str, Any] | None = None,
    parse_mode: str | None = None,
) -> None:
    _validate_markup(reply_markup)
    payload: dict[str, Any] = {
        "chat_id": _assert_chat(chat_id),
        "message_id": message_id,
    }
    if text is not None:
        payload["text"] = _message_text(text, parse_mode)
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        await _call("editMessageText", payload)
    else:
        payload["reply_markup"] = reply_markup
        await _call("editMessageReplyMarkup", payload)


async def answer_callback(callback_id: str, text: str | None = None) -> None:
    payload: dict[str, Any] = {"callback_query_id": callback_id}
    if text:
        payload["text"] = text[:200]
    await _call("answerCallbackQuery", payload)
