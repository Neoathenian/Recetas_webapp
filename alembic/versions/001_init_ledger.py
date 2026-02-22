from alembic import op
import sqlalchemy as sa

revision = "0001_init_ledger"
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    op.create_table(
        "app_user",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("oauth_sub", sa.String(255), nullable=False, unique=True, index=True),
    )

    op.create_table(
        "payment",
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("provider_event_id", sa.String(255), primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("amount_cents", sa.BigInteger, nullable=False),
        sa.Column("credits_granted", sa.BigInteger, nullable=False),
        sa.Column("status", sa.String(32), nullable=False, index=True),
    )

    op.create_table(
        "credit_ledger",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("delta", sa.BigInteger, nullable=False),
        sa.Column("reason", sa.String(255), nullable=False),
        sa.Column("source_type", sa.String(64), nullable=False),
        sa.Column("source_id", sa.String(255), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=False), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("source_type", "source_id", name="uq_ledger_source"),
    )
    op.create_index("ix_ledger_user_created", "credit_ledger", ["user_id", "created_at"])

    op.create_table(
        "user_balance",
        sa.Column("user_id", sa.Integer, sa.ForeignKey("app_user.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("balance", sa.BigInteger, nullable=False),
    )

def downgrade():
    op.drop_table("user_balance")
    op.drop_index("ix_ledger_user_created", table_name="credit_ledger")
    op.drop_table("credit_ledger")
    op.drop_table("payment")
    op.drop_table("app_user")
