"""The scheduler's small, credit-aware evening nudge."""

from __future__ import annotations

import datetime

from leetcode_coach.config import get_settings
from leetcode_coach.db.base import get_session
from leetcode_coach.db.queries import get_state, set_state
from leetcode_coach.flows.credits import balance, format_balance
from leetcode_coach.integrations.telegram import send_message
from leetcode_coach.webhooks.callbacks import encode_callback, register_callback

_SNOOZE_KEY = "nudge_snoozed_on"


async def send_nudge_if_needed() -> bool:
    """Send one actionable deficit nudge, unless the user snoozed it today."""
    with next(get_session()) as session:
        current_balance = balance(session)
    if current_balance >= 0 or get_state(_SNOOZE_KEY) == datetime.date.today().isoformat():
        return False
    await send_message(
        get_settings().telegram_chat_id,
        f"Credits: {format_balance(current_balance)}. Pick an open problem or use /propose.",
        reply_markup={
            "inline_keyboard": [[
                {"text": "Solve now", "callback_data": encode_callback("nudge_solve", {})},
                {"text": "Accept deficit", "callback_data": encode_callback("nudge_accept", {})},
                {"text": "Snooze", "callback_data": encode_callback("nudge_snooze", {})},
            ]]
        },
    )
    return True


async def handle_solve_now(callback, payload: dict) -> None:
    await send_message(str(callback.message.chat.id), "Open an active review and send your attempt, or use /propose.")


async def handle_accept_deficit(callback, payload: dict) -> None:
    await send_message(str(callback.message.chat.id), "Deficit acknowledged. Your ledger stays unchanged until your next result.")


async def handle_snooze(callback, payload: dict) -> None:
    set_state(_SNOOZE_KEY, datetime.date.today().isoformat())
    await send_message(str(callback.message.chat.id), "Snoozed until tomorrow.")


def register_handlers() -> None:
    register_callback("nudge_solve", handle_solve_now)
    register_callback("nudge_accept", handle_accept_deficit)
    register_callback("nudge_snooze", handle_snooze)
