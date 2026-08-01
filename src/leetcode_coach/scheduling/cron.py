"""Cron definitions for the scheduler-only service."""
from __future__ import annotations

import importlib
from collections.abc import Awaitable, Callable

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from leetcode_coach.config import get_settings
from leetcode_coach.errors import send_alert

log = structlog.get_logger("scheduler")
_scheduler: AsyncIOScheduler | None = None


def _load_job(module: str, name: str) -> Callable[[], Awaitable[object]] | None:
    """Load future Phase 9 flows at execution time, without startup coupling."""
    try:
        return getattr(importlib.import_module(module), name)
    except (ImportError, AttributeError):
        log.info("scheduled_job_not_available", module=module, function=name)
        return None


async def _run_safely(label: str, module: str, name: str) -> None:
    job = _load_job(module, name)
    if job is None:
        return
    try:
        await job()
    except Exception as exc:
        log.error("scheduled_job_failed", job=label, error=str(exc), exc_info=True)
        await send_alert(f"Scheduled job {label} failed: {exc}")


async def _safe_daily_tax() -> None:
    await _run_safely("daily_tax", "leetcode_coach.flows.credits", "apply_daily_tax")


async def _safe_queue_refill() -> None:
    job = _load_job("leetcode_coach.flows.flow_a", "refill_queue_if_needed")
    if job is None:
        await _run_safely("queue_refill", "leetcode_coach.flows.flow_a", "propose_5")
        return
    try:
        await job()
    except Exception as exc:
        log.error("scheduled_job_failed", job="queue_refill", error=str(exc), exc_info=True)
        await send_alert(f"Scheduled job queue_refill failed: {exc}")


async def _safe_nudge() -> None:
    await _run_safely("nudge", "leetcode_coach.flows.nudge", "send_nudge_if_needed")


async def _safe_sweep_expired() -> None:
    await _run_safely("expiry_sweep", "leetcode_coach.flows.expiry", "sweep_expired")


async def _safe_refresh_pool() -> None:
    await _run_safely("leetcode_refresh_pool", "leetcode_coach.integrations.leetcode", "refresh_pool")


def start_scheduler() -> AsyncIOScheduler:
    """Start jobs only after the scheduler process has become lock leader."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        return _scheduler
    settings = get_settings()
    _scheduler = AsyncIOScheduler(timezone=settings.timezone)
    jobs = (
        ("daily_tax", _safe_daily_tax, CronTrigger(hour=0, minute=0, timezone=settings.timezone)),
        ("queue_refill", _safe_queue_refill, CronTrigger(hour=9, minute=5, timezone=settings.timezone)),
        ("nudge", _safe_nudge, CronTrigger(hour=20, minute=0, timezone=settings.timezone)),
        ("expiry_sweep", _safe_sweep_expired, CronTrigger(hour=22, minute=0, timezone=settings.timezone)),
        ("leetcode_refresh_pool", _safe_refresh_pool, CronTrigger(day_of_week="mon", hour=3, minute=0, timezone=settings.timezone)),
    )
    for job_id, callback, trigger in jobs:
        _scheduler.add_job(callback, trigger, id=job_id, replace_existing=True)
    _scheduler.start()
    log.info("scheduler_started", timezone=settings.timezone, jobs=[job[0] for job in jobs])
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        log.info("scheduler_stopped")


def is_running() -> bool:
    return _scheduler is not None and _scheduler.running
