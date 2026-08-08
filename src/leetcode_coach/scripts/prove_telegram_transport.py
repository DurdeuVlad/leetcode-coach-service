"""Guarded outbound transport proof for a separate staging Telegram bot."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass

import httpx

from leetcode_coach.config import get_settings
from leetcode_coach.integrations import telegram
from leetcode_coach.rendering import proposal_keyboard, render_proposal_html


@dataclass(frozen=True)
class StagingTelegramSettings:
    telegram_bot_token: str
    telegram_chat_id: str
    telegram_webhook_secret: str = ""


def staging_settings_from_env() -> StagingTelegramSettings:
    token = os.environ.get("STAGING_TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("STAGING_TELEGRAM_CHAT_ID", "").strip()
    if os.environ.get("STAGING_TELEGRAM_ALLOW_SEND") != "YES":
        raise RuntimeError("Set STAGING_TELEGRAM_ALLOW_SEND=YES to authorize staging sends")
    if not token or not chat_id:
        raise RuntimeError("Separate staging Telegram token and chat ID are required")
    if token == get_settings().telegram_bot_token:
        raise RuntimeError("Refusing to use the configured non-staging Telegram bot")
    return StagingTelegramSettings(token, chat_id)


async def _run() -> None:
    staging = staging_settings_from_env()
    telegram.get_settings = lambda: staging
    async with httpx.AsyncClient(timeout=20) as client:
        base = f"https://api.telegram.org/bot{staging.telegram_bot_token}"
        identity = (await client.get(f"{base}/getMe")).json()
        chat = (
            await client.get(f"{base}/getChat", params={"chat_id": staging.telegram_chat_id})
        ).json()
    if not identity.get("ok") or not chat.get("ok"):
        raise RuntimeError("Staging bot or chat credentials are invalid")

    plain_id = await telegram.send_message(
        staging.telegram_chat_id,
        "LeetCode Coach V2 staging transport: plain-text delivery passed.",
    )
    await telegram.edit_message(
        staging.telegram_chat_id,
        plain_id,
        text="LeetCode Coach V2 staging transport: send and edit passed.",
    )
    cards = [
        {
            "title": "Escaping <Test> & Validation",
            "url": "https://leetcode.com/problems/number-of-islands/",
            "difficulty": "medium",
            "tags": "array, dfs & bfs",
            "reasoning": "Confirms deterministic <HTML> escaping.",
            "coaching_hint": "Treat <, >, and & as data.",
        }
    ]
    proposal_id = await telegram.send_message(
        staging.telegram_chat_id,
        render_proposal_html(cards),
        parse_mode="HTML",
        reply_markup=proposal_keyboard(999_999, 1),
    )
    print(
        {
            "staging_bot_username": identity["result"].get("username"),
            "plain_message_id": plain_id,
            "proposal_message_id": proposal_id,
            "outbound_transport": "passed",
        }
    )


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
