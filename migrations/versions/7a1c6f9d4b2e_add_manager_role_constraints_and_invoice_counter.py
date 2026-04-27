"""add manager role constraints and invoice counter

Revision ID: 7a1c6f9d4b2e
Revises: e8a1b93c7f4d
Create Date: 2026-04-20 11:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "7a1c6f9d4b2e"
down_revision = "e8a1b93c7f4d"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()

    if "order_number_counters" in tables:
        counter_columns = {c["name"] for c in inspector.get_columns("order_number_counters")}
        if "invoice_next_number" not in counter_columns:
            with op.batch_alter_table("order_number_counters", schema=None) as batch_op:
                batch_op.add_column(
                    sa.Column("invoice_next_number", sa.Integer(), nullable=False, server_default="1")
                )
            op.execute(
                "UPDATE order_number_counters SET invoice_next_number = 1 WHERE invoice_next_number IS NULL OR invoice_next_number <= 0"
            )

    if "users" in tables:
        op.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_users_single_admin_role ON users(role) WHERE role = 'admin'"
        )
        op.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_users_single_manager_role ON users(role) WHERE role = 'manager'"
        )


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()

    if "users" in tables:
        op.execute("DROP INDEX IF EXISTS uq_users_single_admin_role")
        op.execute("DROP INDEX IF EXISTS uq_users_single_manager_role")

    if "order_number_counters" in tables:
        counter_columns = {c["name"] for c in inspector.get_columns("order_number_counters")}
        if "invoice_next_number" in counter_columns:
            with op.batch_alter_table("order_number_counters", schema=None) as batch_op:
                batch_op.drop_column("invoice_next_number")
