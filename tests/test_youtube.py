"""YouTube client tests (#014) — disabled without key; parses with mock."""

from __future__ import annotations

import httpx
import pytest
import respx

from leetcode_coach.config import get_settings
from leetcode_coach.errors import YouTubeDisabled
from leetcode_coach.integrations import youtube

_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"


@pytest.mark.asyncio
async def test_disabled_without_key_raises_youtube_disabled() -> None:
    get_settings.cache_clear()
    with pytest.raises(YouTubeDisabled):
        await youtube.search_walkthroughs("Two Sum")
    get_settings.cache_clear()


@pytest.mark.asyncio
@respx.mock
async def test_returns_links_with_configured_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YOUTUBE_API_KEY", "real-key")
    get_settings.cache_clear()
    respx.get(_SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {"id": {"videoId": "abc123"}, "snippet": {"title": "Two Sum walkthrough"}},
                    {"id": {"videoId": "def456"}, "snippet": {"title": "Two Sum explained"}},
                ]
            },
        )
    )
    links = await youtube.search_walkthroughs("Two Sum")
    assert links == [
        "https://www.youtube.com/watch?v=abc123",
        "https://www.youtube.com/watch?v=def456",
    ]
    get_settings.cache_clear()


@pytest.mark.asyncio
@respx.mock
async def test_quota_exceeded_raises_youtube_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YOUTUBE_API_KEY", "real-key")
    get_settings.cache_clear()
    respx.get(_SEARCH_URL).mock(
        return_value=httpx.Response(403, json={"error": {"errors": [{"reason": "quotaExceeded"}]}})
    )
    with pytest.raises(YouTubeDisabled):
        await youtube.search_walkthroughs("Two Sum")
    get_settings.cache_clear()


@pytest.mark.asyncio
@respx.mock
async def test_retries_transient_500_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YOUTUBE_API_KEY", "real-key")
    get_settings.cache_clear()
    route = respx.get(_SEARCH_URL).mock(
        side_effect=[
            httpx.Response(500),
            httpx.Response(200, json={"items": [{"id": {"videoId": "zzz"}, "snippet": {}}]}),
        ]
    )
    links = await youtube.search_walkthroughs("Two Sum")
    assert links == ["https://www.youtube.com/watch?v=zzz"]
    assert route.call_count == 2
    get_settings.cache_clear()
