"""Add log_date tracking column to ecos_record"""

from alembic import op
import sqlalchemy as sa


revision = "004_add_log_date_to_ecos_record"
down_revision = "003_multi_user_api_keys"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ecos_record",
        sa.Column("log_date", sa.Date(), nullable=True),
        schema="app",
    )

    op.execute("UPDATE app.ecos_record SET log_date = date")

    op.alter_column(
        "ecos_record",
        "log_date",
        schema="app",
        existing_type=sa.Date(),
        nullable=False,
        server_default=sa.text("CURRENT_DATE"),
    )

    op.create_index(
        "idx_ecos_record_log_date",
        "ecos_record",
        ["log_date"],
        schema="app",
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "idx_ecos_record_log_date",
        table_name="ecos_record",
        schema="app",
    )
    op.drop_column("ecos_record", "log_date", schema="app")
