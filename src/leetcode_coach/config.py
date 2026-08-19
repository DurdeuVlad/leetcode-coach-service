"""Typed runtime configuration for the agentic service."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class V2Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    database_url: str = Field(..., description="Fresh V2 PostgreSQL DSN")
    legacy_database_url: str = Field(
        "", description="Read-only v1 DSN used only by the one-shot importer"
    )

    telegram_bot_token: str = Field(...)
    telegram_chat_id: str = Field(...)
    telegram_webhook_url: str = ""
    telegram_webhook_secret: str = Field(
        ..., description="Required: rejects forged webhook posts that only guess telegram_chat_id"
    )

    openai_api_key: str = Field(...)
    terra_model: str = "gpt-5.6-terra"
    sol_advisor_model: str = "gpt-5.6-sol"
    agent_max_turns: int = Field(16, ge=1, le=32)
    prompt_cache_ttl: str = "30m"

    timezone: str = "Europe/Bucharest"
    leetcode_username: str = ""
    log_level: str = "INFO"

    # Browserless (homelab) — routes LeetCode GraphQL past Cloudflare.
    # Browserless v2 requires the /chrome/ path prefix (e.g. /chrome/function).
    # Blank disables LeetCode refresh; the bot still works with a manually-seeded pool.
    browserless_url: str = ""
    browserless_token: str = ""


@lru_cache(maxsize=1)
def get_settings() -> V2Settings:
    return V2Settings()  # type: ignore[call-arg]
