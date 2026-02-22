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


def find_people_slug_by_name(session: Session, name: str, *, exclude_slug: str = "") -> str | None:
    label = (name or "").strip()
    if not label:
        return None
    normalized_exclude_slug = (exclude_slug or "").strip().lower()
    row = session.execute(
        text(
            """
            SELECT c.slug
            FROM app.people_cards c
            JOIN app.people p
              ON p.id = c.person_id
            WHERE LOWER(BTRIM(p.name)) = LOWER(BTRIM(:name))
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


def ensure_people_name_available(session: Session, name: str, *, exclude_slug: str = "") -> None:
    label = (name or "").strip()
    if not label:
        return
    conflict_slug = find_people_slug_by_name(session, label, exclude_slug=exclude_slug)
    if conflict_slug:
        raise ValueError(f"Card name `{label}` is already used by `{conflict_slug}`.")


def ensure_people_person(session: Session, name: str) -> int:
    label = (name or "").strip() or "Unknown"
    return int(
        session.execute(
            text(
                """
                INSERT INTO app.people (name)
                VALUES (:name)
                RETURNING id
                """
            ),
            {"name": label},
        ).scalar_one()
    )


def ensure_people_title(session: Session, title: str) -> int:
    label = (title or "").strip() or "Unassigned"
    return int(
        session.execute(
            text(
                """
                INSERT INTO app.people_titles (code, label)
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


def ensure_people_taxonomy_schema(session: Session) -> None:
    session.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS people (
                id BIGSERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
    )
    session.execute(text("CREATE INDEX IF NOT EXISTS idx_people_name ON app.people(name)"))
    session.execute(
        text(
            """
            DO $$
            BEGIN
              IF NOT EXISTS (
                SELECT 1
                FROM pg_indexes
                WHERE schemaname = 'app'
                  AND indexname = 'ux_people_name_normalized'
              )
              AND NOT EXISTS (
                SELECT 1
                FROM (
                  SELECT LOWER(BTRIM(name)) AS normalized_name
                  FROM app.people
                  WHERE NULLIF(BTRIM(name), '') IS NOT NULL
                  GROUP BY LOWER(BTRIM(name))
                  HAVING COUNT(*) > 1
                ) AS duplicate_names
              )
              THEN
                EXECUTE 'CREATE UNIQUE INDEX ux_people_name_normalized '
                        'ON app.people ((LOWER(BTRIM(name)))) '
                        'WHERE NULLIF(BTRIM(name), '''') IS NOT NULL';
              END IF;
            END$$;
            """
        )
    )

    session.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS app.people_titles (
                id BIGSERIAL PRIMARY KEY,
                code TEXT NOT NULL,
                label TEXT NOT NULL UNIQUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
    )
    session.execute(text("CREATE INDEX IF NOT EXISTS idx_people_titles_code ON app.people_titles(code)"))

    session.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS app.people_tags (
                id BIGSERIAL PRIMARY KEY,
                code TEXT NOT NULL,
                label TEXT NOT NULL UNIQUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
    )
    session.execute(text("CREATE INDEX IF NOT EXISTS idx_people_tags_code ON app.people_tags(code)"))

    session.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS app.people_person_tags (
                person_id BIGINT NOT NULL REFERENCES app.people(id) ON UPDATE CASCADE ON DELETE CASCADE,
                tag_id BIGINT NOT NULL REFERENCES app.people_tags(id) ON UPDATE CASCADE ON DELETE RESTRICT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (person_id, tag_id)
            )
            """
        )
    )
    session.execute(text("CREATE INDEX IF NOT EXISTS idx_people_person_tags_tag_id ON app.people_person_tags(tag_id)"))


def ensure_people_cards_refs(session: Session) -> None:
    session.execute(text("ALTER TABLE app.people_cards ADD COLUMN IF NOT EXISTS person_id BIGINT"))
    session.execute(text("ALTER TABLE app.people_cards ADD COLUMN IF NOT EXISTS title_id BIGINT"))

    has_legacy_name = bool(
        session.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_schema = 'app'
                      AND table_name = 'people_cards'
                      AND column_name = 'name'
                )
                """
            )
        ).scalar_one()
    )
    has_legacy_title = bool(
        session.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_schema = 'app'
                      AND table_name = 'people_cards'
                      AND column_name = 'title'
                )
                """
            )
        ).scalar_one()
    )
    has_legacy_tags_json = bool(
        session.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_schema = 'app'
                      AND table_name = 'people_cards'
                      AND column_name = 'tags_json'
                )
                """
            )
        ).scalar_one()
    )

    if has_legacy_name:
        person_rows = session.execute(
            text(
                """
                SELECT slug, COALESCE(NULLIF(name, ''), slug) AS person_name
                FROM app.people_cards
                WHERE person_id IS NULL
                """
            )
        ).mappings().all()
    else:
        person_rows = session.execute(
            text(
                """
                SELECT slug, slug AS person_name
                FROM app.people_cards
                WHERE person_id IS NULL
                """
            )
        ).mappings().all()

    for row in person_rows:
        slug = str(row.get("slug") or "").strip().lower()
        if not slug:
            continue
        person_id = ensure_people_person(session, str(row.get("person_name") or "Unknown"))
        session.execute(
            text(
                """
                UPDATE app.people_cards
                SET person_id = :person_id
                WHERE slug = :slug
                """
            ),
            {"person_id": person_id, "slug": slug},
        )

    if has_legacy_title:
        title_rows = session.execute(
            text(
                """
                SELECT slug, COALESCE(NULLIF(title, ''), COALESCE(NULLIF(bucket, ''), 'Unassigned')) AS title
                FROM app.people_cards
                WHERE title_id IS NULL
                """
            )
        ).mappings().all()
    else:
        title_rows = session.execute(
            text(
                """
                SELECT slug, COALESCE(NULLIF(bucket, ''), 'Unassigned') AS title
                FROM app.people_cards
                WHERE title_id IS NULL
                """
            )
        ).mappings().all()

    for row in title_rows:
        slug = str(row.get("slug") or "").strip().lower()
        if not slug:
            continue
        title_id = ensure_people_title(session, str(row.get("title") or "Unassigned"))
        session.execute(
            text(
                """
                UPDATE app.people_cards
                SET title_id = :title_id
                WHERE slug = :slug
                """
            ),
            {"title_id": title_id, "slug": slug},
        )

    if has_legacy_tags_json and has_legacy_title:
        legacy_tag_rows = session.execute(
            text(
                """
                SELECT person_id, tags_json, COALESCE(NULLIF(title, ''), COALESCE(NULLIF(bucket, ''), 'Unassigned')) AS title
                FROM app.people_cards
                WHERE person_id IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1
                      FROM app.people_person_tags ppt
                      WHERE ppt.person_id = app.people_cards.person_id
                  )
                """
            )
        ).mappings().all()
    elif has_legacy_tags_json:
        legacy_tag_rows = session.execute(
            text(
                """
                SELECT person_id, tags_json, COALESCE(NULLIF(bucket, ''), 'Unassigned') AS title
                FROM app.people_cards
                WHERE person_id IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1
                      FROM app.people_person_tags ppt
                      WHERE ppt.person_id = app.people_cards.person_id
                  )
                """
            )
        ).mappings().all()
    else:
        legacy_tag_rows = []

    if legacy_tag_rows:
        for row in legacy_tag_rows:
            person_id_value = int(row.get("person_id") or 0)
            if person_id_value <= 0:
                continue
            sync_people_card_taxonomy(
                session,
                person_id=person_id_value,
                title=str(row.get("title") or "Unassigned"),
                tags=_decode_tags(row.get("tags_json")),
            )


def sync_people_card_taxonomy(
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
        ensure_people_title(session, title_label)

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
            FROM app.people_person_tags ppt
            JOIN app.people_tags tg
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
                DELETE FROM app.people_person_tags
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
            INSERT INTO app.people_tags (code, label)
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
            INSERT INTO app.people_person_tags (person_id, tag_id)
            SELECT :person_id, pt.id
            FROM app.people_tags pt
            WHERE pt.label = ANY(:labels)
            ON CONFLICT (person_id, tag_id) DO NOTHING
            """
        ),
        {
            "person_id": normalized_person_id,
            "labels": labels_to_add,
        },
    )
