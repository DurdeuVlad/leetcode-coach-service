from types import SimpleNamespace

import httpx
import pytest
import respx

from leetcode_coach.integrations import telegram


@pytest.fixture
def staging_settings(monkeypatch):
    settings = SimpleNamespace(
        telegram_bot_token="staging-token",
        telegram_chat_id="REDACTED_TELEGRAM_CHAT_ID",
        telegram_webhook_secret="staging-secret",
    )
    monkeypatch.setattr(telegram, "get_settings", lambda: settings)
    return settings


@pytest.mark.asyncio
@respx.mock
async def test_send_edit_callback_and_webhook_payloads(staging_settings) -> None:
    base = "https://api.telegram.org/botstaging-token"
    send = respx.post(f"{base}/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"message_id": 77}})
    )
    edit = respx.post(f"{base}/editMessageText").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": True})
    )
    callback = respx.post(f"{base}/answerCallbackQuery").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": True})
    )
    webhook = respx.post(f"{base}/setWebhook").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": True})
    )
    markup = {"inline_keyboard": [[{"text": "Pick", "callback_data": "v2p:1:1"}]]}

    message_id = await telegram.send_message(
        staging_settings.telegram_chat_id,
        "<b>Canonical &amp; escaped</b>",
        parse_mode="HTML",
        reply_markup=markup,
        reply_to_message_id=55,
    )
    await telegram.edit_message(
        staging_settings.telegram_chat_id,
        message_id,
        text="Edited",
        parse_mode="HTML",
        reply_markup=markup,
    )
    await telegram.answer_callback("callback-1", "Accepted")
    await telegram.set_webhook("https://staging.example.test/telegram/webhook")

    assert message_id == 77
    assert send.calls[0].request.content
    assert send.calls[0].request.read()
    assert send.calls[0].request.url.path.endswith("/sendMessage")
    assert b'"parse_mode":"HTML"' in send.calls[0].request.content
    assert b'"reply_parameters":{"message_id":55}' in send.calls[0].request.content
    assert b'"callback_data":"v2p:1:1"' in send.calls[0].request.content
    assert b'"message_id":77' in edit.calls[0].request.content
    assert b'"callback_query_id":"callback-1"' in callback.calls[0].request.content
    assert b'"secret_token":"staging-secret"' in webhook.calls[0].request.content
    assert b'"allowed_updates":["message","callback_query"]' in webhook.calls[0].request.content


@pytest.mark.asyncio
@respx.mock
async def test_transport_truncates_limits_and_fails_visibly(staging_settings) -> None:
    route = respx.post("https://api.telegram.org/botstaging-token/sendMessage").mock(
        side_effect=[
            httpx.Response(200, json={"ok": True, "result": {"message_id": 1}}),
            httpx.Response(400, text="bad request"),
        ]
    )

    await telegram.send_message(staging_settings.telegram_chat_id, "x" * 5_000)
    assert len(route.calls[0].request.content) < 5_000
    assert b"x" * 4_096 in route.calls[0].request.content

    with pytest.raises(telegram.TelegramV2Error, match="HTTP 400"):
        await telegram.send_message(staging_settings.telegram_chat_id, "invalid")


@pytest.mark.asyncio
async def test_formatted_text_is_measured_after_html_entity_parsing(staging_settings) -> None:
    valid = "<b>" + ("&amp;" * 4_096) + "</b>"
    assert telegram._message_text(valid, "HTML") == valid

    with pytest.raises(telegram.TelegramV2Error, match="4096 visible"):
        telegram._message_text("<b>" + ("x" * 4_097) + "</b>", "HTML")


@pytest.mark.asyncio
async def test_webhook_and_callback_constraints_are_checked_before_io(staging_settings) -> None:
    with pytest.raises(telegram.TelegramV2Error, match="HTTPS"):
        await telegram.set_webhook("http://example.test/telegram/webhook")

    staging_settings.telegram_webhook_secret = "contains spaces"
    with pytest.raises(telegram.TelegramV2Error, match="secret_token"):
        await telegram.set_webhook("https://example.test/telegram/webhook")

    with pytest.raises(telegram.TelegramV2Error, match="1-64 UTF-8 bytes"):
        await telegram.send_message(
            staging_settings.telegram_chat_id,
            "Pick",
            reply_markup={"inline_keyboard": [[{"text": "Pick", "callback_data": "é" * 33}]]},
        )


@pytest.mark.asyncio
@respx.mock
async def test_transient_telegram_failure_retries_three_times(staging_settings) -> None:
    route = respx.post("https://api.telegram.org/botstaging-token/sendMessage").mock(
        return_value=httpx.Response(503, text="unavailable")
    )

    with pytest.raises(telegram.TelegramV2Error, match="HTTP 503"):
        await telegram.send_message(staging_settings.telegram_chat_id, "retry me")

    assert route.call_count == 3
