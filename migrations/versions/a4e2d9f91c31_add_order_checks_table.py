"""add order checks table

Revision ID: a4e2d9f91c31
Revises: bdac138222e7
Create Date: 2026-03-28 10:25:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "a4e2d9f91c31"
down_revision = "bdac138222e7"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()
    if "order_checks" in tables:
        return

    op.create_table(
        "order_checks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("parsed_json", sa.Text(), nullable=True),
        sa.Column("dynamic_design_fields", sa.Text(), nullable=True),
        sa.Column("dynamic_responses", sa.Text(), nullable=True),
        sa.Column("current_page", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("order_id_verified", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("enquiry_date_verified", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("design_checked", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("logos_checked", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("gender_checked", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("sleeve_type_checked", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("names_numbers_sizes_checked", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("quantity_checked", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("approved", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], name="fk_order_checks_order_id_orders"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_id", name="uq_order_checks_order_id"),
    )
    op.create_index("ix_order_checks_order_id", "order_checks", ["order_id"], unique=False)


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()
    if "order_checks" not in tables:
        return
    op.drop_index("ix_order_checks_order_id", table_name="order_checks")
    op.drop_table("order_checks")
