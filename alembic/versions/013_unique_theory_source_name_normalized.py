"""Enforce unique normalized names for theories and sources.

Revision ID: 013_unique_theory_source_name_normalized
Revises: 012_unique_people_name_normalized
Create Date: 2026-02-20
"""

from alembic import op
from sqlalchemy import text

revision = "013_unique_theory_source_name_normalized"
down_revision = "012_unique_people_name_normalized"
branch_labels = None
depends_on = None


def _find_duplicate_theory_names(bind):
    return bind.execute(
        text(
            """
            WITH theory_rows AS (
                SELECT
                    id,
                    LOWER(BTRIM(name)) AS normalized_name
                FROM app.theories
                WHERE NULLIF(BTRIM(name), '') IS NOT NULL
            ),
            duplicate_names AS (
                SELECT
                    normalized_name,
                    COUNT(*) AS row_count,
                    ARRAY_AGG(id ORDER BY id) AS theory_ids
                FROM theory_rows
                GROUP BY normalized_name
                HAVING COUNT(*) > 1
            )
            SELECT
                d.normalized_name,
                d.row_count,
                d.theory_ids,
                COALESCE(
                    (
                        SELECT ARRAY_AGG(c.slug ORDER BY c.slug)
                        FROM app.theory_cards c
                        WHERE c.person_id = ANY(d.theory_ids)
                    ),
                    ARRAY[]::text[]
                ) AS card_slugs
            FROM duplicate_names d
            ORDER BY d.normalized_name
            LIMIT 10
            """
        )
    ).mappings().all()


def _find_duplicate_source_names(bind):
    return bind.execute(
        text(
            """
            WITH source_rows AS (
                SELECT
                    id,
                    slug,
                    LOWER(BTRIM(name)) AS normalized_name
                FROM app.sources_cards
                WHERE NULLIF(BTRIM(name), '') IS NOT NULL
            )
            SELECT
                normalized_name,
                COUNT(*) AS row_count,
                ARRAY_AGG(id ORDER BY id) AS source_ids,
                ARRAY_AGG(slug ORDER BY slug) AS source_slugs
            FROM source_rows
            GROUP BY normalized_name
            HAVING COUNT(*) > 1
            ORDER BY normalized_name
            LIMIT 10
            """
        )
    ).mappings().all()


def upgrade():
    bind = op.get_bind()

    duplicate_theory_rows = _find_duplicate_theory_names(bind)
    if duplicate_theory_rows:
        formatted = []
        for row in duplicate_theory_rows:
            normalized_name = str(row.get("normalized_name") or "")
            theory_ids = ", ".join(str(item) for item in (row.get("theory_ids") or []))
            card_slugs = ", ".join(str(item) for item in (row.get("card_slugs") or []))
            card_summary = card_slugs if card_slugs else "no cards"
            formatted.append(
                f"name=`{normalized_name}` theory_ids=[{theory_ids}] cards=[{card_summary}]"
            )
        sample = "; ".join(formatted)
        raise RuntimeError(
            "Cannot add unique theory-name index because duplicate normalized names already exist. "
            f"Resolve duplicates first. Sample conflicts: {sample}"
        )

    duplicate_source_rows = _find_duplicate_source_names(bind)
    if duplicate_source_rows:
        formatted = []
        for row in duplicate_source_rows:
            normalized_name = str(row.get("normalized_name") or "")
            source_ids = ", ".join(str(item) for item in (row.get("source_ids") or []))
            source_slugs = ", ".join(str(item) for item in (row.get("source_slugs") or []))
            source_summary = source_slugs if source_slugs else "no slugs"
            formatted.append(
                f"name=`{normalized_name}` source_ids=[{source_ids}] slugs=[{source_summary}]"
            )
        sample = "; ".join(formatted)
        raise RuntimeError(
            "Cannot add unique source-name index because duplicate normalized names already exist. "
            f"Resolve duplicates first. Sample conflicts: {sample}"
        )

    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_theories_name_normalized
        ON app.theories ((LOWER(BTRIM(name))))
        WHERE NULLIF(BTRIM(name), '') IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_sources_cards_name_normalized
        ON app.sources_cards ((LOWER(BTRIM(name))))
        WHERE NULLIF(BTRIM(name), '') IS NOT NULL
        """
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS app.ux_sources_cards_name_normalized")
    op.execute("DROP INDEX IF EXISTS app.ux_theories_name_normalized")
