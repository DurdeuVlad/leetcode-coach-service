"""Scheduler-only process entrypoint; run with ``python -m leetcode_coach.scheduler``."""
from __future__ import annotations

import asyncio
import signal
from contextlib import suppress
from typing import Any

import structlog
from sqlalchemy import text

from leetcode_coach.db.base import engine
from leetcode_coach.scheduling.cron import start_scheduler, stop_scheduler

log = structlog.get_logger("scheduler_service")
SCHEDULER_ADVISORY_LOCK = 8_204_2026
RETRY_SECONDS = 10
REQUIRED_SCHEMA_REVISION = "0006"


def try_acquire_scheduler_lock() -> Any | None:
    """Keep the DBAPI connection open for the lifetime of the lock."""
    connection = engine.raw_connection()
    try:
        cursor = connection.cursor()
        cursor.execute("SELECT pg_try_advisory_lock(%s)", (SCHEDULER_ADVISORY_LOCK,))
        acquired = bool(cursor.fetchone()[0])
        cursor.close()
        if acquired:
            return connection
    except Exception:
        connection.close()
        raise
    connection.close()
    return None


def lock_connection_is_healthy(connection: Any) -> bool:
    """Verify the session which owns the advisory lock is still alive."""
    try:
        cursor = connection.cursor()
        cursor.execute("SELECT 1")
        cursor.fetchone()
        cursor.close()
        return True
    except Exception:
        return False


def schema_is_ready() -> bool:
    """Do not run jobs against a database before the app has migrated it."""
    try:
        with engine.connect() as connection:
            revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        return revision == REQUIRED_SCHEMA_REVISION
    except Exception:
        return False


async def run_scheduler() -> None:
    lock_connection: Any | None = None
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stopped.set)
        except NotImplementedError:  # pragma: no cover
            signal.signal(sig, lambda *_: stopped.set())
    try:
        while not stopped.is_set():
            while lock_connection is None and not stopped.is_set():
                if not await asyncio.to_thread(schema_is_ready):
                    log.info("scheduler_schema_not_ready", required_revision=REQUIRED_SCHEMA_REVISION)
                    with suppress(TimeoutError):
                        await asyncio.wait_for(stopped.wait(), timeout=RETRY_SECONDS)
                    continue
                lock_connection = await asyncio.to_thread(try_acquire_scheduler_lock)
                if lock_connection is None:
                    log.info("scheduler_lock_not_acquired", retry_in_seconds=RETRY_SECONDS)
                    with suppress(TimeoutError):
                        await asyncio.wait_for(stopped.wait(), timeout=RETRY_SECONDS)
            if lock_connection is None:
                break
            log.info("scheduler_lock_acquired", lock_id=SCHEDULER_ADVISORY_LOCK)
            start_scheduler()
            while not stopped.is_set():
                try:
                    await asyncio.wait_for(stopped.wait(), timeout=RETRY_SECONDS)
                    break
                except TimeoutError:
                    if await asyncio.to_thread(lock_connection_is_healthy, lock_connection):
                        continue
                    log.error("scheduler_lock_connection_lost")
                    stop_scheduler()
                    try:
                        lock_connection.close()
                    finally:
                        lock_connection = None
                    break
    finally:
        stop_scheduler()
        if lock_connection is not None:
            lock_connection.close()
        engine.dispose()
        log.info("scheduler_service_stopped")


def main() -> None:
    asyncio.run(run_scheduler())


if __name__ == "__main__":
    main()
