"""work timing webhook delivery metadata

Revision ID: f1c2d3e4b5a6
Revises: 7a1c6f9d4b2e
Create Date: 2026-04-21 11:15:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "f1c2d3e4b5a6"
down_revision = "7a1c6f9d4b2e"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()
    if "work_timing_alert_events" not in tables:
        return

    columns = {c["name"] for c in inspector.get_columns("work_timing_alert_events")}
    with op.batch_alter_table("work_timing_alert_events", schema=None) as batch_op:
        if "provider_message_id" not in columns:
            batch_op.add_column(sa.Column("provider_message_id", sa.String(length=120), nullable=True))
        batch_op.alter_column(
            "delivery_result",
            existing_type=sa.String(length=40),
            type_=sa.String(length=120),
            existing_nullable=False,
        )


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()
    if "work_timing_alert_events" not in tables:
        return

    columns = {c["name"] for c in inspector.get_columns("work_timing_alert_events")}
    with op.batch_alter_table("work_timing_alert_events", schema=None) as batch_op:
        batch_op.alter_column(
            "delivery_result",
            existing_type=sa.String(length=120),
            type_=sa.String(length=40),
            existing_nullable=False,
        )
        if "provider_message_id" in columns:
            batch_op.drop_column("provider_message_id")

