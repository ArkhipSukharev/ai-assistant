"""Link application users to amoCRM managers.

Revision ID: 20260723_0006
Revises: 20260723_0005
Create Date: 2026-07-23 22:24:00
"""

from alembic import op
import sqlalchemy as sa

revision = "20260723_0006"
down_revision = "20260723_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {item["name"] for item in inspector.get_columns("users")}
    if "amocrm_user_id" not in columns:
        op.add_column(
            "users",
            sa.Column("amocrm_user_id", sa.BigInteger(), nullable=True),
        )
    indexes = {item["name"] for item in inspector.get_indexes("users")}
    if "ix_users_amocrm_user_id" not in indexes:
        op.create_index(
            "ix_users_amocrm_user_id",
            "users",
            ["amocrm_user_id"],
            unique=True,
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    indexes = {item["name"] for item in inspector.get_indexes("users")}
    if "ix_users_amocrm_user_id" in indexes:
        op.drop_index("ix_users_amocrm_user_id", table_name="users")
    columns = {item["name"] for item in inspector.get_columns("users")}
    if "amocrm_user_id" in columns:
        op.drop_column("users", "amocrm_user_id")
