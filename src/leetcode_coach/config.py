"""Typed env-var configuration via pydantic-settings.

Single responsibility (per #034): load + validate env into typed settings.
No business logic, no network calls. Missing required vars fail fast at
startup with a clear error.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All env vars the service reads. Keys match `.env.example` exactly."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Database
    database_url: str = Field(
        ..., description="Postgres DSN, e.g. postgresql+psycopg://user:pass@host:5432/db"
    )

    # Telegram
    telegram_bot_token: str = Field(..., description="Bot token from @BotFather")
    telegram_chat_id: str = Field(..., description="Allowed chat id (single-user allowlist)")
    telegram_webhook_url: str = Field(
        "", description="Public HTTPS URL Telegram will POST updates to"
    )
    telegram_webhook_secret: str = Field(
        "", description="Secret token echoed back in X-Telegram-Bot-Api-Secret-Token"
    )

    # LLM
    openai_api_key: str = Field(..., description="OpenAI API key (primary model)")
    gemini_api_key: str = Field(..., description="Google Gemini API key (fallback model)")

    # Google Tasks — OPTIONAL per the 2026-07-28 decision to discontinue GCP
    # OAuth for v1 (docs/business-requirements.md §8 #5). When all four are
    # empty, google_tasks.py runs in mock mode: create_task returns synthetic
    # IDs, mark_complete/update_task are no-ops. The flows still work; the
    # coach feedback is delivered via Telegram reply instead of Google Task notes.
    google_client_id: str = Field(
        "", description="GCP OAuth client id — leave blank to disable Google Tasks"
    )
    google_client_secret: str = Field(
        "", description="GCP OAuth client secret — leave blank to disable"
    )
    google_refresh_token: str = Field(
        "", description="GCP OAuth refresh token — leave blank to disable"
    )
    google_tasks_list_id: str = Field(
        "", description="Task list id — leave blank to disable Google Tasks"
    )

    # YouTube search via SearXNG (homelab) — primary YouTube search path
    # (per docs/business-requirements.md §8 #3). If absent, YouTube
    # enrichment is disabled (callers skip, not hard error).
    searxng_url: str = Field(
        "", description="Base URL of SearXNG instance, e.g. https://search.example.com"
    )

    # Browserless (homelab) — primary path for LeetCode GraphQL (per
    # docs/business-requirements.md §8 #4). If absent, GraphQL calls raise
    # LeetCodeFetchError immediately.
    browserless_url: str = Field(
        "", description="Base URL of Browserless, e.g. https://browserless.example.com"
    )

    # LeetCode
    leetcode_username: str = Field(...)

    # Admin API — shared secret for the /admin/* test/trigger endpoints.
    # Empty = admin endpoints disabled (returns 404). Set to a random string
    # to enable automated end-to-end testing via HTTP.
    admin_api_key: str = Field(
        "", description="Shared secret for /admin/* endpoints — blank disables admin API"
    )

    # Runtime
    timezone: str = Field("Europe/Bucharest")
    log_level: str = Field("INFO")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings singleton — constructed once on first access."""
    return Settings()  # type: ignore[call-arg]
