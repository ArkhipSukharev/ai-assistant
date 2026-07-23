"""Create the initial F5 Assistant schema.

Revision ID: 20260723_0001
Revises:
Create Date: 2026-07-23 20:45:00
"""

from alembic import op

from app.database import Base

revision = "20260723_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    tables = [
        Base.metadata.tables[name]
        for name in (
            "users",
            "auth_sessions",
            "audit_logs",
            "app_settings",
            "user_model_preferences",
            "conversations",
            "chat_messages",
            "message_attachments",
        )
    ]
    Base.metadata.create_all(bind=op.get_bind(), tables=tables, checkfirst=True)


def downgrade() -> None:
    tables = [
        Base.metadata.tables[name]
        for name in (
            "message_attachments",
            "chat_messages",
            "conversations",
            "user_model_preferences",
            "app_settings",
            "audit_logs",
            "auth_sessions",
            "users",
        )
    ]
    Base.metadata.drop_all(bind=op.get_bind(), tables=tables, checkfirst=True)
