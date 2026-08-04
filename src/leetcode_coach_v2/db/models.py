"""Fresh, PostgreSQL-compatible persistence model for Coach V2.

All user-specific state has a ``chat_id`` even though V2 is currently
single-user.  This keeps Telegram idempotency and run serialisation explicit
without pretending that one chat can read another chat's state.
"""

import datetime as dt
from decimal import Decimal
from enum import Enum

import sqlalchemy as sa
from sqlmodel import Field

from leetcode_coach_v2.db.base import V2SQLModel


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def as_utc(value: dt.datetime) -> dt.datetime:
    """Normalize database timestamps before Python-side comparisons.

    PostgreSQL ``timestamp without time zone`` and SQLite both reload values
    without ``tzinfo`` even when the inserted value represented UTC.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.UTC)
    return value.astimezone(dt.UTC)


class Difficulty(str, Enum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class ProposalStatus(str, Enum):
    OPEN = "open"
    PICKED = "picked"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class CandidateStatus(str, Enum):
    AVAILABLE = "available"
    SELECTED = "selected"
    CANCELLED = "cancelled"


class ReviewStatus(str, Enum):
    OPEN = "open"
    DONE = "done"
    SKIPPED = "skipped"
    SAW_SOLUTION = "saw_solution"
    EXPIRED = "expired"


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class AgentRunStatus(str, Enum):
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


class V2Problem(V2SQLModel, table=True):
    __tablename__ = "v2_problems"

    slug: str = Field(primary_key=True, max_length=200)
    title: str = Field(max_length=300)
    url: str = Field(max_length=500)
    difficulty: Difficulty = Field(sa_column=sa.Column(sa.String(10), nullable=False, index=True))
    tags: str = Field(default="", max_length=1000)
    solved: bool = Field(default=False, index=True)
    eligible: bool = Field(default=True, index=True)
    last_attempted: dt.date | None = Field(default=None)
    times_attempted: int = Field(default=0)
    created_at: dt.datetime = Field(default_factory=utcnow)
    updated_at: dt.datetime = Field(default_factory=utcnow)


class V2Attempt(V2SQLModel, table=True):
    __tablename__ = "v2_attempts"
    __table_args__ = (sa.UniqueConstraint("legacy_attempt_id", name="uq_v2_attempt_legacy_id"),)

    id: int | None = Field(default=None, primary_key=True)
    legacy_attempt_id: int | None = Field(default=None, index=True)
    chat_id: int = Field(sa_column=sa.Column(sa.BigInteger, nullable=False, index=True))
    review_id: int | None = Field(default=None, foreign_key="v2_pending_reviews.id", index=True)
    problem_slug: str = Field(foreign_key="v2_problems.slug", max_length=200, index=True)
    attempted_on: dt.date = Field(default_factory=dt.date.today, index=True)
    outcome: str = Field(max_length=30)
    feedback: str = Field(default="", max_length=4000)
    time_spent_min: int | None = Field(default=None)
    created_at: dt.datetime = Field(default_factory=utcnow)


class V2Lesson(V2SQLModel, table=True):
    __tablename__ = "v2_lessons"
    __table_args__ = (sa.UniqueConstraint("legacy_lesson_id", name="uq_v2_lesson_legacy_id"),)

    id: int | None = Field(default=None, primary_key=True)
    legacy_lesson_id: int | None = Field(default=None, index=True)
    chat_id: int = Field(sa_column=sa.Column(sa.BigInteger, nullable=False, index=True))
    title: str = Field(max_length=300)
    category: str = Field(default="general", max_length=100)
    active: bool = Field(default=True, index=True)
    times_reinforced: int = Field(default=1)
    created_at: dt.date = Field(default_factory=dt.date.today)
    updated_at: dt.datetime = Field(default_factory=utcnow)


class V2ProposalBatch(V2SQLModel, table=True):
    __tablename__ = "v2_proposal_batches"

    id: int | None = Field(default=None, primary_key=True)
    chat_id: int = Field(sa_column=sa.Column(sa.BigInteger, nullable=False, index=True))
    proposed_on: dt.date = Field(default_factory=dt.date.today, index=True)
    status: ProposalStatus = Field(
        default=ProposalStatus.OPEN,
        sa_column=sa.Column(sa.String(20), nullable=False, index=True),
    )
    telegram_message_id: int | None = Field(
        default=None, sa_column=sa.Column(sa.BigInteger, nullable=True, index=True)
    )
    expires_at: dt.datetime = Field(
        default_factory=lambda: utcnow() + dt.timedelta(hours=24), index=True
    )
    extended_until: dt.datetime | None = Field(default=None)
    created_at: dt.datetime = Field(default_factory=utcnow)


class V2ProposalCandidate(V2SQLModel, table=True):
    __tablename__ = "v2_proposal_candidates"
    __table_args__ = (
        sa.UniqueConstraint("batch_id", "position", name="uq_v2_candidate_position"),
        sa.UniqueConstraint("batch_id", "problem_slug", name="uq_v2_candidate_problem"),
    )

    id: int | None = Field(default=None, primary_key=True)
    batch_id: int = Field(foreign_key="v2_proposal_batches.id", index=True)
    position: int = Field()
    problem_slug: str = Field(foreign_key="v2_problems.slug", max_length=200, index=True)
    reasoning: str = Field(default="", max_length=2000)
    coaching_hint: str = Field(default="", max_length=2000)
    status: CandidateStatus = Field(
        default=CandidateStatus.AVAILABLE,
        sa_column=sa.Column(sa.String(20), nullable=False, index=True),
    )


class V2PendingReview(V2SQLModel, table=True):
    __tablename__ = "v2_pending_reviews"
    __table_args__ = (sa.UniqueConstraint("candidate_id", name="uq_v2_review_candidate"),)

    id: int | None = Field(default=None, primary_key=True)
    chat_id: int = Field(sa_column=sa.Column(sa.BigInteger, nullable=False, index=True))
    candidate_id: int | None = Field(
        default=None, foreign_key="v2_proposal_candidates.id", index=True
    )
    batch_id: int | None = Field(default=None, foreign_key="v2_proposal_batches.id", index=True)
    problem_slug: str = Field(foreign_key="v2_problems.slug", max_length=200, index=True)
    telegram_message_id: int | None = Field(
        default=None, sa_column=sa.Column(sa.BigInteger, nullable=True, index=True)
    )
    proposed_on: dt.date = Field(default_factory=dt.date.today, index=True)
    status: ReviewStatus = Field(
        default=ReviewStatus.OPEN,
        sa_column=sa.Column(sa.String(20), nullable=False, index=True),
    )
    created_at: dt.datetime = Field(default_factory=utcnow)
    updated_at: dt.datetime = Field(default_factory=utcnow)


class V2CreditLedger(V2SQLModel, table=True):
    __tablename__ = "v2_credit_ledger"

    id: int | None = Field(default=None, primary_key=True)
    chat_id: int = Field(sa_column=sa.Column(sa.BigInteger, nullable=False, index=True))
    idempotency_key: str = Field(max_length=200, unique=True, index=True)
    amount: Decimal = Field(sa_column=sa.Column(sa.Numeric(10, 2), nullable=False))
    reason: str = Field(max_length=50, index=True)
    effective_on: dt.date = Field(default_factory=dt.date.today, index=True)
    review_id: int | None = Field(default=None, foreign_key="v2_pending_reviews.id", index=True)
    created_at: dt.datetime = Field(default_factory=utcnow)


class V2BotState(V2SQLModel, table=True):
    __tablename__ = "v2_bot_state"
    __table_args__ = (sa.UniqueConstraint("chat_id", "key", name="uq_v2_bot_state_chat_key"),)

    id: int | None = Field(default=None, primary_key=True)
    chat_id: int = Field(sa_column=sa.Column(sa.BigInteger, nullable=False, index=True))
    key: str = Field(max_length=100)
    value: str = Field(default="", max_length=4000)
    updated_at: dt.datetime = Field(default_factory=utcnow)


class V2ProcessedUpdate(V2SQLModel, table=True):
    __tablename__ = "v2_processed_updates"

    update_id: int = Field(sa_column=sa.Column(sa.BigInteger, primary_key=True))
    chat_id: int = Field(sa_column=sa.Column(sa.BigInteger, nullable=False, index=True))
    status: str = Field(default="received", max_length=20, index=True)
    received_at: dt.datetime = Field(default_factory=utcnow)
    handled_at: dt.datetime | None = Field(default=None)
    error: str | None = Field(default=None, max_length=1000)


class V2ConversationItem(V2SQLModel, table=True):
    __tablename__ = "v2_conversation_items"
    __table_args__ = (
        sa.UniqueConstraint("chat_id", "sequence", name="uq_v2_conversation_sequence"),
    )

    id: int | None = Field(default=None, primary_key=True)
    chat_id: int = Field(sa_column=sa.Column(sa.BigInteger, nullable=False, index=True))
    sequence: int = Field()
    role: str = Field(max_length=20)
    content: str = Field(sa_column=sa.Column(sa.Text, nullable=False))
    created_at: dt.datetime = Field(default_factory=utcnow)


class V2AgentRun(V2SQLModel, table=True):
    __tablename__ = "v2_agent_runs"

    id: str = Field(primary_key=True, max_length=64)
    chat_id: int = Field(sa_column=sa.Column(sa.BigInteger, nullable=False, index=True))
    status: AgentRunStatus = Field(
        default=AgentRunStatus.RUNNING,
        sa_column=sa.Column(sa.String(20), nullable=False, index=True),
    )
    turn_count: int = Field(default=0)
    sol_calls: int = Field(default=0)
    model: str = Field(default="gpt-5.6-terra", max_length=80)
    input_tokens: int = Field(default=0)
    output_tokens: int = Field(default=0)
    cache_read_tokens: int = Field(default=0)
    cache_write_tokens: int = Field(default=0)
    tool_calls: int = Field(default=0)
    escalation_reason: str | None = Field(default=None, max_length=500)
    latency_ms: int | None = Field(default=None)
    state_json: str = Field(
        default="{}", sa_column=sa.Column(sa.Text, nullable=False, server_default="{}")
    )
    started_at: dt.datetime = Field(default_factory=utcnow)
    updated_at: dt.datetime = Field(default_factory=utcnow)
    completed_at: dt.datetime | None = Field(default=None)


class V2PendingApproval(V2SQLModel, table=True):
    __tablename__ = "v2_pending_approvals"

    id: str = Field(primary_key=True, max_length=64)
    chat_id: int = Field(sa_column=sa.Column(sa.BigInteger, nullable=False, index=True))
    agent_run_id: str | None = Field(default=None, foreign_key="v2_agent_runs.id", index=True)
    action: str = Field(max_length=80)
    payload_json: str = Field(max_length=12000)
    summary: str = Field(max_length=2000)
    status: ApprovalStatus = Field(
        default=ApprovalStatus.PENDING,
        sa_column=sa.Column(sa.String(20), nullable=False, index=True),
    )
    approval_message_id: int | None = Field(
        default=None, sa_column=sa.Column(sa.BigInteger, nullable=True, index=True)
    )
    expires_at: dt.datetime = Field(
        default_factory=lambda: utcnow() + dt.timedelta(hours=24), index=True
    )
    created_at: dt.datetime = Field(default_factory=utcnow)
    resolved_at: dt.datetime | None = Field(default=None)
