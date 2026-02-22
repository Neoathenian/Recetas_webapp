from __future__ import annotations

import json
import re
from typing import Sequence

from sqlalchemy import text
from sqlalchemy.orm import Session


def _slugify(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower())
    return normalized.strip("-") or "value"


def _decode_tags(raw_value: object) -> list[str]:
    if raw_value is None:
        return []
    if isinstance(raw_value, list):
        return [str(item).strip().lower() for item in raw_value if str(item).strip()]
    try:
        parsed = json.loads(str(raw_value))
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item).strip().lower() for item in parsed if str(item).strip()]


def find_theory_slug_by_name(session: Session, name: str, *, exclude_slug: str = "") -> str | None:
    label = (name or "").strip()
    if not label:
        return None
    normalized_exclude_slug = (exclude_slug or "").strip().lower()
    row = session.execute(
        text(
            """
            SELECT c.slug
            FROM app.theory_cards c
            JOIN app.theories t
              ON t.id = c.person_id
            WHERE LOWER(BTRIM(t.name)) = LOWER(BTRIM(:name))
              AND (:exclude_slug = '' OR c.slug <> :exclude_slug)
            ORDER BY c.slug
            LIMIT 1
            """
        ),
        {"name": label, "exclude_slug": normalized_exclude_slug},
    ).scalar_one_or_none()
    if row is None:
        return None
    slug = str(row).strip().lower()
    return slug or None


def ensure_theory_name_available(session: Session, name: str, *, exclude_slug: str = "") -> None:
    label = (name or "").strip()
    if not label:
        return
    conflict_slug = find_theory_slug_by_name(session, label, exclude_slug=exclude_slug)
    if conflict_slug:
        raise ValueError(f"Card name `{label}` is already used by `{conflict_slug}`.")


def ensure_theory_person(session: Session, name: str) -> int:
    label = (name or "").strip() or "Unknown"
    return int(
        session.execute(
            text(
                """
                INSERT INTO app.theories (name)
                VALUES (:name)
                RETURNING id
                """
            ),
            {"name": label},
        ).scalar_one()
    )


def ensure_theory_title(session: Session, title: str) -> int:
    label = (title or "").strip() or "Unassigned"
    return int(
        session.execute(
            text(
                """
                INSERT INTO app.theory_titles (code, label)
                VALUES (:code, :label)
                ON CONFLICT (label) DO UPDATE
                SET code = EXCLUDED.code,
                    updated_at = now()
                RETURNING id
                """
            ),
            {"code": _slugify(label), "label": label},
        ).scalar_one()
    )


def ensure_theory_taxonomy_schema(session: Session) -> None:
    session.execute(
        text(
            """
            DO $$
            BEGIN
              IF NOT EXISTS (
                SELECT 1
                FROM pg_indexes
                WHERE schemaname = 'app'
                  AND indexname = 'ux_theories_name_normalized'
              )
              AND NOT EXISTS (
                SELECT 1
                FROM (
                  SELECT LOWER(BTRIM(name)) AS normalized_name
                  FROM app.theories
                  WHERE NULLIF(BTRIM(name), '') IS NOT NULL
                  GROUP BY LOWER(BTRIM(name))
                  HAVING COUNT(*) > 1
                ) AS duplicate_names
              )
              THEN
                EXECUTE 'CREATE UNIQUE INDEX ux_theories_name_normalized '
                        'ON app.theories ((LOWER(BTRIM(name)))) '
                        'WHERE NULLIF(BTRIM(name), '''') IS NOT NULL';
              END IF;
            END$$;
            """
        )
    )


def ensure_theory_cards_refs(session: Session) -> None:
    _ = session
    return


def sync_theory_card_taxonomy(
    session: Session,
    *,
    person_id: int,
    title: str,
    tags: Sequence[str],
    ensure_title: bool = True,
) -> None:
    normalized_person_id = int(person_id or 0)
    title_label = (title or "").strip()
    if normalized_person_id <= 0 or not title_label:
        return

    if ensure_title:
        ensure_theory_title(session, title_label)

    deduped_tags: list[str] = []
    seen: set[str] = set()
    for raw_tag in tags:
        tag = str(raw_tag or "").strip().lower()
        if not tag or tag in seen:
            continue
        seen.add(tag)
        deduped_tags.append(tag)
    existing_rows = session.execute(
        text(
            """
            SELECT
                ppt.tag_id,
                LOWER(BTRIM(tg.label)) AS label
            FROM app.theory_person_tags ppt
            JOIN app.theory_tags tg
                ON tg.id = ppt.tag_id
            WHERE ppt.person_id = :person_id
            """
        ),
        {"person_id": normalized_person_id},
    ).mappings().all()
    existing_tag_id_by_label = {
        str(row.get("label") or "").strip().lower(): int(row.get("tag_id") or 0) for row in existing_rows
    }
    existing_labels = set(existing_tag_id_by_label.keys())
    requested_labels = set(deduped_tags)
    labels_to_add = [label for label in deduped_tags if label not in existing_labels]
    tag_ids_to_remove = [
        tag_id
        for label, tag_id in existing_tag_id_by_label.items()
        if label not in requested_labels and tag_id > 0
    ]

    if tag_ids_to_remove:
        session.execute(
            text(
                """
                DELETE FROM app.theory_person_tags
                WHERE person_id = :person_id
                  AND tag_id = ANY(:tag_ids)
                """
            ),
            {"person_id": normalized_person_id, "tag_ids": tag_ids_to_remove},
        )

    if not labels_to_add:
        return

    session.execute(
        text(
            """
            INSERT INTO app.theory_tags (code, label)
            VALUES (:code, :label)
            ON CONFLICT (label) DO UPDATE
            SET code = EXCLUDED.code,
                updated_at = now()
            """
        ),
        [{"code": _slugify(tag_label), "label": tag_label} for tag_label in labels_to_add],
    )
    session.execute(
        text(
            """
            INSERT INTO app.theory_person_tags (person_id, tag_id)
            SELECT :person_id, pt.id
            FROM app.theory_tags pt
            WHERE pt.label = ANY(:labels)
            ON CONFLICT (person_id, tag_id) DO NOTHING
            """
        ),
        {
            "person_id": normalized_person_id,
            "labels": labels_to_add,
        },
    )
