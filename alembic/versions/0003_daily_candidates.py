"""daily_candidates table — persists Flow A's 5-candidate list for Flow B

Implements the issue #020 decision: a dedicated `daily_candidates` table
(keyed by date + pick_index) rather than pre-inserted `pending_review` rows.
Rationale documented in docs/architecture.md §7 and the model docstring:
- Preserves the FR-2.2 routing invariant (the 5-list message_id is never
  stored in `pending_review`, so reply-to-5-list → not found → pick-parse).
- Keeps `pending_review` semantics clean (only per-problem threads).

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-28
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = '0003'
down_revision: Union[str, None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('daily_candidates',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('proposed_at', sa.Date(), nullable=False),
    sa.Column('pick_index', sa.Integer(), nullable=False),
    sa.Column('slug', sqlmodel.sql.sqltypes.AutoString(length=200), nullable=False),
    sa.Column('title', sqlmodel.sql.sqltypes.AutoString(length=300), nullable=False),
    sa.Column('url', sqlmodel.sql.sqltypes.AutoString(length=500), nullable=False),
    sa.Column('tags', sqlmodel.sql.sqltypes.AutoString(length=500), nullable=False),
    sa.Column('difficulty', sqlmodel.sql.sqltypes.AutoString(length=10), nullable=False),
    sa.Column('reasoning', sqlmodel.sql.sqltypes.AutoString(length=1000), nullable=False),
    sa.Column('coaching_hint', sqlmodel.sql.sqltypes.AutoString(length=1000), nullable=False),
    sa.ForeignKeyConstraint(['slug'], ['leetcode_problems.slug'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_daily_candidates_proposed_at'), 'daily_candidates', ['proposed_at'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_daily_candidates_proposed_at'), table_name='daily_candidates')
    op.drop_table('daily_candidates')
