"""add order assignments

Revision ID: f72c9b8e21b1
Revises: bdac138222e7
Create Date: 2026-04-09 18:40:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "f72c9b8e21b1"
down_revision = "bdac138222e7"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()

    if "order_assignments" not in tables:
        op.create_table(
            "order_assignments",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("order_code", sa.String(length=120), nullable=False),
            sa.Column("team_name", sa.String(length=255), nullable=False),
            sa.Column("operator_id", sa.Integer(), nullable=False),
            sa.Column("sequence_number", sa.Integer(), nullable=False),
            sa.Column("month_abbr", sa.String(length=8), nullable=False),
            sa.Column("year", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("linked_order_id", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["linked_order_id"], ["orders.id"]),
            sa.ForeignKeyConstraint(["operator_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("linked_order_id"),
            sa.UniqueConstraint("order_code"),
        )
        op.create_index(
            "ix_order_assignments_operator_id",
            "order_assignments",
            ["operator_id"],
            unique=False,
        )
        op.create_index(
            "ix_order_assignments_status",
            "order_assignments",
            ["status"],
            unique=False,
        )
        op.create_index(
            "ix_order_assignments_order_code",
            "order_assignments",
            ["order_code"],
            unique=True,
        )

    order_cols = {c["name"] for c in inspector.get_columns("orders")}
    if "assignment_id" not in order_cols:
        with op.batch_alter_table("orders", schema=None) as batch_op:
            batch_op.add_column(sa.Column("assignment_id", sa.Integer(), nullable=True))
            batch_op.create_index("ix_orders_assignment_id", ["assignment_id"], unique=True)
            batch_op.create_foreign_key(
                "fk_orders_assignment_id_order_assignments",
                "order_assignments",
                ["assignment_id"],
                ["id"],
            )


def downgrade():
    inspector = sa.inspect(op.get_bind())
    order_cols = {c["name"] for c in inspector.get_columns("orders")}
    if "assignment_id" in order_cols:
        with op.batch_alter_table("orders", schema=None) as batch_op:
            try:
                batch_op.drop_constraint("fk_orders_assignment_id_order_assignments", type_="foreignkey")
            except Exception:
                pass
            try:
                batch_op.drop_index("ix_orders_assignment_id")
            except Exception:
                pass
            batch_op.drop_column("assignment_id")

    if "order_assignments" in inspector.get_table_names():
        try:
            op.drop_index("ix_order_assignments_order_code", table_name="order_assignments")
        except Exception:
            pass
        try:
            op.drop_index("ix_order_assignments_status", table_name="order_assignments")
        except Exception:
            pass
        try:
            op.drop_index("ix_order_assignments_operator_id", table_name="order_assignments")
        except Exception:
            pass
        op.drop_table("order_assignments")
