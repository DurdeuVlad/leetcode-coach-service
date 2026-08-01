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
from decimal import Decimal
from enum import Enum

import sqlalchemy as sa
from sqlmodel import Field

from leetcode_coach.db.base import SQLModel  # re-export for Alembic env.py


class ReviewStatus(str, Enum):
    """Persisted lifecycle states for a problem review."""

    OPEN = "open"
    COACHING = "coaching"
    DONE = "done"
    SKIPPED = "skipped"
    SAW_SOLUTION = "saw_solution"
    EXPIRED = "expired"


class ProposalBatchStatus(str, Enum):
    CREATED = "created"
    ACTIVE = "active"
    PICKED = "picked"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class CandidateStatus(str, Enum):
    AVAILABLE = "available"
    SELECTED = "selected"
    CANCELLED = "cancelled"


class ProcessedUpdateStatus(str, Enum):
    RECEIVED = "received"
    HANDLED = "handled"
    FAILED = "failed"


class CreditReason(str, Enum):
    DAILY_TAX = "daily_tax"
    SOLVED = "solved"
    REVIEWED = "reviewed"
    SAW_SOLUTION = "saw_solution"
    SKIPPED = "skipped"


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
    credits_earned: Decimal = Field(
        default=Decimal("0"),
        sa_column=sa.Column(sa.Numeric(8, 2), nullable=False, server_default="0"),
    )


class ProposalBatch(SQLModel, table=True):
    """One interactive Telegram proposal message and its candidate list."""

    __tablename__ = "proposal_batches"

    id: int | None = Field(default=None, primary_key=True)
    proposed_at: datetime.date = Field(default_factory=datetime.date.today, index=True)
    telegram_message_id: int | None = Field(default=None, unique=True, index=True)
    status: ProposalBatchStatus = Field(
        default=ProposalBatchStatus.CREATED,
        sa_column=sa.Column(sa.String(20), nullable=False, server_default="created", index=True),
    )
    expires_at: datetime.date | None = Field(default=None, index=True)
    extended_until: datetime.date | None = Field(default=None)


class PendingReview(SQLModel, table=True):
    """Tracks up to 2 concurrent open problems per day.

    Correlation key is `message_id` (the Telegram per-problem message ID).
    Flow B looks up this table by reply_to_message.message_id.
    """

    __tablename__ = "pending_review"

    id: int | None = Field(default=None, primary_key=True)
    __table_args__ = (
        # Historical reviews have no candidate link. Every new, batch-backed
        # selection must consume one of the two persisted slots, making the
        # cap enforceable even if an application path regresses.
        sa.CheckConstraint(
            "candidate_id IS NULL OR (batch_id IS NOT NULL AND pick_slot IN (1, 2))",
            name="ck_review_pick_slot",
        ),
        sa.UniqueConstraint("candidate_id", name="uq_pending_review_candidate"),
        sa.UniqueConstraint("batch_id", "pick_slot", name="uq_pending_review_batch_pick_slot"),
    )

    message_id: int = Field(index=True)  # Telegram per-problem message_id
    problem_slug: str = Field(max_length=200, foreign_key="leetcode_problems.slug")
    problem_title: str = Field(max_length=300)  # denormalized for fuzzy match
    proposed_at: datetime.date = Field(default_factory=datetime.date.today, index=True)
    batch_id: int | None = Field(default=None, foreign_key="proposal_batches.id", index=True)
    candidate_id: int | None = Field(default=None, foreign_key="daily_candidates.id", index=True)
    pick_slot: int | None = Field(default=None)
    status: ReviewStatus = Field(
        default=ReviewStatus.OPEN,
        sa_column=sa.Column(sa.String(20), nullable=False, server_default="open", index=True),
    )


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

    __table_args__ = (
        sa.UniqueConstraint("batch_id", "pick_index", name="uq_daily_candidate_batch_pick_index"),
    )

    id: int | None = Field(default=None, primary_key=True)
    batch_id: int | None = Field(default=None, foreign_key="proposal_batches.id", index=True)
    proposed_at: datetime.date = Field(default_factory=datetime.date.today, index=True)
    pick_index: int = Field(default=0)  # 1-based position in the 5-list (1..5)
    slug: str = Field(max_length=200, foreign_key="leetcode_problems.slug")
    title: str = Field(max_length=300)
    url: str = Field(max_length=500)
    tags: str = Field(default="", max_length=500)
    difficulty: str = Field(max_length=10)  # easy / medium / hard
    reasoning: str = Field(default="", max_length=1000)
    coaching_hint: str = Field(default="", max_length=1000)
    status: CandidateStatus = Field(
        default=CandidateStatus.AVAILABLE,
        sa_column=sa.Column(sa.String(20), nullable=False, server_default="available", index=True),
    )


class ProcessedUpdate(SQLModel, table=True):
    """Telegram delivery idempotency record, keyed by Telegram update_id."""

    __tablename__ = "processed_updates"

    update_id: int = Field(primary_key=True)
    received_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC),
        sa_column=sa.Column(sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    status: ProcessedUpdateStatus = Field(
        default=ProcessedUpdateStatus.RECEIVED,
        sa_column=sa.Column(sa.String(20), nullable=False, server_default="received", index=True),
    )
    error: str | None = Field(default=None, max_length=1000)


class CreditLedger(SQLModel, table=True):
    """Append-only, idempotent credit changes."""

    __tablename__ = "credit_ledger"

    id: int | None = Field(default=None, primary_key=True)
    idempotency_key: str = Field(max_length=200, unique=True, index=True)
    amount: Decimal = Field(sa_column=sa.Column(sa.Numeric(8, 2), nullable=False))
    reason: CreditReason = Field(
        sa_column=sa.Column(sa.String(30), nullable=False, index=True),
    )
    review_id: int | None = Field(default=None, foreign_key="pending_review.id", index=True)
    log_id: int | None = Field(default=None, foreign_key="leetcode_log.id", index=True)
    created_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC),
        sa_column=sa.Column(sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


class BotState(SQLModel, table=True):
    """Generic key-value table for runtime state that must survive restarts
    but should not require a redeploy to change (issue #036).

    First and currently only use: the pinned progression message ID
    (FR-8.3, #039) under key ``pinned_message_id``.

    Schema (business-requirements.md §5):
    - ``key``: primary key (e.g. ``pinned_message_id``).
    - ``value``: JSON-encoded string; the consumer parses per key. Keeping
      the column a plain string avoids constraining the schema to today's
      only use (YAGNI — no JSONB, no per-key columns).
    - ``updated_at``: ``TIMESTAMPTZ`` (wall-clock time, not DATE) — set on
      every write. This is the one table that needs real timestamps because
      "last write" semantics matter for state freshness.

    Intentionally generic: add keys as new stateful features arrive; do not
    add columns to existing tables for one-off state.
    """

    __tablename__ = "bot_state"

    key: str = Field(primary_key=True, max_length=100)
    value: str = Field(default="", max_length=2000)
    updated_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC)
    )
