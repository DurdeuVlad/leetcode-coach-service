"""APScheduler — runs in-process (single container) or as a dedicated process."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import text
from sqlalchemy.engine import Engine

from leetcode_coach.config import get_settings
from leetcode_coach.db.base import create_db_engine
from leetcode_coach.jobs import (
    apply_daily_tax,
    deliver_due_follow_ups,
    expire_state,
    queue_refill,
    refresh_problem_pool,
    send_nudge,
)

log = structlog.get_logger("scheduler")

_LOCK_ID = 8_204_202_602
_RETRY_SECONDS = 10


@dataclass
class SchedulerHandle:
    """Owned by the caller; pass to `stop_scheduler` for clean shutdown."""

    scheduler: AsyncIOScheduler
    lock_connection: object  # raw DBAPI connection holding the advisory lock
    engine: Engine


def _try_lock(engine: Engine):
    connection = engine.raw_connection()
    try:
        cursor = connection.cursor()
        cursor.execute("SELECT pg_try_advisory_lock(%s)", (_LOCK_ID,))
        acquired = bool(cursor.fetchone()[0])
        cursor.close()
        if acquired:
            return connection
    except Exception:
        connection.close()
        raise
    connection.close()
    return None


def _schema_ready(engine: Engine) -> bool:
    try:
        with engine.connect() as connection:
            return (
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "v2_0002"
            )
    except Exception:
        return False


def _build_scheduler(timezone: str) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=timezone)
    scheduler.add_job(
        apply_daily_tax, CronTrigger(hour=0, minute=0, timezone=timezone), id="v2_daily_tax"
    )
    scheduler.add_job(
        queue_refill, CronTrigger(hour=9, minute=5, timezone=timezone), id="v2_queue_refill"
    )
    scheduler.add_job(send_nudge, CronTrigger(hour=20, minute=0, timezone=timezone), id="v2_nudge")
    scheduler.add_job(
        deliver_due_follow_ups,
        CronTrigger(minute="*", timezone=timezone),
        id="v2_follow_ups",
        max_instances=1,
    )
    scheduler.add_job(
        expire_state, CronTrigger(hour=22, minute=0, timezone=timezone), id="v2_expiry"
    )
    scheduler.add_job(
        refresh_problem_pool,
        CronTrigger(day_of_week="mon", hour=3, minute=0, timezone=timezone),
        id="v2_problem_refresh",
    )
    return scheduler


async def start_scheduler() -> SchedulerHandle:
    """Acquire the advisory lock and start the scheduler.

    Waits for the schema to be ready (migrations applied) before acquiring
    the lock. In the in-process model, `entrypoint.sh` runs migrations
    before the app boots, so the schema is normally ready immediately. In the
    dedicated-process model, migrations may still be in-flight.

    The pg advisory lock ensures only one scheduler instance fires jobs, even
    if multiple containers or processes are running.
    """
    settings = get_settings()
    timezone = settings.timezone
    engine = create_db_engine(settings.database_url)
    lock = None
    while lock is None:
        if _schema_ready(engine):
            lock = await asyncio.to_thread(_try_lock, engine)
        if lock is None:
            log.info("scheduler_waiting_for_schema_or_lock")
            await asyncio.sleep(_RETRY_SECONDS)
    scheduler = _build_scheduler(timezone)
    scheduler.start()
    log.info("scheduler_started", timezone=timezone, lock_id=_LOCK_ID)
    return SchedulerHandle(scheduler=scheduler, lock_connection=lock, engine=engine)


def stop_scheduler(handle: SchedulerHandle) -> None:
    """Shut down the scheduler, release the lock, and dispose the engine."""
    with suppress(Exception):
        handle.scheduler.shutdown(wait=False)
    with suppress(Exception):
        handle.lock_connection.close()
    with suppress(Exception):
        handle.engine.dispose()
    log.info("scheduler_stopped")


async def serve() -> None:
    """Dedicated-process entrypoint: start scheduler and block until cancelled."""
    handle = await start_scheduler()
    try:
        await asyncio.Event().wait()
    finally:
        stop_scheduler(handle)


if __name__ == "__main__":
    asyncio.run(serve())
