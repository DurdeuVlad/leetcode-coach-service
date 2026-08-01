"""Telegram inline-callback transport and registry.

Buttons carry an opaque, bounded token rather than business identifiers.  The
token maps to a JSON record in ``bot_state`` so callback data is both below
Telegram's 64-byte limit and cannot be forged into an arbitrary action.
Business modules register handlers; this module only decodes, acknowledges,
and dispatches.
"""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

import structlog
from telegram import CallbackQuery

from leetcode_coach.db.queries import get_state, set_state
from leetcode_coach.integrations.telegram import answer_callback_query

log = structlog.get_logger("callbacks")

_PREFIX = "cb:"
_MAX_CALLBACK_BYTES = 64
_STATE_PREFIX = "callback:"
CallbackHandler = Callable[[CallbackQuery, dict[str, Any]], Awaitable[None]]
_handlers: dict[str, CallbackHandler] = {}


class StaleCallbackError(ValueError):
    """A registered action no longer has a valid state transition."""


def register_callback(action: str, handler: CallbackHandler) -> None:
    """Register a business handler for an opaque callback action.

    Registration is intentionally explicit.  Importing this transport layer
    never imports Flow A/B, avoiding circular imports and allowing phased UI
    handlers to land independently.
    """
    if not action or ":" in action:
        raise ValueError("callback action must be a non-empty simple name")
    _handlers[action] = handler


def encode_callback(action: str, payload: Mapping[str, Any]) -> str:
    """Persist a callback mapping and return Telegram-safe opaque data.

    The deterministic SHA-256 key makes rendering/retrying the same button
    idempotent.  Full payloads remain server-side, never in Telegram's
    ``callback_data`` field.
    """
    if not action or ":" in action:
        raise ValueError("callback action must be a non-empty simple name")
    record = {"action": action, "payload": dict(payload)}
    serialized = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    digest = hashlib.sha256(serialized.encode("utf-8")).digest()[:18]
    token = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    callback_data = f"{_PREFIX}{token}"
    if len(callback_data.encode("utf-8")) > _MAX_CALLBACK_BYTES:  # pragma: no cover - invariant
        raise ValueError("callback_data exceeds Telegram's 64-byte limit")
    set_state(f"{_STATE_PREFIX}{token}", serialized)
    return callback_data


def decode_callback(callback_data: str | None) -> tuple[str, dict[str, Any]] | None:
    """Resolve opaque callback data, returning ``None`` for stale/invalid data."""
    if not callback_data or not callback_data.startswith(_PREFIX):
        return None
    token = callback_data.removeprefix(_PREFIX)
    if not token or len(callback_data.encode("utf-8")) > _MAX_CALLBACK_BYTES:
        return None
    raw = get_state(f"{_STATE_PREFIX}{token}")
    if raw is None:
        return None
    try:
        record = json.loads(raw)
        action = record["action"]
        payload = record["payload"]
    except (KeyError, TypeError, json.JSONDecodeError):
        return None
    if not isinstance(action, str) or not isinstance(payload, dict):
        return None
    return action, payload


async def dispatch_callback(callback: CallbackQuery) -> bool:
    """Acknowledge and dispatch a callback query.

    Returns ``True`` for every callback query, including stale buttons, so it
    cannot fall through into text reply routing.  Unknown/stale callbacks are
    intentionally harmless and receive a short Telegram toast.
    """
    decoded = decode_callback(callback.data)
    if decoded is None:
        await answer_callback_query(callback.id, text="This button is no longer active.")
        return True

    action, payload = decoded
    handler = _handlers.get(action)
    if handler is None:
        log.warning("callback_unregistered_action", action=action)
        await answer_callback_query(callback.id, text="This action is not available yet.")
        return True

    # Telegram clients show a spinner until this call completes.  Acknowledge
    # before business work (LLM, locks, or outbound messages) begins.
    await answer_callback_query(callback.id)
    try:
        await handler(callback, payload)
    except StaleCallbackError:
        # A second answer replaces the initial spinner acknowledgement with a
        # visible toast on Telegram clients.  The action remains a no-op.
        await answer_callback_query(callback.id, text="This button is no longer active.")
    return True
