"""Add personal quick actions.

Revision ID: 20260723_0003
Revises: 20260723_0002
Create Date: 2026-07-23 21:20:00
"""

from alembic import op
import sqlalchemy as sa

revision = "20260723_0003"
down_revision = "20260723_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_quick_actions",
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("actions", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("user_quick_actions")
