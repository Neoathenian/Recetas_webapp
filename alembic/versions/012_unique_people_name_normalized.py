"""Enforce unique normalized names for app.people.

Revision ID: 012_unique_people_name_normalized
Revises: 011_add_unsorted_file_tag_proposals
Create Date: 2026-02-20
"""

from alembic import op
from sqlalchemy import text

revision = "012_unique_people_name_normalized"
down_revision = "011_add_unsorted_file_tag_proposals"
branch_labels = None
depends_on = None


def _find_duplicate_people_names(bind):
    return bind.execute(
        text(
            """
            WITH people_rows AS (
                SELECT
                    id,
                    LOWER(BTRIM(name)) AS normalized_name
                FROM app.people
                WHERE NULLIF(BTRIM(name), '') IS NOT NULL
            ),
            duplicate_names AS (
                SELECT
                    normalized_name,
                    COUNT(*) AS row_count,
                    ARRAY_AGG(id ORDER BY id) AS person_ids
                FROM people_rows
                GROUP BY normalized_name
                HAVING COUNT(*) > 1
            )
            SELECT
                d.normalized_name,
                d.row_count,
                d.person_ids,
                COALESCE(
                    (
                        SELECT ARRAY_AGG(c.slug ORDER BY c.slug)
                        FROM app.people_cards c
                        WHERE c.person_id = ANY(d.person_ids)
                    ),
                    ARRAY[]::text[]
                ) AS card_slugs
            FROM duplicate_names d
            ORDER BY d.normalized_name
            LIMIT 10
            """
        )
    ).mappings().all()


def upgrade():
    bind = op.get_bind()
    duplicate_rows = _find_duplicate_people_names(bind)
    if duplicate_rows:
        formatted = []
        for row in duplicate_rows:
            normalized_name = str(row.get("normalized_name") or "")
            person_ids = ", ".join(str(item) for item in (row.get("person_ids") or []))
            card_slugs = ", ".join(str(item) for item in (row.get("card_slugs") or []))
            card_summary = card_slugs if card_slugs else "no cards"
            formatted.append(
                f"name=`{normalized_name}` person_ids=[{person_ids}] cards=[{card_summary}]"
            )
        sample = "; ".join(formatted)
        raise RuntimeError(
            "Cannot add unique people-name index because duplicate normalized names already exist. "
            f"Resolve duplicates first. Sample conflicts: {sample}"
        )

    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_people_name_normalized
        ON app.people ((LOWER(BTRIM(name))))
        WHERE NULLIF(BTRIM(name), '') IS NOT NULL
        """
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS app.ux_people_name_normalized")
