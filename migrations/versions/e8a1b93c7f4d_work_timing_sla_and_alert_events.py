"""work timing sla and alert events

Revision ID: e8a1b93c7f4d
Revises: d94e6f2a1b7c
Create Date: 2026-04-15 17:10:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "e8a1b93c7f4d"
down_revision = "d94e6f2a1b7c"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()

    if "work_timing_entries" in tables:
        columns = {c["name"] for c in inspector.get_columns("work_timing_entries")}
        with op.batch_alter_table("work_timing_entries", schema=None) as batch_op:
            if "status_updated_at" not in columns:
                batch_op.add_column(sa.Column("status_updated_at", sa.DateTime(), nullable=True))
            if "deadline_at" not in columns:
                batch_op.add_column(sa.Column("deadline_at", sa.DateTime(), nullable=True))
            if "escalation_state" not in columns:
                batch_op.add_column(sa.Column("escalation_state", sa.String(length=20), nullable=True))
            if "giri_alert_sent_at" not in columns:
                batch_op.add_column(sa.Column("giri_alert_sent_at", sa.DateTime(), nullable=True))
            if "md_alert_sent_at" not in columns:
                batch_op.add_column(sa.Column("md_alert_sent_at", sa.DateTime(), nullable=True))

        op.execute(
            "UPDATE work_timing_entries SET status_updated_at = COALESCE(status_updated_at, updated_at, created_at)"
        )
        op.execute(
            "UPDATE work_timing_entries SET escalation_state = COALESCE(NULLIF(escalation_state, ''), 'NONE')"
        )

        with op.batch_alter_table("work_timing_entries", schema=None) as batch_op:
            batch_op.alter_column("status_updated_at", existing_type=sa.DateTime(), nullable=False)
            batch_op.alter_column("escalation_state", existing_type=sa.String(length=20), nullable=False)

        index_names = {idx["name"] for idx in inspector.get_indexes("work_timing_entries")}
        if "ix_work_timing_entries_status_updated_at" not in index_names:
            op.create_index(
                "ix_work_timing_entries_status_updated_at",
                "work_timing_entries",
                ["status_updated_at"],
                unique=False,
            )
        if "ix_work_timing_entries_deadline_at" not in index_names:
            op.create_index(
                "ix_work_timing_entries_deadline_at",
                "work_timing_entries",
                ["deadline_at"],
                unique=False,
            )
        if "ix_work_timing_entries_escalation_state" not in index_names:
            op.create_index(
                "ix_work_timing_entries_escalation_state",
                "work_timing_entries",
                ["escalation_state"],
                unique=False,
            )

    if "work_timing_alert_events" not in tables:
        op.create_table(
            "work_timing_alert_events",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("entry_id", sa.Integer(), nullable=False),
            sa.Column("target", sa.String(length=16), nullable=False),
            sa.Column("event_type", sa.String(length=40), nullable=False),
            sa.Column("status_snapshot", sa.String(length=50), nullable=False),
            sa.Column("deadline_snapshot", sa.DateTime(), nullable=True),
            sa.Column("delivery_mode", sa.String(length=20), nullable=False),
            sa.Column("delivery_result", sa.String(length=40), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["entry_id"], ["work_timing_entries.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_work_timing_alert_events_entry_id", "work_timing_alert_events", ["entry_id"], unique=False)
        op.create_index("ix_work_timing_alert_events_target", "work_timing_alert_events", ["target"], unique=False)
        op.create_index("ix_work_timing_alert_events_event_type", "work_timing_alert_events", ["event_type"], unique=False)
        op.create_index("ix_work_timing_alert_events_delivery_mode", "work_timing_alert_events", ["delivery_mode"], unique=False)
        op.create_index("ix_work_timing_alert_events_created_at", "work_timing_alert_events", ["created_at"], unique=False)


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()

    if "work_timing_alert_events" in tables:
        for idx in [
            "ix_work_timing_alert_events_created_at",
            "ix_work_timing_alert_events_delivery_mode",
            "ix_work_timing_alert_events_event_type",
            "ix_work_timing_alert_events_target",
            "ix_work_timing_alert_events_entry_id",
        ]:
            try:
                op.drop_index(idx, table_name="work_timing_alert_events")
            except Exception:
                pass
        op.drop_table("work_timing_alert_events")

    if "work_timing_entries" in tables:
        for idx in [
            "ix_work_timing_entries_escalation_state",
            "ix_work_timing_entries_deadline_at",
            "ix_work_timing_entries_status_updated_at",
        ]:
            try:
                op.drop_index(idx, table_name="work_timing_entries")
            except Exception:
                pass

        columns = {c["name"] for c in inspector.get_columns("work_timing_entries")}
        with op.batch_alter_table("work_timing_entries", schema=None) as batch_op:
            if "md_alert_sent_at" in columns:
                batch_op.drop_column("md_alert_sent_at")
            if "giri_alert_sent_at" in columns:
                batch_op.drop_column("giri_alert_sent_at")
            if "escalation_state" in columns:
                batch_op.drop_column("escalation_state")
            if "deadline_at" in columns:
                batch_op.drop_column("deadline_at")
            if "status_updated_at" in columns:
                batch_op.drop_column("status_updated_at")

