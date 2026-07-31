"""Typed exception hierarchy + global alert helper.

Per NFR-1 (business-requirements.md §6), there are three error layers:
1. Retry on transient failures (handled by tenacity in the integration clients).
2. Typed error branches for known non-recoverable failures.
3. Global catch that sends one Telegram alert for anything that escapes
   layers 1 and 2.

This module defines the typed exceptions (layer 2) and the `send_alert`
helper used by layer 3. The integration clients raise these; the flows
let everything bubble to the global handler.

`send_alert` is mock-aware: if `TELEGRAM_BOT_TOKEN` is the placeholder
`mock` or empty, it logs the alert instead of calling Telegram. This lets
the app run end-to-end in development without real credentials.
"""

from __future__ import annotations

import structlog

log = structlog.get_logger("errors")


class LeetCodeCoachError(Exception):
    """Base for all typed errors in the system."""


class LLMError(LeetCodeCoachError):
    """Both primary and fallback LLM failed. No coach pass possible."""


class LLMUnavailableError(LLMError):
    """LLM request itself was rejected (4xx other than auth) — not retryable,
    not fallback-able. Raised when the request is malformed or otherwise
    permanently rejected, per the decision table in docs/architecture.md §5.
    """


class TelegramError(LeetCodeCoachError):
    """Telegram API call failed after retries."""


class LeetCodeAPIError(LeetCodeCoachError):
    """LeetCode GraphQL endpoint failed after retries + fallback."""


class LeetCodeFetchError(LeetCodeAPIError):
    """LeetCode GraphQL fetch failed (Browserless unavailable/error/malformed).

    Raised when the homelab Browserless instance is not configured or
    returns an error. Per architecture.md §12, Browserless is the sole
    path for LeetCode GraphQL; we never silently succeed with fabricated
    data.
    """


class YouTubeDisabled(LeetCodeCoachError):
    """YouTube search is unavailable — SearXNG not configured or returned no results.

    Callers treat this as "skip enrichment", not a hard failure (#013).
    """


async def send_alert(message: str) -> None:
    """Send one Telegram alert to the configured chat.

    Used by the global catch (layer 3). Mock-aware: if the bot token is
    the placeholder `mock` or empty, log the alert instead of calling
    Telegram — this lets the app run end-to-end in development without
    real credentials.
    """
    from leetcode_coach.config import get_settings
    from leetcode_coach.integrations.telegram import send_message

    settings = get_settings()
    token = settings.telegram_bot_token
    if not token or token == "mock":
        log.warning("alert_mock", message=message)
        return
    try:
        await send_message(settings.telegram_chat_id, message)
    except Exception as e:
        # If the alert itself fails, log it — never let alerting raise.
        log.error("alert_failed", message=message, error=str(e))
