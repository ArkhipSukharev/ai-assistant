"""Add saved and scheduled report templates.

Revision ID: 20260723_0002
Revises: 20260723_0001
Create Date: 2026-07-23 21:06:00
"""

from alembic import op
import sqlalchemy as sa

revision = "20260723_0002"
down_revision = "20260723_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "report_templates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "owner_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("report_type", sa.String(length=40), nullable=False),
        sa.Column("arguments", sa.JSON(), nullable=False),
        sa.Column("period_mode", sa.String(length=30), nullable=False),
        sa.Column("fixed_date_from", sa.String(length=10), nullable=True),
        sa.Column("fixed_date_to", sa.String(length=10), nullable=True),
        sa.Column("comparison_mode", sa.String(length=30), nullable=False),
        sa.Column("plan_value", sa.BigInteger(), nullable=True),
        sa.Column("schedule_frequency", sa.String(length=20), nullable=False),
        sa.Column("schedule_time", sa.String(length=5), nullable=False),
        sa.Column("schedule_weekday", sa.Integer(), nullable=False),
        sa.Column("schedule_month_day", sa.Integer(), nullable=False),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("export_formats", sa.JSON(), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status", sa.String(length=30), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_report_templates_owner_user_id",
        "report_templates",
        ["owner_user_id"],
    )
    op.create_index(
        "ix_report_templates_next_run_at",
        "report_templates",
        ["next_run_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_report_templates_next_run_at", table_name="report_templates")
    op.drop_index("ix_report_templates_owner_user_id", table_name="report_templates")
    op.drop_table("report_templates")
