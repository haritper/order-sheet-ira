"""add work timing entries

Revision ID: d94e6f2a1b7c
Revises: c31f4bd6e2ab
Create Date: 2026-04-15 16:20:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "d94e6f2a1b7c"
down_revision = "c31f4bd6e2ab"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()

    if "work_timing_entries" not in tables:
        op.create_table(
            "work_timing_entries",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("order_id", sa.Integer(), nullable=False),
            sa.Column("order_code", sa.String(length=120), nullable=False),
            sa.Column("customer_name", sa.String(length=255), nullable=False),
            sa.Column("status", sa.String(length=50), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["order_id"], ["orders.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("order_id"),
        )
        op.create_index(
            "ix_work_timing_entries_order_id",
            "work_timing_entries",
            ["order_id"],
            unique=True,
        )
        op.create_index(
            "ix_work_timing_entries_order_code",
            "work_timing_entries",
            ["order_code"],
            unique=False,
        )
        op.create_index(
            "ix_work_timing_entries_status",
            "work_timing_entries",
            ["status"],
            unique=False,
        )


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()
    if "work_timing_entries" in tables:
        try:
            op.drop_index("ix_work_timing_entries_status", table_name="work_timing_entries")
        except Exception:
            pass
        try:
            op.drop_index("ix_work_timing_entries_order_code", table_name="work_timing_entries")
        except Exception:
            pass
        try:
            op.drop_index("ix_work_timing_entries_order_id", table_name="work_timing_entries")
        except Exception:
            pass
        op.drop_table("work_timing_entries")

