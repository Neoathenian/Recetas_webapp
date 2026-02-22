"""add table for user api keys"""

from alembic import op
import sqlalchemy as sa


revision = "002_add_user_api_key"
down_revision = "001_init_ledger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_api_key",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("key_hash", sa.String(length=128), nullable=False),
        sa.Column("key_prefix", sa.String(length=32), nullable=False),
        sa.Column("storage_uid", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=False), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("last_used_at", sa.TIMESTAMP(timezone=False), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["app_user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
        sa.UniqueConstraint("key_hash", name="uq_user_api_key_hash"),
    )
    op.create_index("ix_user_api_key_storage_uid", "user_api_key", ["storage_uid"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_user_api_key_storage_uid", table_name="user_api_key")
    op.drop_table("user_api_key")
