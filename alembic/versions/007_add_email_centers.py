"""Add email column to centers.

Revision ID: 007_add_email_centers
Revises: 006_timesheet_sends
Create Date: 2026-01-13
"""
from alembic import op
import sqlalchemy as sa

revision = "007_add_email_centers"
down_revision = "006_timesheet_sends"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("centers", sa.Column("email", sa.Text(), nullable=True), schema="app")


def downgrade():
    op.drop_column("centers", "email", schema="app")
