"""FastAPI application entrypoint.

Single responsibility (per #034): wire the app + lifespan + routes.
Business logic lives in `flows/`; the lifespan just starts/stops infra
(scheduler hook is a placeholder until #017).

Architecture refs:
- §4: one container, APScheduler in-process via lifespan.
- §9: `/health` returns 200 iff DB reachable (scheduler check is a
  placeholder until #017; reports `scheduler: "not_started"` for now).
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


# Scheduler is started/stopped by #017. Until then this stays None and
# `/health` reports `scheduler: "not_started"`. The seam is explicit.
_scheduler = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup: configure logging; shutdown: dispose DB engine."""
    settings = get_settings()
    _configure_logging(settings.log_level)
    log = structlog.get_logger("lifespan")
    log.info("startup", timezone=settings.timezone, webhook_url=settings.telegram_webhook_url or None)
    # TODO(#017): start APScheduler here; set _scheduler.
    try:
        yield
    finally:
        engine.dispose()
        log.info("shutdown", msg="db engine disposed")
        # TODO(#017): scheduler.shutdown(wait=False).


app = FastAPI(title="LeetCode Coach", version="0.1.0", lifespan=lifespan)


@app.get("/health")
async def health(response: Response) -> dict[str, str]:
    """200 iff DB reachable; scheduler field is a placeholder until #017."""
    db_ok = True
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        db_ok = False

    response.status_code = 200 if db_ok else 503
    return {
        "status": "ok" if db_ok else "degraded",
        "db": "reachable" if db_ok else "unreachable",
        "scheduler": "not_started",
    }
