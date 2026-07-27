"""SQLModel table definitions — placeholder.

The four tables (leetcode_problems, leetcode_log, pending_review,
tutor_lessons) are defined in issue #003 (Phase 0). This file exists so
the package imports cleanly and Alembic's `env.py` has a target; it will
be filled in with the actual `class Problem(SQLModel, table=True)` etc.
when #003 is implemented.

Until then, `SQLModel.metadata` is empty and `alembic upgrade head` runs
the initial (empty) migration successfully — enough for the container to
boot and `/health` to verify DB connectivity.
"""

from leetcode_coach.db.base import SQLModel  # noqa: F401  (re-export for Alembic env.py)
