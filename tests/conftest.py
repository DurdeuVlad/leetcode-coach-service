"""Shared test fixtures.

`fast_retries`: several integration clients use `tenacity` with real
exponential backoff (2-10s) so production retries are polite to upstream
APIs. In tests we don't want to actually wait — this autouse fixture patches
`asyncio.sleep` so retry tests run at full speed without changing the
retry *logic* under test (attempt counts, exception classification).
"""

from __future__ import annotations

import asyncio

import pytest


@pytest.fixture(autouse=True)
def fast_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    real_sleep = asyncio.sleep

    async def _instant_sleep(_seconds: float, *args, **kwargs) -> None:
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", _instant_sleep)
