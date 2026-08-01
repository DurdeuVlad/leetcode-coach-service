"""Callback transport tests: opaque wire format and guaranteed acknowledgements."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from telegram import Update

from leetcode_coach.webhooks import callbacks


def _callback_update(data: str) -> Update:
    return Update.de_json(
        {
            "update_id": 5,
            "callback_query": {
                "id": "callback-id",
                "from": {"id": 123456, "is_bot": False, "first_name": "Test"},
                "chat_instance": "instance",
                "data": data,
                "message": {
                    "message_id": 20,
                    "date": 1,
                    "chat": {"id": 123456, "type": "private"},
                    "text": "card",
                },
            },
        },
        bot=None,
    )


def test_encode_decode_is_opaque_and_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    stored: dict[str, str] = {}
    monkeypatch.setattr(callbacks, "set_state", lambda key, value: stored.__setitem__(key, value))
    monkeypatch.setattr(callbacks, "get_state", lambda key: stored.get(key))

    data = callbacks.encode_callback("pick", {"batch_id": 123, "secret": "not-on-wire"})

    assert data.startswith("cb:")
    assert len(data.encode()) <= 64
    assert "123" not in data
    assert callbacks.decode_callback(data) == ("pick", {"batch_id": 123, "secret": "not-on-wire"})


@pytest.mark.asyncio
async def test_stale_callback_is_acknowledged(monkeypatch: pytest.MonkeyPatch) -> None:
    answer = AsyncMock()
    monkeypatch.setattr(callbacks, "answer_callback_query", answer)
    monkeypatch.setattr(callbacks, "get_state", lambda _key: None)

    handled = await callbacks.dispatch_callback(_callback_update("cb:missing").callback_query)

    assert handled is True
    answer.assert_awaited_once_with("callback-id", text="This button is no longer active.")


@pytest.mark.asyncio
async def test_registered_callback_is_acknowledged_before_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    stored: dict[str, str] = {}
    monkeypatch.setattr(callbacks, "set_state", lambda key, value: stored.__setitem__(key, value))
    monkeypatch.setattr(callbacks, "get_state", lambda key: stored.get(key))
    answer = AsyncMock()
    handler = AsyncMock()
    monkeypatch.setattr(callbacks, "answer_callback_query", answer)
    callbacks.register_callback("test_pick", handler)
    data = callbacks.encode_callback("test_pick", {"candidate_id": 9})

    await callbacks.dispatch_callback(_callback_update(data).callback_query)

    answer.assert_awaited_once_with("callback-id")
    handler.assert_awaited_once()
    assert handler.await_args.args[1] == {"candidate_id": 9}
    callbacks._handlers.pop("test_pick", None)
