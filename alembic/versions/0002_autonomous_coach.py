"""autonomous coach persistence

Revision ID: v2_0002
Revises: v2_0001
"""

import sqlalchemy as sa
from alembic import op

revision = "v2_0002"
down_revision = "v2_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    bot_columns = {column["name"]: column for column in inspector.get_columns("v2_bot_state")}
    if not isinstance(bot_columns["value"]["type"], sa.Text):
        with op.batch_alter_table("v2_bot_state") as batch:
            batch.alter_column(
                "value", type_=sa.Text(), existing_type=sa.String(4000), nullable=False
            )
    problem_columns = {column["name"] for column in inspector.get_columns("v2_problems")}
    if "verified_solved" not in problem_columns:
        op.add_column(
            "v2_problems",
            sa.Column("verified_solved", sa.Boolean(), server_default=sa.false(), nullable=False),
        )
    if "attempt_baseline_count" not in problem_columns:
        op.add_column(
            "v2_problems",
            sa.Column("attempt_baseline_count", sa.Integer(), server_default="0", nullable=False),
        )
    if "attempt_baseline_last" not in problem_columns:
        op.add_column("v2_problems", sa.Column("attempt_baseline_last", sa.Date(), nullable=True))
    op.execute(
        "UPDATE v2_problems SET verified_solved = solved, "
        "attempt_baseline_count = CASE WHEN times_attempted > "
        "(SELECT COUNT(*) FROM v2_attempts WHERE v2_attempts.problem_slug = v2_problems.slug) "
        "THEN times_attempted - "
        "(SELECT COUNT(*) FROM v2_attempts WHERE v2_attempts.problem_slug = v2_problems.slug) "
        "ELSE 0 END, attempt_baseline_last = CASE WHEN last_attempted IS NOT NULL AND ("
        "(SELECT MAX(attempted_on) FROM v2_attempts "
        "WHERE v2_attempts.problem_slug = v2_problems.slug) IS NULL OR last_attempted > "
        "(SELECT MAX(attempted_on) FROM v2_attempts "
        "WHERE v2_attempts.problem_slug = v2_problems.slug)) "
        "THEN last_attempted ELSE NULL END"
    )
    attempt_columns = {column["name"] for column in inspector.get_columns("v2_attempts")}
    if "language" not in attempt_columns:
        op.add_column("v2_attempts", sa.Column("language", sa.String(50), nullable=True))
    if "solution_summary" not in attempt_columns:
        op.add_column(
            "v2_attempts",
            sa.Column("solution_summary", sa.Text(), server_default="", nullable=False),
        )
    if "reversed_at" not in attempt_columns:
        op.add_column(
            "v2_attempts", sa.Column("reversed_at", sa.DateTime(timezone=True), nullable=True)
        )
    if "updated_at" not in attempt_columns:
        op.add_column(
            "v2_attempts", sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True)
        )
    op.execute("UPDATE v2_attempts SET updated_at = created_at")
    if "updated_at" not in attempt_columns:
        with op.batch_alter_table("v2_attempts") as batch:
            batch.alter_column("updated_at", nullable=False)
    attempt_indexes = {index["name"] for index in inspector.get_indexes("v2_attempts")}
    if "ix_v2_attempts_reversed_at" not in attempt_indexes:
        op.create_index("ix_v2_attempts_reversed_at", "v2_attempts", ["reversed_at"])
    credit_columns = {column["name"] for column in inspector.get_columns("v2_credit_ledger")}
    if "attempt_id" not in credit_columns:
        op.add_column("v2_credit_ledger", sa.Column("attempt_id", sa.Integer(), nullable=True))
        with op.batch_alter_table("v2_credit_ledger") as batch:
            batch.create_foreign_key("fk_v2_credit_attempt", "v2_attempts", ["attempt_id"], ["id"])
            batch.create_index("ix_v2_credit_ledger_attempt_id", ["attempt_id"])
    tables = set(inspector.get_table_names())
    if "v2_attempt_revisions" not in tables:
        _create_attempt_revisions()
    if "v2_follow_ups" not in tables:
        _create_follow_ups()


def _create_attempt_revisions() -> None:
    op.create_table(
        "v2_attempt_revisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("attempt_id", sa.Integer(), sa.ForeignKey("v2_attempts.id"), nullable=False),
        sa.Column("operation_key", sa.String(200), nullable=False, unique=True),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column("before_json", sa.Text(), nullable=False),
        sa.Column("after_json", sa.Text(), nullable=False),
        sa.Column("reason", sa.String(1000), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_v2_attempt_revision_chat", "v2_attempt_revisions", ["chat_id"])
    op.create_index("ix_v2_attempt_revision_attempt", "v2_attempt_revisions", ["attempt_id"])


def _create_follow_ups() -> None:
    op.create_table(
        "v2_follow_ups",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False, unique=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.String(1000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_v2_follow_up_status_due", "v2_follow_ups", ["status", "due_at"])
    op.create_index("ix_v2_follow_up_chat", "v2_follow_ups", ["chat_id"])


def downgrade() -> None:
    op.drop_table("v2_follow_ups")
    op.drop_table("v2_attempt_revisions")
    attempt_fk = next(
        foreign_key
        for foreign_key in sa.inspect(op.get_bind()).get_foreign_keys("v2_credit_ledger")
        if foreign_key["constrained_columns"] == ["attempt_id"]
    )
    attempt_fk_name = attempt_fk["name"] or "fk_v2_credit_ledger_attempt_id_v2_attempts"
    with op.batch_alter_table(
        "v2_credit_ledger",
        naming_convention={"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"},
    ) as batch:
        batch.drop_index("ix_v2_credit_ledger_attempt_id")
        batch.drop_constraint(attempt_fk_name, type_="foreignkey")
        batch.drop_column("attempt_id")
    with op.batch_alter_table("v2_attempts") as batch:
        batch.drop_index("ix_v2_attempts_reversed_at")
        batch.drop_column("updated_at")
        batch.drop_column("reversed_at")
        batch.drop_column("solution_summary")
        batch.drop_column("language")
    problem_indexes = {
        index["name"] for index in sa.inspect(op.get_bind()).get_indexes("v2_problems")
    }
    with op.batch_alter_table("v2_problems") as batch:
        if "ix_v2_problems_verified_solved" in problem_indexes:
            batch.drop_index("ix_v2_problems_verified_solved")
        batch.drop_column("attempt_baseline_last")
        batch.drop_column("attempt_baseline_count")
        batch.drop_column("verified_solved")
    with op.batch_alter_table("v2_bot_state") as batch:
        batch.alter_column("value", type_=sa.String(4000), existing_type=sa.Text(), nullable=False)
