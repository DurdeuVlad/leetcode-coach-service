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
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request, Response
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

    # Startup diagnostics — one structured block so the operator can see at
    # a glance which integrations are live vs mock/disabled. This is the
    # single place to look when "the bot doesn't respond": each line says
    # whether the corresponding inbound/outbound path is wired.
    db_ok = True
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as e:
        db_ok = False
        log.error("startup_db_unreachable", error=str(e))

    log.info(
        "startup",
        timezone=settings.timezone,
        db="reachable" if db_ok else "unreachable",
        telegram_bot_token="set" if settings.telegram_bot_token else "empty",
        telegram_chat_id=settings.telegram_chat_id,
        webhook_url=settings.telegram_webhook_url or None,
        webhook_secret="set" if settings.telegram_webhook_secret else "empty",
        openai_api_key="set" if settings.openai_api_key else "empty",
        openai_mock_mode=settings.openai_api_key.lower() == "mock",
        gemini_api_key="set" if settings.gemini_api_key else "empty",
        admin_api_enabled=bool(settings.admin_api_key),
        browserless_url=settings.browserless_url or None,
        searxng_url=settings.searxng_url or None,
        leetcode_username=settings.leetcode_username,
        log_level=settings.log_level,
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
        log.warning(
            "webhook_skipped",
            reason="TELEGRAM_WEBHOOK_URL not set — bot will NOT receive Telegram updates",
        )

    try:
        yield
    finally:
        stop_scheduler()
        engine.dispose()
        log.info("shutdown", msg="db engine disposed + scheduler stopped")


app = FastAPI(title="LeetCode Coach", version="0.1.0", lifespan=lifespan)
# Inbound Telegram webhook (#019) — the single inbound HTTP surface.
app.include_router(telegram_router)

# Admin API — only mounted when ADMIN_API_KEY is set (disabled by default).
# Used for automated end-to-end testing via HTTP (Flow A → pick → coach).
if get_settings().admin_api_key:
    from leetcode_coach.webhooks.admin import router as admin_router

    app.include_router(admin_router)


# HTTP request/response logging middleware — logs every inbound request with
# method, path, status, and duration. This is the missing piece for runtime
# observability: without it, a webhook hit that 500s or an admin call that
# hangs leaves no trace in the logs. Health checks are excluded to avoid
# spam (Coolify pings /health frequently).
_access_log = structlog.get_logger("http")


@app.middleware("http")
async def log_requests(request: Request, call_next):  # type: ignore[no-untyped-def]
    """Log every HTTP request with method, path, status, duration_ms.

    Skips ``/health`` to avoid log spam from Coolify's health probe.
    """
    if request.url.path in ("/health", "/health/deep"):
        return await call_next(request)
    start = time.monotonic()
    status_code: int | None = None
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    except Exception:
        status_code = 500
        raise
    finally:
        duration_ms = int((time.monotonic() - start) * 1000)
        _access_log.info(
            "request",
            method=request.method,
            path=request.url.path,
            status=status_code,
            duration_ms=duration_ms,
            client=request.client.host if request.client else None,
        )


@app.get("/health")
async def health(response: Response) -> dict[str, str]:
    """Lightweight liveness probe — DB + scheduler only.

    Coolify pings this every few seconds, so it must be cheap: no external
    HTTP calls. Use ``/health/deep`` for the full external-service probes.
    200 iff DB reachable + scheduler running.
    """
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


@app.get("/health/deep")
async def health_deep(response: Response) -> dict[str, object]:
    """Full diagnostic probe — DB + scheduler + every external service.

    Runs the cheapest possible authenticated round-trip per integration
    (Telegram getMe, OpenAI 1-token completion, etc.). Mock and disabled
    services are reported as such — they are not probed. A probe failure
    never flips the HTTP status; it only surfaces in the payload so an
    operator can see which integration is down without grepping logs.

    Use this for on-demand diagnostics, not for Coolify's liveness pings
    (that would rate-limit external APIs). The terminal ``:ping`` command
    calls the same ``ping_all()`` function.
    """
    db_ok = True
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        db_ok = False

    sched_ok = is_running()
    response.status_code = 200 if (db_ok and sched_ok) else 503

    from leetcode_coach.integrations.connectivity import ping_all

    probes = await ping_all()
    services = {r.name: {"status": r.status, "detail": r.detail} for r in probes}

    return {
        "status": "ok" if (db_ok and sched_ok) else "degraded",
        "db": "reachable" if db_ok else "unreachable",
        "scheduler": "running" if sched_ok else "not_started",
        "services": services,
    }
