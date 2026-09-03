"""Регионы в фильтрах и напоминания о подписке

Revision ID: 0005_regions
Revises: 0004_pause
Create Date: 2026-08-10

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_regions"
down_revision: str | None = "0004_pause"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Раньше опрашивался только www.vinted.com, поэтому всё, что уже лежит в базе,
# относится именно к нему.
LEGACY_REGION = "com"


def upgrade() -> None:
    op.add_column(
        "filters",
        sa.Column(
            "regions",
            postgresql.ARRAY(sa.String(length=8)),
            nullable=False,
            server_default=sa.text(f"ARRAY['{LEGACY_REGION}']::varchar[]"),
        ),
    )
    op.create_index(
        "ix_filters_regions", "filters", ["regions"], postgresql_using="gin"
    )

    op.alter_column(
        "listings",
        "country",
        new_column_name="region",
        existing_type=sa.String(length=32),
        type_=sa.String(length=8),
        existing_nullable=False,
        postgresql_using=f"'{LEGACY_REGION}'",
    )

    # Ключ дедупликации становится составным: primary key пересоздаём целиком.
    op.add_column(
        "seen_items",
        sa.Column(
            "region",
            sa.String(length=8),
            nullable=False,
            server_default=LEGACY_REGION,
        ),
    )
    op.drop_constraint("seen_items_pkey", "seen_items", type_="primary")
    op.create_primary_key("seen_items_pkey", "seen_items", ["region", "vinted_id"])

    op.add_column(
        "users",
        sa.Column("renewal_notified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("expiry_notified_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "expiry_notified_at")
    op.drop_column("users", "renewal_notified_at")

    op.drop_constraint("seen_items_pkey", "seen_items", type_="primary")
    op.create_primary_key("seen_items_pkey", "seen_items", ["vinted_id"])
    op.drop_column("seen_items", "region")

    op.alter_column(
        "listings",
        "region",
        new_column_name="country",
        existing_type=sa.String(length=8),
        type_=sa.String(length=32),
        existing_nullable=False,
    )

    op.drop_index("ix_filters_regions", table_name="filters")
    op.drop_column("filters", "regions")
