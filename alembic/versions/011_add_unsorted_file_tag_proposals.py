"""Add unsorted file tag proposals.

Revision ID: 011_add_unsorted_file_tag_proposals
Revises: 010_add_unsorted_files_flow
Create Date: 2026-02-18
"""

from alembic import op

revision = "011_add_unsorted_file_tag_proposals"
down_revision = "010_add_unsorted_files_flow"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE SCHEMA IF NOT EXISTS app")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS app.unsorted_file_tag_proposals (
            id BIGSERIAL PRIMARY KEY,
            unsorted_file_id BIGINT NOT NULL REFERENCES app.unsorted_files(id) ON DELETE CASCADE,
            proposer_user_id BIGINT NOT NULL REFERENCES app."user"(id) ON UPDATE CASCADE ON DELETE CASCADE,
            tags_json TEXT NOT NULL DEFAULT '[]',
            note TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            reviewed_at TIMESTAMPTZ,
            reviewer_user_id BIGINT REFERENCES app."user"(id) ON UPDATE CASCADE ON DELETE SET NULL,
            review_note TEXT,
            CONSTRAINT chk_unsorted_tag_status CHECK (
                lower(status) IN ('pending', 'accepted', 'declined')
            ),
            UNIQUE (unsorted_file_id, proposer_user_id)
        )
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_unsorted_tag_proposals_file_status
        ON app.unsorted_file_tag_proposals(unsorted_file_id, status)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_unsorted_tag_proposals_proposer_file
        ON app.unsorted_file_tag_proposals(proposer_user_id, unsorted_file_id)
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS app.unsorted_file_tag_proposal_tags (
            proposal_id BIGINT NOT NULL REFERENCES app.unsorted_file_tag_proposals(id) ON DELETE CASCADE,
            tag_code TEXT NOT NULL,
            tag_label TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT pk_unsorted_file_tag_proposal_tags PRIMARY KEY (proposal_id, tag_code),
            CONSTRAINT chk_unsorted_file_tag_proposal_tag_code CHECK (btrim(tag_code) <> ''),
            CONSTRAINT chk_unsorted_file_tag_proposal_tag_label CHECK (btrim(tag_label) <> '')
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_unsorted_tag_proposal_tags_proposal
        ON app.unsorted_file_tag_proposal_tags(proposal_id)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_unsorted_tag_proposal_tags_code
        ON app.unsorted_file_tag_proposal_tags(tag_code)
        """
    )


def downgrade():
    op.execute("DROP TABLE IF EXISTS app.unsorted_file_tag_proposal_tags")
    op.execute("DROP TABLE IF EXISTS app.unsorted_file_tag_proposals")
