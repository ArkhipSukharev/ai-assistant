"""Add pinned conversations.

Revision ID: 20260723_0004
Revises: 20260723_0003
Create Date: 2026-07-23 21:23:00
"""

from alembic import op
import sqlalchemy as sa

revision = "20260723_0004"
down_revision = "20260723_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {item["name"] for item in inspector.get_columns("conversations")}
    if "is_pinned" not in columns:
        op.add_column(
            "conversations",
            sa.Column(
                "is_pinned",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )
    indexes = {item["name"] for item in inspector.get_indexes("conversations")}
    if "ix_conversations_is_pinned" not in indexes:
        op.create_index(
            "ix_conversations_is_pinned",
            "conversations",
            ["is_pinned"],
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    indexes = {item["name"] for item in inspector.get_indexes("conversations")}
    if "ix_conversations_is_pinned" in indexes:
        op.drop_index("ix_conversations_is_pinned", table_name="conversations")
    columns = {item["name"] for item in inspector.get_columns("conversations")}
    if "is_pinned" in columns:
        op.drop_column("conversations", "is_pinned")
