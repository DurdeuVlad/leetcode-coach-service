"""Shared test fixtures.

`fast_retries`: several integration clients use `tenacity` with real
exponential backoff (2-10s) so production retries are polite to upstream
APIs. In tests we don't want to actually wait — this autouse fixture patches
`asyncio.sleep` so retry tests run at full speed without changing the retry
*logic* under test (attempt counts, exception classification).

`telegram_test_settings`: the real `.env` has `TELEGRAM_CHAT_ID=8131572669`
but tests use the dummy `chat_id="123456"`. This autouse fixture patches
`get_settings` in the telegram module only (not globally) so the allowlist
accepts "123456". Tests that mock `send_message` at the module level
(test_flow_b, test_expiry) are unaffected. The negative allowlist test
(`test_rejects_non_allowlisted_chat_id`) still passes because it uses
"999999" which is still not in the patched allowlist.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def fast_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    real_sleep = asyncio.sleep

    async def _instant_sleep(_seconds: float, *args, **kwargs) -> None:
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", _instant_sleep)


@pytest.fixture(autouse=True)
def telegram_test_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch Telegram allowlist so tests using chat_id='123456' pass."""
    from leetcode_coach.integrations import telegram

    test_settings = SimpleNamespace(
        telegram_bot_token="test-token",
        telegram_chat_id="123456",
        telegram_webhook_secret="",
    )
    monkeypatch.setattr(telegram, "get_settings", lambda: test_settings)
