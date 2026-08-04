"""Interactive V2 Telegram simulator using the real agent and database."""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from contextlib import suppress

from leetcode_coach_v2 import application as application_module
from leetcode_coach_v2.application import CoachApplication
from leetcode_coach_v2.config import get_settings
from leetcode_coach_v2.db.base import create_v2_engine


async def _run() -> None:
    for stream in (sys.stdout, sys.stderr):
        with suppress(AttributeError, OSError):
            stream.reconfigure(encoding="utf-8", errors="replace")
    settings = get_settings()
    chat_id = int(settings.telegram_chat_id)
    next_message_id = 1

    async def send_message(
        target_chat_id: str | int,
        text: str,
        *,
        reply_to_message_id: int | None = None,
        reply_markup: dict | None = None,
        parse_mode: str | None = None,
    ) -> int:
        nonlocal next_message_id
        message_id = next_message_id
        next_message_id += 1
        print(f"\nBOT [{message_id}] ({parse_mode or 'plain'}):\n{text}")
        if reply_to_message_id is not None:
            print(f"  reply-to: {reply_to_message_id}")
        if reply_markup:
            print("  buttons:", json.dumps(reply_markup, ensure_ascii=False))
        assert int(target_chat_id) == chat_id
        return message_id

    async def edit_message(
        target_chat_id: str | int,
        message_id: int,
        *,
        text: str | None = None,
        reply_markup: dict | None = None,
        parse_mode: str | None = None,
    ) -> None:
        assert int(target_chat_id) == chat_id
        print(f"\nEDIT [{message_id}] ({parse_mode or 'plain'}): {text or ''}")
        if reply_markup:
            print("  buttons:", json.dumps(reply_markup, ensure_ascii=False))

    async def answer_callback(callback_id: str, text: str | None = None) -> None:
        print(f"\nCALLBACK ACK [{callback_id}]: {text or 'ok'}")

    application_module.send_message = send_message
    application_module.edit_message = edit_message
    application_module.answer_callback = answer_callback

    coach = CoachApplication(create_v2_engine(settings.database_url))
    inbound_message_id = 10_000
    print(
        "V2 terminal simulator. Normal text calls Terra. Commands: "
        "/callback <data>, /reply <bot_message_id> <text>, /quit"
    )
    while True:
        raw = (await asyncio.to_thread(input, "\nYOU> ")).strip()
        if not raw:
            continue
        if raw in {"/quit", "/exit"}:
            return
        if raw.startswith("/callback "):
            await coach.handle_callback(
                chat_id=chat_id,
                callback_id=uuid.uuid4().hex,
                data=raw.removeprefix("/callback ").strip(),
            )
            continue
        reply_to: int | None = None
        text = raw
        if raw.startswith("/reply "):
            _, message_id, text = raw.split(" ", 2)
            reply_to = int(message_id)
        inbound_message_id += 1
        await coach.handle_text(
            chat_id=chat_id,
            text=text,
            message_id=inbound_message_id,
            reply_to_message_id=reply_to,
        )


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
