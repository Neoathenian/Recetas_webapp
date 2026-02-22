"""Add unsorted files workflow tables.

Revision ID: 010_add_unsorted_files_flow
Revises: 009_add_card_article_scope
Create Date: 2026-02-18
"""

from alembic import op

revision = "010_add_unsorted_files_flow"
down_revision = "009_add_card_article_scope"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE SCHEMA IF NOT EXISTS app")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS app.unsorted_files (
            id BIGSERIAL PRIMARY KEY,
            bucket TEXT NOT NULL,
            blob_path TEXT NOT NULL UNIQUE,
            file_name TEXT NOT NULL,
            original_path TEXT NOT NULL DEFAULT '',
            origin_text TEXT NOT NULL DEFAULT '',
            mime_type TEXT,
            size_bytes BIGINT NOT NULL DEFAULT 0,
            uploaded_by_user_id BIGINT REFERENCES app."user"(id) ON UPDATE CASCADE ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT chk_unsorted_files_size_bytes CHECK (size_bytes >= 0)
        )
        """
    )
    op.execute("ALTER TABLE app.unsorted_files ADD COLUMN IF NOT EXISTS original_path TEXT NOT NULL DEFAULT ''")
    op.execute("ALTER TABLE app.unsorted_files ADD COLUMN IF NOT EXISTS origin_text TEXT NOT NULL DEFAULT ''")
    op.execute("ALTER TABLE app.unsorted_files ADD COLUMN IF NOT EXISTS mime_type TEXT")
    op.execute("ALTER TABLE app.unsorted_files ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now()")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS app.unsorted_file_actions (
            id BIGSERIAL PRIMARY KEY,
            unsorted_file_id BIGINT NOT NULL REFERENCES app.unsorted_files(id) ON DELETE CASCADE,
            actor_user_id BIGINT NOT NULL REFERENCES app."user"(id) ON UPDATE CASCADE ON DELETE CASCADE,
            action_type TEXT NOT NULL,
            source_id BIGINT REFERENCES app.sources_cards(id) ON UPDATE CASCADE ON DELETE SET NULL,
            source_slug TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT chk_unsorted_file_action_type CHECK (
                lower(action_type) IN ('too_redacted', 'push_to_source', 'create_new_source', 'useless')
            ),
            UNIQUE (unsorted_file_id, actor_user_id)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS app.unsorted_file_push_proposals (
            id BIGSERIAL PRIMARY KEY,
            unsorted_file_id BIGINT NOT NULL REFERENCES app.unsorted_files(id) ON DELETE CASCADE,
            source_id BIGINT NOT NULL REFERENCES app.sources_cards(id) ON UPDATE CASCADE ON DELETE CASCADE,
            source_slug TEXT NOT NULL,
            proposer_user_id BIGINT NOT NULL REFERENCES app."user"(id) ON UPDATE CASCADE ON DELETE CASCADE,
            note TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            reviewed_at TIMESTAMPTZ,
            CONSTRAINT chk_unsorted_push_status CHECK (
                lower(status) IN ('pending', 'accepted', 'declined')
            ),
            UNIQUE (unsorted_file_id, source_id, proposer_user_id)
        )
        """
    )

    op.execute("CREATE INDEX IF NOT EXISTS idx_unsorted_files_created_at ON app.unsorted_files(created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_unsorted_files_uploaded_by ON app.unsorted_files(uploaded_by_user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_unsorted_actions_file_id ON app.unsorted_file_actions(unsorted_file_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_unsorted_actions_actor ON app.unsorted_file_actions(actor_user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_unsorted_actions_type ON app.unsorted_file_actions(action_type)")
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_unsorted_push_proposals_file_source
        ON app.unsorted_file_push_proposals(unsorted_file_id, source_id)
        """
    )


def downgrade():
    op.execute("DROP TABLE IF EXISTS app.unsorted_file_push_proposals")
    op.execute("DROP TABLE IF EXISTS app.unsorted_file_actions")
    op.execute("DROP TABLE IF EXISTS app.unsorted_files")
