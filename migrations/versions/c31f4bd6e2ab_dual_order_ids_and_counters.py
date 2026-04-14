"""dual order ids and counters

Revision ID: c31f4bd6e2ab
Revises: a4e2d9f91c31, f72c9b8e21b1
Create Date: 2026-04-09 18:55:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "c31f4bd6e2ab"
down_revision = ("a4e2d9f91c31", "f72c9b8e21b1")
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()

    if "order_number_counters" not in tables:
        op.create_table(
            "order_number_counters",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("pod_next_number", sa.Integer(), nullable=False),
            sa.Column("ira_next_number", sa.Integer(), nullable=False),
            sa.Column("sequence_width", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )

    order_cols = {c["name"] for c in inspector.get_columns("orders")}
    if "production_order_id" not in order_cols:
        with op.batch_alter_table("orders", schema=None) as batch_op:
            batch_op.add_column(sa.Column("production_order_id", sa.String(length=120), nullable=True))
            batch_op.create_index("ix_orders_production_order_id", ["production_order_id"], unique=True)


def downgrade():
    inspector = sa.inspect(op.get_bind())
    order_cols = {c["name"] for c in inspector.get_columns("orders")}
    if "production_order_id" in order_cols:
        with op.batch_alter_table("orders", schema=None) as batch_op:
            try:
                batch_op.drop_index("ix_orders_production_order_id")
            except Exception:
                pass
            batch_op.drop_column("production_order_id")

    if "order_number_counters" in inspector.get_table_names():
        op.drop_table("order_number_counters")
