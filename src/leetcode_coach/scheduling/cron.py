"""APScheduler setup — in-process cron jobs, started/stopped by the app lifespan.

Single responsibility (per #034): decide *when* jobs fire and delegate to flow
functions. No business logic here.

Jobs registered:
- `flow_a.propose_5()` at `5 9 * * *` Europe/Bucharest (FR-1.1).
  (#028 expiry sweep and #029 weekly refresh add theirs to the same scheduler
  in later phases — this module is the single registration point.)

Escaped job errors are wrapped in the #008 error handler so a failure sends
exactly one Telegram alert (NFR-1 layer 3), never a silent crash.
"""

from __future__ import annotations

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from leetcode_coach.config import get_settings
from leetcode_coach.errors import send_alert

log = structlog.get_logger("scheduler")

# Module-level singleton — one process, one scheduler (architecture §12).
# Started/stopped by main.py's lifespan. `/health` reads `is_running()`.
_scheduler: AsyncIOScheduler | None = None


async def _safe_propose_5() -> None:
    """Wrap propose_5 in the #008 error handler: on any escaped exception,
    send exactly one Telegram alert and log the failure. Never crash the
    scheduler thread (a crashed job would silently stop firing forever).
    """
    from leetcode_coach.flows.flow_a import propose_5

    try:
        await propose_5()
    except Exception as e:
        log.error("flow_a_job_failed", error=str(e), exc_info=True)
        await send_alert(f"Flow A (daily proposal) failed: {e}")


def start_scheduler() -> AsyncIOScheduler:
    """Build and start the in-process AsyncIO scheduler with the Flow A job.

    Idempotent: if already running, returns the existing scheduler. Called
    by the FastAPI lifespan on startup.
    """
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        return _scheduler

    settings = get_settings()
    _scheduler = AsyncIOScheduler(timezone=settings.timezone)
    _scheduler.add_job(
        _safe_propose_5,
        CronTrigger(hour=9, minute=5, timezone=settings.timezone),
        id="flow_a_propose_5",
        replace_existing=True,
    )
    _scheduler.start()
    log.info("scheduler_started", timezone=settings.timezone, jobs=["flow_a_propose_5"])
    return _scheduler


def stop_scheduler() -> None:
    """Stop the scheduler on app shutdown. No-op if not running."""
    global _scheduler
    if _scheduler is None:
        return
    _scheduler.shutdown(wait=False)
    log.info("scheduler_stopped")
    _scheduler = None


def is_running() -> bool:
    """True iff the scheduler is started and running. Used by `/health`."""
    return _scheduler is not None and _scheduler.running
