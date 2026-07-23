"""Add amoCRM webhook events and deal insights.

Revision ID: 20260723_0005
Revises: 20260723_0004
Create Date: 2026-07-23 21:45:00
"""

from alembic import op
import sqlalchemy as sa

revision = "20260723_0005"
down_revision = "20260723_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "amocrm_webhook_events" not in tables:
        op.create_table(
            "amocrm_webhook_events",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("event_key", sa.String(64), nullable=False),
            sa.Column("event_type", sa.String(80), nullable=False),
            sa.Column("entity_type", sa.String(40), nullable=True),
            sa.Column("entity_id", sa.BigInteger(), nullable=True),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("status", sa.String(30), nullable=False),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_amocrm_webhook_events_event_key", "amocrm_webhook_events", ["event_key"], unique=True)
        op.create_index("ix_amocrm_webhook_events_event_type", "amocrm_webhook_events", ["event_type"])
        op.create_index("ix_amocrm_webhook_events_entity_id", "amocrm_webhook_events", ["entity_id"])
        op.create_index("ix_amocrm_webhook_events_status", "amocrm_webhook_events", ["status"])

    if "deal_insights" not in tables:
        op.create_table(
            "deal_insights",
            sa.Column("lead_id", sa.BigInteger(), primary_key=True),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("url", sa.String(500), nullable=False),
            sa.Column("manager_id", sa.BigInteger(), nullable=True),
            sa.Column("risk_score", sa.Integer(), nullable=False),
            sa.Column("risk_level", sa.String(20), nullable=False),
            sa.Column("reasons", sa.JSON(), nullable=False),
            sa.Column("recommendations", sa.JSON(), nullable=False),
            sa.Column("communication", sa.JSON(), nullable=False),
            sa.Column("source", sa.JSON(), nullable=False),
            sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("analyzed_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_deal_insights_manager_id", "deal_insights", ["manager_id"])
        op.create_index("ix_deal_insights_risk_score", "deal_insights", ["risk_score"])
        op.create_index("ix_deal_insights_risk_level", "deal_insights", ["risk_level"])
        op.create_index("ix_deal_insights_analyzed_at", "deal_insights", ["analyzed_at"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "deal_insights" in tables:
        op.drop_table("deal_insights")
    if "amocrm_webhook_events" in tables:
        op.drop_table("amocrm_webhook_events")
