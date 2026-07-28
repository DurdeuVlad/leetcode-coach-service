"""FastAPI application entrypoint.

Single responsibility (per #034): wire the app + lifespan + routes.
Business logic lives in `flows/`; the lifespan starts/stops the APScheduler
(#017) and disposes the DB engine.

Architecture refs:
- §4: one container, APScheduler in-process via lifespan.
- §9: `/health` returns 200 iff DB reachable; scheduler field reports
  running/not_started.
- §11: structured JSON logs to stdout via structlog.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Response
from sqlalchemy import text

from leetcode_coach.config import get_settings
from leetcode_coach.db.base import engine
from leetcode_coach.integrations.telegram import set_webhook
from leetcode_coach.scheduling.cron import is_running, start_scheduler, stop_scheduler
from leetcode_coach.webhooks.telegram import router as telegram_router


def _configure_logging(level: str) -> None:
    """structlog → JSON to stdout. Configured once at startup."""
    logging.basicConfig(format="%(message)s", level=getattr(logging, level.upper(), logging.INFO))
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        cache_logger_on_first_use=True,
    )


# Scheduler is started/stopped by the lifespan via scheduling.cron (#017).
# `/health` reads `is_running()` to report scheduler status.


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup: configure logging + start scheduler; shutdown: stop both."""
    settings = get_settings()
    _configure_logging(settings.log_level)
    log = structlog.get_logger("lifespan")
    log.info(
        "startup", timezone=settings.timezone, webhook_url=settings.telegram_webhook_url or None
    )
    start_scheduler()

    # Register the Telegram webhook if a public URL is configured (#030).
    # No-op in mock mode (dummy/empty token) — the app still boots for local dev.
    if settings.telegram_webhook_url:
        try:
            await set_webhook(settings.telegram_webhook_url)
            log.info("webhook_registered", url=settings.telegram_webhook_url)
        except Exception as e:
            log.error("webhook_registration_failed", error=str(e))
    else:
        log.info("webhook_skipped", reason="TELEGRAM_WEBHOOK_URL not set")

    try:
        yield
    finally:
        stop_scheduler()
        engine.dispose()
        log.info("shutdown", msg="db engine disposed + scheduler stopped")


app = FastAPI(title="LeetCode Coach", version="0.1.0", lifespan=lifespan)
# Inbound Telegram webhook (#019) — the single inbound HTTP surface.
app.include_router(telegram_router)


@app.get("/health")
async def health(response: Response) -> dict[str, str]:
    """200 iff DB reachable; scheduler field reports running/not_started."""
    db_ok = True
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        db_ok = False

    sched_ok = is_running()
    response.status_code = 200 if (db_ok and sched_ok) else 503
    return {
        "status": "ok" if (db_ok and sched_ok) else "degraded",
        "db": "reachable" if db_ok else "unreachable",
        "scheduler": "running" if sched_ok else "not_started",
    }
