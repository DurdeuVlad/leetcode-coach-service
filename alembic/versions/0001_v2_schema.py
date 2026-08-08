"""create isolated V2 schema

Revision ID: v2_0001
Revises:
"""

from alembic import op

from leetcode_coach.db import models  # noqa: F401
from leetcode_coach.db.base import BaseSQLModel

revision = "v2_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    BaseSQLModel.metadata.create_all(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    BaseSQLModel.metadata.drop_all(bind=bind)
