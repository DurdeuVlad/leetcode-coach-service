"""Telegram client tests (#014) — send returns message_id; retry on 500."""

from __future__ import annotations

import httpx
import pytest
import respx

from leetcode_coach.errors import TelegramError
from leetcode_coach.integrations import telegram


@pytest.mark.asyncio
@respx.mock
async def test_send_message_returns_message_id() -> None:
    route = respx.post(url__regex=r".*/bot.*/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"message_id": 42}})
    )
    message_id = await telegram.send_message("123456", "hello")
    assert message_id == 42
    assert route.called


@pytest.mark.asyncio
@respx.mock
async def test_send_reply_returns_message_id() -> None:
    respx.post(url__regex=r".*/bot.*/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"message_id": 99}})
    )
    message_id = await telegram.send_reply("123456", 7, "hi back")
    assert message_id == 99


@pytest.mark.asyncio
@respx.mock
async def test_retries_on_500_then_succeeds() -> None:
    route = respx.post(url__regex=r".*/bot.*/sendMessage").mock(
        side_effect=[
            httpx.Response(500, text="server error"),
            httpx.Response(200, json={"ok": True, "result": {"message_id": 5}}),
        ]
    )
    message_id = await telegram.send_message("123456", "retry me")
    assert message_id == 5
    assert route.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_does_not_retry_on_400() -> None:
    route = respx.post(url__regex=r".*/bot.*/sendMessage").mock(
        return_value=httpx.Response(400, json={"ok": False, "description": "chat not found"})
    )
    with pytest.raises(TelegramError):
        await telegram.send_message("123456", "bad chat")
    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_stops_retrying_after_configured_attempts() -> None:
    route = respx.post(url__regex=r".*/bot.*/sendMessage").mock(
        return_value=httpx.Response(500, text="always fails")
    )
    with pytest.raises(TelegramError):
        await telegram.send_message("123456", "never works")
    assert route.call_count == 3
