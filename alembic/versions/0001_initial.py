"""initial (empty) migration — tables land with issue #003

This migration exists so the container entrypoint's `alembic upgrade head`
succeeds on first deploy. When #003 defines the four SQLModel tables
(leetcode_problems, leetcode_log, pending_review, tutor_lessons),
regenerate the migration with `alembic revision --autogenerate -m "four
tables"` and replace this file (or add a new revision above it).

Revision ID: 0001
Revises:
Create Date: 2026-07-27
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # No tables yet — see issue #003.
    pass


def downgrade() -> None:
    pass
