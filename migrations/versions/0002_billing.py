"""Подписка, суточные лимиты и журнал платежей

Revision ID: 0002_billing
Revises: 0001_initial
Create Date: 2026-08-09

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_billing"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("subscription_until", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "daily_usage",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("sent", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "limit_notified", sa.Boolean(), server_default="false", nullable=False
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "day"),
    )

    op.create_table(
        "payments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("charge_id", sa.String(length=128), nullable=False),
        sa.Column("offer_code", sa.String(length=16), nullable=False),
        sa.Column("stars", sa.Integer(), nullable=False),
        sa.Column("days", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("refunded_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("charge_id"),
    )

    op.add_column(
        "outbox",
        sa.Column("kind", sa.String(length=16), server_default="listing", nullable=False),
    )
    op.alter_column("outbox", "listing_id", existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    op.alter_column("outbox", "listing_id", existing_type=sa.Integer(), nullable=False)
    op.drop_column("outbox", "kind")
    op.drop_table("payments")
    op.drop_table("daily_usage")
    op.drop_column("users", "subscription_until")
