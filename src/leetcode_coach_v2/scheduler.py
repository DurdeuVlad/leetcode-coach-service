"""Dedicated APScheduler process for V2."""

from __future__ import annotations

import asyncio
from contextlib import suppress

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import text

from leetcode_coach_v2.config import get_settings
from leetcode_coach_v2.db.base import create_v2_engine
from leetcode_coach_v2.jobs import (
    apply_daily_tax,
    expire_state,
    queue_refill,
    refresh_problem_pool,
    send_nudge,
)

_LOCK_ID = 8_204_202_602
_RETRY_SECONDS = 10


def _try_lock(engine):
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


def _schema_ready(engine) -> bool:
    try:
        with engine.connect() as connection:
            return connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "v2_0001"
    except Exception:
        return False


async def serve() -> None:
    settings = get_settings()
    timezone = settings.timezone
    engine = create_v2_engine(settings.database_url)
    lock = None
    while lock is None:
        if _schema_ready(engine):
            lock = await asyncio.to_thread(_try_lock, engine)
        if lock is None:
            await asyncio.sleep(_RETRY_SECONDS)
    scheduler = AsyncIOScheduler(timezone=timezone)
    scheduler.add_job(
        apply_daily_tax, CronTrigger(hour=0, minute=0, timezone=timezone), id="v2_daily_tax"
    )
    scheduler.add_job(
        queue_refill, CronTrigger(hour=9, minute=5, timezone=timezone), id="v2_queue_refill"
    )
    scheduler.add_job(
        send_nudge, CronTrigger(hour=20, minute=0, timezone=timezone), id="v2_nudge"
    )
    scheduler.add_job(
        expire_state, CronTrigger(hour=22, minute=0, timezone=timezone), id="v2_expiry"
    )
    scheduler.add_job(
        refresh_problem_pool,
        CronTrigger(day_of_week="mon", hour=3, minute=0, timezone=timezone),
        id="v2_problem_refresh",
    )
    scheduler.start()
    try:
        await asyncio.Event().wait()
    finally:
        scheduler.shutdown(wait=False)
        with suppress(Exception):
            lock.close()
        engine.dispose()


if __name__ == "__main__":
    asyncio.run(serve())
