"""Add timesheet_sends table to track email sends.

Revision ID: 006_timesheet_sends
Revises: 005_unique_assignments
Create Date: 2026-01-13
"""
from alembic import op
import sqlalchemy as sa

revision = "006_timesheet_sends"
down_revision = "005_unique_assignments"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "timesheet_sends",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("center_id", sa.BigInteger(), nullable=False),
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column("sent_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("modified", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.ForeignKeyConstraint(
            ["center_id"],
            ["app.centers.id"],
            onupdate="CASCADE",
            ondelete="RESTRICT",
        ),
        schema="app",
    )
    op.create_index(
        "ix_timesheet_sends_center_month",
        "timesheet_sends",
        ["center_id", "month"],
        schema="app",
    )


def downgrade():
    op.drop_index("ix_timesheet_sends_center_month", table_name="timesheet_sends", schema="app")
    op.drop_table("timesheet_sends", schema="app")
