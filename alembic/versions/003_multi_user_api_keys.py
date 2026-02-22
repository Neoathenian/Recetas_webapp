"""Support multiple API keys per user"""

from alembic import op
import sqlalchemy as sa


revision = "003_multi_user_api_keys"
down_revision = "002_add_user_api_key"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_api_key_new",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("key_hash", sa.String(length=128), nullable=False),
        sa.Column("key_prefix", sa.String(length=32), nullable=False),
        sa.Column("label", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("storage_uid", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=False),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("last_used_at", sa.TIMESTAMP(timezone=False), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["app_user.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("key_hash", name="uq_user_api_key_hash"),
        sa.UniqueConstraint("user_id", "key_prefix", name="ux_user_api_key_user_prefix"),
    )

    op.execute(
        """
        INSERT INTO user_api_key_new (user_id, key_hash, key_prefix, label, storage_uid, created_at, last_used_at)
        SELECT user_id, key_hash, key_prefix, '', storage_uid, created_at, last_used_at
        FROM user_api_key
        """
    )

    op.drop_table("user_api_key")
    op.rename_table("user_api_key_new", "user_api_key")

    op.create_index("ix_user_api_key_storage_uid", "user_api_key", ["storage_uid"], unique=False)
    op.create_index("ix_user_api_key_user", "user_api_key", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_user_api_key_user", table_name="user_api_key")
    op.drop_index("ix_user_api_key_storage_uid", table_name="user_api_key")

    op.create_table(
        "user_api_key_old",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("key_hash", sa.String(length=128), nullable=False),
        sa.Column("key_prefix", sa.String(length=32), nullable=False),
        sa.Column("storage_uid", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=False),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("last_used_at", sa.TIMESTAMP(timezone=False), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["app_user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
        sa.UniqueConstraint("key_hash", name="uq_user_api_key_hash"),
    )

    op.execute(
        """
        INSERT INTO user_api_key_old (user_id, key_hash, key_prefix, storage_uid, created_at, last_used_at)
        SELECT user_id, key_hash, key_prefix, storage_uid, created_at, last_used_at
        FROM (
            SELECT *,
                   ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY created_at DESC, id DESC) AS rn
            FROM user_api_key
        ) ranked
        WHERE ranked.rn = 1
        """
    )

    op.drop_table("user_api_key")
    op.rename_table("user_api_key_old", "user_api_key")

    op.create_index("ix_user_api_key_storage_uid", "user_api_key", ["storage_uid"], unique=False)
