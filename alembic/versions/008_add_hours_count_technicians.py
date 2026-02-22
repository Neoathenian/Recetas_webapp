"""Add hours_count column to technicians.

Revision ID: 008_add_hours_count_technicians
Revises: 007_add_email_centers
Create Date: 2026-02-03
"""
from alembic import op
import sqlalchemy as sa

revision = "008_add_hours_count_technicians"
down_revision = "007_add_email_centers"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("technicians", sa.Column("hours_count", sa.Integer(), nullable=True), schema="app")


def downgrade():
    op.drop_column("technicians", "hours_count", schema="app")
