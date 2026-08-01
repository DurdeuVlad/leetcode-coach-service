"""state-machine persistence, delivery idempotency, and credit ledger.

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "proposal_batches",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("proposed_at", sa.Date(), nullable=False),
        sa.Column("telegram_message_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="created"),
        sa.Column("expires_at", sa.Date(), nullable=True),
        sa.Column("extended_until", sa.Date(), nullable=True),
        sa.CheckConstraint(
            "status IN ('created', 'active', 'picked', 'cancelled', 'expired')",
            name="ck_proposal_batch_status",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("telegram_message_id", name="uq_proposal_batch_telegram_message_id"),
    )
    op.create_index("ix_proposal_batches_proposed_at", "proposal_batches", ["proposed_at"])
    op.create_index("ix_proposal_batches_status", "proposal_batches", ["status"])
    op.create_index("ix_proposal_batches_expires_at", "proposal_batches", ["expires_at"])

    # Existing proposals did not retain Telegram message IDs. Preserve their
    # candidate/review history by making one legacy batch per proposal date.
    op.execute(
        "INSERT INTO proposal_batches (proposed_at, status) "
        "SELECT DISTINCT proposed_at, 'expired' FROM daily_candidates"
    )

    op.add_column("daily_candidates", sa.Column("batch_id", sa.Integer(), nullable=True))
    op.add_column(
        "daily_candidates",
        sa.Column("status", sa.String(length=20), nullable=False, server_default="available"),
    )
    op.execute(
        "UPDATE daily_candidates AS candidate SET batch_id = batch.id "
        "FROM proposal_batches AS batch "
        "WHERE candidate.proposed_at = batch.proposed_at"
    )
    op.create_foreign_key(
        "fk_daily_candidate_batch", "daily_candidates", "proposal_batches", ["batch_id"], ["id"]
    )
    op.create_index("ix_daily_candidates_batch_id", "daily_candidates", ["batch_id"])
    op.create_index("ix_daily_candidates_status", "daily_candidates", ["status"])
    op.create_unique_constraint(
        "uq_daily_candidate_batch_pick_index", "daily_candidates", ["batch_id", "pick_index"]
    )
    op.create_check_constraint(
        "ck_daily_candidate_status",
        "daily_candidates",
        "status IN ('available', 'selected', 'cancelled')",
    )

    op.add_column("pending_review", sa.Column("batch_id", sa.Integer(), nullable=True))
    op.add_column("pending_review", sa.Column("candidate_id", sa.Integer(), nullable=True))
    op.add_column("pending_review", sa.Column("pick_slot", sa.Integer(), nullable=True))
    op.alter_column(
        "pending_review", "status", existing_type=sa.String(length=10), type_=sa.String(length=20)
    )
    op.execute(
        "UPDATE pending_review AS review SET batch_id = batch.id "
        "FROM proposal_batches AS batch "
        "WHERE review.proposed_at = batch.proposed_at"
    )
    op.create_foreign_key(
        "fk_pending_review_batch", "pending_review", "proposal_batches", ["batch_id"], ["id"]
    )
    op.create_foreign_key(
        "fk_pending_review_candidate",
        "pending_review",
        "daily_candidates",
        ["candidate_id"],
        ["id"],
    )
    op.create_index("ix_pending_review_batch_id", "pending_review", ["batch_id"])
    op.create_index("ix_pending_review_candidate_id", "pending_review", ["candidate_id"])
    op.create_unique_constraint("uq_pending_review_candidate", "pending_review", ["candidate_id"])
    op.create_unique_constraint(
        "uq_pending_review_batch_pick_slot", "pending_review", ["batch_id", "pick_slot"]
    )
    # Legacy reviews have no candidate relationship.  New selected
    # candidates must be batch-backed and occupy one of the two unique slots.
    op.create_check_constraint(
        "ck_review_pick_slot",
        "pending_review",
        "candidate_id IS NULL OR (batch_id IS NOT NULL AND pick_slot IN (1, 2))",
    )
    op.create_check_constraint(
        "ck_pending_review_status",
        "pending_review",
        "status IN ('open', 'coaching', 'done', 'skipped', 'saw_solution', 'expired')",
    )

    op.create_table(
        "processed_updates",
        sa.Column("update_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="received"),
        sa.Column("error", sa.String(length=1000), nullable=True),
        sa.CheckConstraint(
            "status IN ('received', 'handled', 'failed')", name="ck_processed_update_status"
        ),
        sa.PrimaryKeyConstraint("update_id"),
    )
    op.create_index("ix_processed_updates_status", "processed_updates", ["status"])

    op.add_column(
        "leetcode_log",
        sa.Column("credits_earned", sa.Numeric(8, 2), nullable=False, server_default="0"),
    )
    op.create_table(
        "credit_ledger",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("amount", sa.Numeric(8, 2), nullable=False),
        sa.Column("reason", sa.String(length=30), nullable=False),
        sa.Column("review_id", sa.Integer(), nullable=True),
        sa.Column("log_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "reason IN ('daily_tax', 'solved', 'reviewed', 'saw_solution', 'skipped')",
            name="ck_credit_ledger_reason",
        ),
        sa.ForeignKeyConstraint(["log_id"], ["leetcode_log.id"]),
        sa.ForeignKeyConstraint(["review_id"], ["pending_review.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_credit_ledger_idempotency_key"),
    )
    op.create_index("ix_credit_ledger_reason", "credit_ledger", ["reason"])
    op.create_index("ix_credit_ledger_review_id", "credit_ledger", ["review_id"])
    op.create_index("ix_credit_ledger_log_id", "credit_ledger", ["log_id"])

    # Historical attempts existed before the ledger.  Backfill both the
    # denormalized log amount and one deterministic append-only ledger entry
    # per recognized outcome.  The key makes this safe to reason about even
    # when a restored database has already received part of the migration.
    op.execute(
        """
        UPDATE leetcode_log AS log
        SET credits_earned = CASE
            WHEN log.status = 'solved' AND problem.difficulty = 'easy' THEN 0.5
            WHEN log.status = 'solved' AND problem.difficulty = 'medium' THEN 1
            WHEN log.status = 'solved' AND problem.difficulty = 'hard' THEN 2
            WHEN log.status = 'reviewed' THEN 0.5
            WHEN log.status = 'saw_solution' THEN 0.25
            ELSE 0
        END
        FROM leetcode_problems AS problem
        WHERE problem.slug = log.problem_slug
        """
    )
    op.execute(
        """
        INSERT INTO credit_ledger (idempotency_key, amount, reason, log_id)
        SELECT
            'log:' || log.id,
            log.credits_earned,
            log.status,
            log.id
        FROM leetcode_log AS log
        WHERE log.status IN ('solved', 'reviewed', 'saw_solution', 'skipped')
        ON CONFLICT (idempotency_key) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_index("ix_credit_ledger_log_id", table_name="credit_ledger")
    op.drop_index("ix_credit_ledger_review_id", table_name="credit_ledger")
    op.drop_index("ix_credit_ledger_reason", table_name="credit_ledger")
    op.drop_table("credit_ledger")
    op.drop_column("leetcode_log", "credits_earned")
    op.drop_index("ix_processed_updates_status", table_name="processed_updates")
    op.drop_table("processed_updates")
    op.drop_constraint("ck_pending_review_status", "pending_review", type_="check")
    op.drop_constraint("ck_review_pick_slot", "pending_review", type_="check")
    op.drop_constraint("uq_pending_review_batch_pick_slot", "pending_review", type_="unique")
    op.drop_constraint("uq_pending_review_candidate", "pending_review", type_="unique")
    op.drop_index("ix_pending_review_candidate_id", table_name="pending_review")
    op.drop_index("ix_pending_review_batch_id", table_name="pending_review")
    op.drop_constraint("fk_pending_review_candidate", "pending_review", type_="foreignkey")
    op.drop_constraint("fk_pending_review_batch", "pending_review", type_="foreignkey")
    op.alter_column(
        "pending_review", "status", existing_type=sa.String(length=20), type_=sa.String(length=10)
    )
    op.drop_column("pending_review", "pick_slot")
    op.drop_column("pending_review", "candidate_id")
    op.drop_column("pending_review", "batch_id")
    op.drop_constraint("ck_daily_candidate_status", "daily_candidates", type_="check")
    op.drop_constraint("uq_daily_candidate_batch_pick_index", "daily_candidates", type_="unique")
    op.drop_index("ix_daily_candidates_status", table_name="daily_candidates")
    op.drop_index("ix_daily_candidates_batch_id", table_name="daily_candidates")
    op.drop_constraint("fk_daily_candidate_batch", "daily_candidates", type_="foreignkey")
    op.drop_column("daily_candidates", "status")
    op.drop_column("daily_candidates", "batch_id")
    op.drop_index("ix_proposal_batches_expires_at", table_name="proposal_batches")
    op.drop_index("ix_proposal_batches_status", table_name="proposal_batches")
    op.drop_index("ix_proposal_batches_proposed_at", table_name="proposal_batches")
    op.drop_table("proposal_batches")
