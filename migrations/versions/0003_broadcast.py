"""Текст рассылки в outbox

Revision ID: 0003_broadcast
Revises: 0002_billing
Create Date: 2026-08-09

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_broadcast"
down_revision: str | None = "0002_billing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("outbox", sa.Column("payload", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("outbox", "payload")
