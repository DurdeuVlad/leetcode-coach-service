"""create isolated V2 schema

Revision ID: v2_0001
Revises:
"""

from alembic import op

from leetcode_coach_v2.db import models  # noqa: F401
from leetcode_coach_v2.db.base import V2SQLModel

revision = "v2_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    V2SQLModel.metadata.create_all(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    V2SQLModel.metadata.drop_all(bind=bind)
