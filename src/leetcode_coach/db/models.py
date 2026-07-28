"""SQLModel table definitions — the 4 tables from business-requirements.md §5.

Tables:
- leetcode_problems: the problem pool (PK = slug)
- leetcode_log: attempt history
- pending_review: tracks up to 2 concurrent open problems per day
- tutor_lessons: the memory system (active/reinforced/graduated lessons)

Schema choices (architecture.md §7):
- `slug` is the PK of leetcode_problems (LeetCode slugs are stable).
- pending_review has no DB-level "at most 2 open per day" constraint — that's
  enforced in application code (Flow A caps picks at 2).
- tutor_lessons.title is not unique by DB constraint; dedup is by similarity
  match in flow_b.py before insert.
- All dates are DATE (not TIMESTAMPTZ) — the system is single-timezone.

NOTE: no `from __future__ import annotations` here, and we use `datetime.date`
rather than `from datetime import date` — the `LeetCodeLog.date` field name
would otherwise shadow the `date` type within the class body and break
pydantic's annotation resolution.
"""

import datetime

from sqlmodel import Field

from leetcode_coach.db.base import SQLModel  # re-export for Alembic env.py


class LeetCodeProblem(SQLModel, table=True):
    """A LeetCode problem in the pool. PK = slug (stable across refreshes)."""

    __tablename__ = "leetcode_problems"

    slug: str = Field(primary_key=True, max_length=200)
    title: str = Field(max_length=300)
    url: str = Field(max_length=500)
    difficulty: str = Field(max_length=10)  # easy / medium / hard
    tags: str = Field(default="", max_length=500)  # comma-separated
    solved: bool = Field(default=False)
    last_attempted: datetime.date | None = Field(default=None)
    times_attempted: int = Field(default=0)


class LeetCodeLog(SQLModel, table=True):
    """A single attempt log entry. Append-only."""

    __tablename__ = "leetcode_log"

    id: int | None = Field(default=None, primary_key=True)
    problem_slug: str = Field(max_length=200, foreign_key="leetcode_problems.slug")
    date: datetime.date = Field(default_factory=datetime.date.today)
    status: str = Field(max_length=20)  # solved / reviewed / skipped / saw_solution
    time_spent_min: int | None = Field(default=None)
    tutor_feedback: str | None = Field(default=None)
    lesson_title: str | None = Field(default=None)


class PendingReview(SQLModel, table=True):
    """Tracks up to 2 concurrent open problems per day.

    Correlation key is `message_id` (the Telegram per-problem message ID).
    Flow B looks up this table by reply_to_message.message_id.
    """

    __tablename__ = "pending_review"

    id: int | None = Field(default=None, primary_key=True)
    message_id: int = Field(index=True)  # Telegram per-problem message_id
    google_task_id: str = Field(default="", max_length=200)
    problem_slug: str = Field(max_length=200, foreign_key="leetcode_problems.slug")
    problem_title: str = Field(max_length=300)  # denormalized for fuzzy match
    proposed_at: datetime.date = Field(default_factory=datetime.date.today, index=True)
    status: str = Field(default="open", max_length=10, index=True)  # open/done/expired


class TutorLesson(SQLModel, table=True):
    """The memory system — generalizable patterns the user is reinforcing.

    Graduation is double-gated (FR-2.6):
    - coach says lesson_should_graduate = true
    - AND times_reinforced >= 5 (read from DB, not from the coach)
    On graduation: active = false.
    """

    __tablename__ = "tutor_lessons"

    id: int | None = Field(default=None, primary_key=True)
    title: str = Field(max_length=300)
    category: str = Field(max_length=100)
    created_at: datetime.date = Field(default_factory=datetime.date.today)
    times_reinforced: int = Field(default=1)
    active: bool = Field(default=True, index=True)


class DailyCandidate(SQLModel, table=True):
    """The 5-candidate list Flow A proposes each day (issue #020 decision).

    Persistence decision (architecture.md §7): a dedicated `daily_candidates`
    table — NOT pre-inserted `pending_review` rows. Rationale:
    - Preserves the FR-2.2 routing invariant: the 5-list Telegram message_id
      is never stored in `pending_review`, so a reply to the 5-list is
      distinguishable from a reply to a per-problem thread (lookup by
      `reply_to_message.message_id` misses → pick-parse path).
    - Keeps `pending_review` semantics clean (only per-problem threads).
    - The ≤2-open-per-day rule is enforced by Flow B's pick cap (≤2 picks),
      not by this table.

    Flow A writes 5 rows per day (one per candidate, indexed 1..5). Flow B's
    pick-parse reads today's rows ordered by `pick_index` and maps the user's
    pick numbers (1-based) → candidates. YAGNI: no historical archive, no
    extra metadata — just enough to map a pick number → problem for today.
    """

    __tablename__ = "daily_candidates"

    id: int | None = Field(default=None, primary_key=True)
    proposed_at: datetime.date = Field(default_factory=datetime.date.today, index=True)
    pick_index: int = Field(default=0)  # 1-based position in the 5-list (1..5)
    slug: str = Field(max_length=200, foreign_key="leetcode_problems.slug")
    title: str = Field(max_length=300)
    url: str = Field(max_length=500)
    tags: str = Field(default="", max_length=500)
    difficulty: str = Field(max_length=10)  # easy / medium / hard
    reasoning: str = Field(default="", max_length=1000)
    coaching_hint: str = Field(default="", max_length=1000)
