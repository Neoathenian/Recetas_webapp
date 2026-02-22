"""Add card_article scope to people_change_proposals.

Revision ID: 009_add_card_article_scope
Revises: 008_add_hours_count_technicians
Create Date: 2026-02-15
"""
from alembic import op

revision = "009_add_card_article_scope"
down_revision = "008_add_hours_count_technicians"
branch_labels = None
depends_on = None


def upgrade():
    # Drop the existing CHECK constraint and recreate with the new scope value
    op.execute(
        """
        ALTER TABLE app.people_change_proposals
        DROP CONSTRAINT IF EXISTS chk_people_change_scope
        """
    )
    op.execute(
        """
        ALTER TABLE app.people_change_proposals
        ADD CONSTRAINT chk_people_change_scope
        CHECK (lower(proposal_scope) IN ('article', 'card', 'description', 'card_article'))
        """
    )


def downgrade():
    # Revert to the old constraint (note: this will fail if card_article rows exist)
    op.execute(
        """
        ALTER TABLE app.people_change_proposals
        DROP CONSTRAINT IF EXISTS chk_people_change_scope
        """
    )
    op.execute(
        """
        ALTER TABLE app.people_change_proposals
        ADD CONSTRAINT chk_people_change_scope
        CHECK (lower(proposal_scope) IN ('article', 'card', 'description'))
        """
    )
