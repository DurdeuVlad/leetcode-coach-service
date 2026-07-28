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

    # Google Tasks
    google_client_id: str = Field(...)
    google_client_secret: str = Field(...)
    google_refresh_token: str = Field(...)
    google_tasks_list_id: str = Field(..., description="Task list id to create/complete tasks in")

    # YouTube (optional)
    youtube_api_key: str = Field("", description="If absent, YouTube search is disabled")

    # LeetCode
    leetcode_username: str = Field(...)

    # Runtime
    timezone: str = Field("Europe/Bucharest")
    log_level: str = Field("INFO")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings singleton — constructed once on first access."""
    return Settings()  # type: ignore[call-arg]
