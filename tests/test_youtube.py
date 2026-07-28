"""YouTube client tests (#014) — SearXNG-based search, disabled without URL."""

from __future__ import annotations

import httpx
import pytest
import respx

from leetcode_coach.config import get_settings
from leetcode_coach.errors import YouTubeDisabled
from leetcode_coach.integrations import youtube

_SEARXNG_BASE = "https://search.example.com"


@pytest.mark.asyncio
async def test_disabled_without_url_raises_youtube_disabled() -> None:
    get_settings.cache_clear()
    with pytest.raises(YouTubeDisabled):
        await youtube.search_walkthroughs("Two Sum")
    get_settings.cache_clear()


@pytest.mark.asyncio
@respx.mock
async def test_returns_youtube_links_from_searxng(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEARXNG_URL", _SEARXNG_BASE)
    get_settings.cache_clear()
    respx.get(f"{_SEARXNG_BASE}/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "url": "https://www.youtube.com/watch?v=abc123",
                        "title": "Two Sum walkthrough",
                    },
                    {"url": "https://www.youtube.com/watch?v=def456", "title": "Two Sum explained"},
                    {"url": "https://vimeo.com/123", "title": "irrelevant"},
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
async def test_filters_non_youtube_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEARXNG_URL", _SEARXNG_BASE)
    get_settings.cache_clear()
    respx.get(f"{_SEARXNG_BASE}/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {"url": "https://vimeo.com/123", "title": "vimeo"},
                    {"url": "https://youtu.be/xyz789", "title": "short link"},
                ]
            },
        )
    )
    links = await youtube.search_walkthroughs("Two Sum")
    assert links == ["https://youtu.be/xyz789"]
    get_settings.cache_clear()


@pytest.mark.asyncio
@respx.mock
async def test_searxng_403_raises_youtube_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEARXNG_URL", _SEARXNG_BASE)
    get_settings.cache_clear()
    respx.get(f"{_SEARXNG_BASE}/search").mock(return_value=httpx.Response(403, text="forbidden"))
    with pytest.raises(YouTubeDisabled):
        await youtube.search_walkthroughs("Two Sum")
    get_settings.cache_clear()


@pytest.mark.asyncio
@respx.mock
async def test_retries_transient_500_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEARXNG_URL", _SEARXNG_BASE)
    get_settings.cache_clear()
    route = respx.get(f"{_SEARXNG_BASE}/search").mock(
        side_effect=[
            httpx.Response(500),
            httpx.Response(
                200,
                json={"results": [{"url": "https://www.youtube.com/watch?v=zzz", "title": "ok"}]},
            ),
        ]
    )
    links = await youtube.search_walkthroughs("Two Sum")
    assert links == ["https://www.youtube.com/watch?v=zzz"]
    assert route.call_count == 2
    get_settings.cache_clear()
