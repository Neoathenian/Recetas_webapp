"""Add unique constraint to radiologist_assignments to prevent duplicates.

Revision ID: 005_unique_assignments
Revises: 004_add_log_date_to_ecos_record
Create Date: 2026-01-13

"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "005_unique_assignments"
down_revision = "004_add_log_date_to_ecos_record"
branch_labels = None
depends_on = None


def upgrade():
    # First, delete duplicate rows keeping only one per unique combination
    op.execute(
        """
        DELETE FROM app.radiologist_assignments a
        USING app.radiologist_assignments b
        WHERE a.ctid < b.ctid
          AND a.date = b.date
          AND a.technician_id = b.technician_id
          AND a.center_id IS NOT DISTINCT FROM b.center_id
          AND a.echo_type = b.echo_type
          AND a.reporting_radiologist_id = b.reporting_radiologist_id
        """
    )
    
    # Add unique constraint
    op.create_unique_constraint(
        "uq_radiologist_assignments_unique_assignment",
        "radiologist_assignments",
        ["date", "technician_id", "center_id", "echo_type", "reporting_radiologist_id"],
        schema="app",
    )


def downgrade():
    op.drop_constraint(
        "uq_radiologist_assignments_unique_assignment",
        "radiologist_assignments",
        schema="app",
    )
