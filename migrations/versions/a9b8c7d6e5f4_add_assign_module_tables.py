"""add assign module tables

Revision ID: a9b8c7d6e5f4
Revises: f1c2d3e4b5a6
Create Date: 2026-04-24 16:30:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "a9b8c7d6e5f4"
down_revision = "f1c2d3e4b5a6"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()

    if "assign_designer_contacts" not in tables:
        op.create_table(
            "assign_designer_contacts",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("designer_name", sa.String(length=120), nullable=False),
            sa.Column("designer_email", sa.String(length=255), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_assign_designer_contacts_designer_name",
            "assign_designer_contacts",
            ["designer_name"],
            unique=True,
        )

    if "assign_order_states" not in tables:
        op.create_table(
            "assign_order_states",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("order_id", sa.Integer(), nullable=False),
            sa.Column("date_received", sa.Date(), nullable=True),
            sa.Column("date_shipping", sa.Date(), nullable=True),
            sa.Column("order_name", sa.String(length=255), nullable=False),
            sa.Column("qty", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("status", sa.String(length=80), nullable=False, server_default="order sheet recieved"),
            sa.Column("assigned_designer_name", sa.String(length=120), nullable=True),
            sa.Column("order_category", sa.String(length=40), nullable=True),
            sa.Column("file_required", sa.String(length=8), nullable=True),
            sa.Column("client_name", sa.String(length=255), nullable=True),
            sa.Column("source", sa.String(length=24), nullable=False, server_default="system"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("order_id"),
        )
        op.create_index("ix_assign_order_states_order_id", "assign_order_states", ["order_id"], unique=True)
        op.create_index("ix_assign_order_states_status", "assign_order_states", ["status"], unique=False)
        op.create_index("ix_assign_order_states_assigned_designer_name", "assign_order_states", ["assigned_designer_name"], unique=False)
        op.create_index("ix_assign_order_states_order_category", "assign_order_states", ["order_category"], unique=False)
        op.create_index("ix_assign_order_states_date_received", "assign_order_states", ["date_received"], unique=False)
        op.create_index("ix_assign_order_states_date_shipping", "assign_order_states", ["date_shipping"], unique=False)

    if "assign_notification_events" not in tables:
        op.create_table(
            "assign_notification_events",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("assign_state_id", sa.Integer(), nullable=False),
            sa.Column("order_id", sa.Integer(), nullable=False),
            sa.Column("old_status", sa.String(length=80), nullable=True),
            sa.Column("new_status", sa.String(length=80), nullable=False),
            sa.Column("recipient_email", sa.String(length=255), nullable=True),
            sa.Column("event_type", sa.String(length=80), nullable=False),
            sa.Column("subject", sa.String(length=255), nullable=False),
            sa.Column("delivery_result", sa.String(length=120), nullable=False, server_default="pending"),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("sent_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["assign_state_id"], ["assign_order_states.id"], ),
            sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_assign_notification_events_assign_state_id", "assign_notification_events", ["assign_state_id"], unique=False)
        op.create_index("ix_assign_notification_events_order_id", "assign_notification_events", ["order_id"], unique=False)
        op.create_index("ix_assign_notification_events_new_status", "assign_notification_events", ["new_status"], unique=False)
        op.create_index("ix_assign_notification_events_event_type", "assign_notification_events", ["event_type"], unique=False)
        op.create_index("ix_assign_notification_events_created_at", "assign_notification_events", ["created_at"], unique=False)


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()

    if "assign_notification_events" in tables:
        op.drop_index("ix_assign_notification_events_created_at", table_name="assign_notification_events")
        op.drop_index("ix_assign_notification_events_event_type", table_name="assign_notification_events")
        op.drop_index("ix_assign_notification_events_new_status", table_name="assign_notification_events")
        op.drop_index("ix_assign_notification_events_order_id", table_name="assign_notification_events")
        op.drop_index("ix_assign_notification_events_assign_state_id", table_name="assign_notification_events")
        op.drop_table("assign_notification_events")

    if "assign_order_states" in tables:
        op.drop_index("ix_assign_order_states_date_shipping", table_name="assign_order_states")
        op.drop_index("ix_assign_order_states_date_received", table_name="assign_order_states")
        op.drop_index("ix_assign_order_states_order_category", table_name="assign_order_states")
        op.drop_index("ix_assign_order_states_assigned_designer_name", table_name="assign_order_states")
        op.drop_index("ix_assign_order_states_status", table_name="assign_order_states")
        op.drop_index("ix_assign_order_states_order_id", table_name="assign_order_states")
        op.drop_table("assign_order_states")

    if "assign_designer_contacts" in tables:
        op.drop_index("ix_assign_designer_contacts_designer_name", table_name="assign_designer_contacts")
        op.drop_table("assign_designer_contacts")

