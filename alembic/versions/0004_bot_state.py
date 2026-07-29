"""bot_state table — generic key-value store for runtime state (issue #036)

Implements the `bot_state` table from docs/business-requirements.md §5.
First and currently only use: the pinned progression message ID (FR-8.3,
#039) under key `pinned_message_id`.

Schema choices (issue #036 principles):
- YAGNI: one table, three columns. No JSONB, no indexes beyond the PK,
  no schema-per-key.
- `updated_at` is TIMESTAMPTZ (wall-clock time) — the one table in this
  schema that needs real timestamps, because "last write" semantics matter
  for state freshness. All other tables use DATE (single-timezone system).
- `value` is a plain string (JSON-encoded by the consumer) so the column
  type stays stable across future keys.

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-29
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = '0004'
down_revision: Union[str, None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('bot_state',
        sa.Column('key', sqlmodel.sql.sqltypes.AutoString(length=100), nullable=False),
        sa.Column('value', sqlmodel.sql.sqltypes.AutoString(length=2000), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('key')
    )


def downgrade() -> None:
    op.drop_table('bot_state')
