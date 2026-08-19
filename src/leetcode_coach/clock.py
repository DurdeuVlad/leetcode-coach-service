"""Authoritative Europe/Bucharest calendar and scheduling helpers."""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

DEFAULT_TIMEZONE = "Europe/Bucharest"


def local_now(timezone: str = DEFAULT_TIMEZONE) -> dt.datetime:
    return dt.datetime.now(ZoneInfo(timezone))


def local_today(timezone: str = DEFAULT_TIMEZONE) -> dt.date:
    return local_now(timezone).date()


def local_wall_to_utc(
    value: str, timezone: str = DEFAULT_TIMEZONE, *, fold: int | None = None
) -> dt.datetime:
    wall = dt.datetime.fromisoformat(value)
    if wall.tzinfo is not None:
        return wall.astimezone(dt.UTC)
    zone = ZoneInfo(timezone)
    candidates = [wall.replace(tzinfo=zone, fold=item) for item in (0, 1)]
    valid = [
        item
        for item in candidates
        if item.astimezone(dt.UTC).astimezone(zone).replace(tzinfo=None) == wall
    ]
    distinct = {item.utcoffset() for item in valid}
    if not valid:
        raise ValueError("local_due_at is a nonexistent local time")
    if len(distinct) > 1 and fold is None:
        raise ValueError("local_due_at is ambiguous; provide fold 0 or 1")
    selected_fold = 0 if fold is None else fold
    if selected_fold not in {0, 1}:
        raise ValueError("fold must be 0 or 1")
    return wall.replace(tzinfo=zone, fold=selected_fold).astimezone(dt.UTC)
