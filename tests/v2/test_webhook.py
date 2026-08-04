from types import SimpleNamespace

import pytest

from leetcode_coach_v2 import main


class _Request:
    async def json(self):
        return {
            "update_id": 9001,
            "message": {
                "chat": {"id": 123},
                "message_id": 7,
                "text": "status",
            },
        }


class _Session:
    def __init__(self, *_args, **_kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


@pytest.mark.asyncio
@pytest.mark.parametrize(("status", "expected"), [("received", 503), ("handled", 200)])
async def test_duplicate_webhook_distinguishes_inflight_from_handled(
    monkeypatch, status, expected
) -> None:
    class FakeDomain:
        def __init__(self, _session):
            pass

        def record_update(self, update_id, chat_id):
            return False

        def processed_update_status(self, update_id):
            return status

    monkeypatch.setattr(main, "Session", _Session)
    monkeypatch.setattr(main, "CoachDomain", FakeDomain)
    monkeypatch.setattr(
        main,
        "settings",
        SimpleNamespace(telegram_webhook_secret="secret", telegram_chat_id="123"),
    )

    response = await main.telegram_webhook(
        _Request(), x_telegram_bot_api_secret_token="secret"
    )

    assert response.status_code == expected


@pytest.mark.asyncio
async def test_invalid_webhook_secret_is_ignored_without_touching_state(monkeypatch) -> None:
    monkeypatch.setattr(
        main,
        "settings",
        SimpleNamespace(telegram_webhook_secret="secret", telegram_chat_id="123"),
    )

    response = await main.telegram_webhook(
        _Request(), x_telegram_bot_api_secret_token="wrong"
    )

    assert response.status_code == 200
