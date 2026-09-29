"""Add local amoCRM entity snapshots.

Revision ID: 20260724_0007
Revises: 20260723_0006
Create Date: 2026-07-24 11:25:00
"""

from alembic import op
import sqlalchemy as sa

revision = "20260724_0007"
down_revision = "20260723_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "amocrm_snapshots" not in inspector.get_table_names():
        op.create_table(
            "amocrm_snapshots",
            sa.Column("entity_type", sa.String(length=40), nullable=False),
            sa.Column("entity_id", sa.BigInteger(), nullable=False),
            sa.Column("data", sa.JSON(), nullable=False),
            sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("entity_type", "entity_id"),
        )
        op.create_index(
            "ix_amocrm_snapshots_fetched_at",
            "amocrm_snapshots",
            ["fetched_at"],
        )
        op.create_index(
            "ix_amocrm_snapshots_expires_at",
            "amocrm_snapshots",
            ["expires_at"],
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "amocrm_snapshots" in inspector.get_table_names():
        op.drop_table("amocrm_snapshots")
