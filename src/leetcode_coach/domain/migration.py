"""One-way, deliberately narrow import from the legacy schema."""

from __future__ import annotations

import datetime as dt

import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlmodel import Session

from leetcode_coach.db.models import V2Attempt, V2Lesson, V2Problem


def import_learning_data(source: Engine, target: Session, *, chat_id: int) -> dict[str, int]:
    """Import only canonical problems, attempt history, and tutor lessons.

    Legacy operational state (callbacks, proposals, reviews, agent state and
    every credit transaction) is intentionally ignored. Calls are safe to
    rerun: problems upsert by slug and old logs/lessons are imported once per
    target database based on their stable legacy ids. Credit data is never
    read, so the new ledger begins at zero.
    """

    metadata = sa.MetaData()
    metadata.reflect(
        bind=source,
        only=lambda name, _: name in {"leetcode_problems", "leetcode_log", "tutor_lessons"},
    )
    counts = {"problems": 0, "attempts": 0, "lessons": 0}
    with source.connect() as connection:
        if "leetcode_problems" in metadata.tables:
            for row in connection.execute(
                sa.select(metadata.tables["leetcode_problems"])
            ).mappings():
                existing = target.get(V2Problem, row["slug"])
                values = dict(
                    title=row["title"],
                    url=row["url"],
                    difficulty=str(row["difficulty"]).lower(),
                    tags=row.get("tags") or "",
                    solved=bool(row.get("solved", False)),
                    last_attempted=row.get("last_attempted"),
                    times_attempted=int(row.get("times_attempted") or 0),
                )
                if existing is None:
                    target.add(V2Problem(slug=row["slug"], **values))
                else:
                    for key, value in values.items():
                        setattr(existing, key, value)
                counts["problems"] += 1
            target.flush()
        if "leetcode_log" in metadata.tables:
            for row in connection.execute(sa.select(metadata.tables["leetcode_log"])).mappings():
                exists = target.exec(
                    sa.select(V2Attempt).where(V2Attempt.legacy_attempt_id == row["id"])
                ).first()
                if exists is None:
                    target.add(
                        V2Attempt(
                            chat_id=chat_id,
                            legacy_attempt_id=row["id"],
                            problem_slug=row["problem_slug"],
                            attempted_on=row.get("date") or dt.date.today(),
                            outcome=row.get("status") or "reviewed",
                            feedback=row.get("tutor_feedback") or "",
                            time_spent_min=row.get("time_spent_min"),
                        )
                    )
                    counts["attempts"] += 1
            target.flush()
        if "tutor_lessons" in metadata.tables:
            for row in connection.execute(sa.select(metadata.tables["tutor_lessons"])).mappings():
                exists = target.exec(
                    sa.select(V2Lesson).where(V2Lesson.legacy_lesson_id == row["id"])
                ).first()
                if exists is None:
                    target.add(
                        V2Lesson(
                            chat_id=chat_id,
                            legacy_lesson_id=row["id"],
                            title=row["title"],
                            category=row.get("category") or "general",
                            active=bool(row.get("active", True)),
                            times_reinforced=int(row.get("times_reinforced") or 1),
                            created_at=row.get("created_at") or dt.date.today(),
                        )
                    )
                    counts["lessons"] += 1
            target.flush()
    return counts
