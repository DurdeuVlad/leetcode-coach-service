"""Drop pending_review.google_task_id — Google Tasks integration removed

Removes the `google_task_id` column from `pending_review`. The Google Tasks
integration was removed from v1 (see docs/business-requirements.md §8
decision 5, updated 2026-07-31): the `google_tasks.py` module, the four
`GOOGLE_*` env vars, the `GoogleAuthExpiredError` typed exception, and the
connectivity probe were all deleted. The column is no longer written or
read by any code path.

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-31
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0005'
down_revision: Union[str, None] = '0004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column('pending_review', 'google_task_id')


def downgrade() -> None:
    op.add_column(
        'pending_review',
        sa.Column(
            'google_task_id',
            sa.String(length=200),
            nullable=False,
            server_default='',
        ),
    )
