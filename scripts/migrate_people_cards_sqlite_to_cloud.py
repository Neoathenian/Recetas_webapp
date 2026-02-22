#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:  # pragma: no cover - fallback when python-dotenv is unavailable
    def load_dotenv(*_args, **_kwargs):  # type: ignore[no-redef]
        return False
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import src.gcs_storage as gcs_storage
from src.db import session_scope
from src.employees import ensure_user
from src.gcs_storage import blob_exists, bucket_name, media_path, upload_bytes
from src.people_proposal_diffs import normalize_proposal_scope

_IMAGE_PATH_RE = re.compile(r"/images/[A-Za-z0-9._/\-]+")
_VALID_STATUS = {"pending", "accepted", "declined", "reported"}
_DEFAULT_MEDIA_PREFIX = "the-list/migration/legacy-images"


@dataclass
class MigrationStats:
    cards_upserted: int = 0
    articles_upserted: int = 0
    people_synced: int = 0
    tags_synced: int = 0
    users_ensured: int = 0
    privileges_synced: int = 0
    proposals_upserted: int = 0
    events_upserted: int = 0
    article_diffs_upserted: int = 0
    card_diffs_upserted: int = 0
    images_uploaded: int = 0
    images_reused: int = 0
    warnings: list[str] = field(default_factory=list)

    def warn(self, message: str) -> None:
        self.warnings.append(message)


def _has_sqlite_table(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ? LIMIT 1",
        (name,),
    ).fetchone()
    return row is not None


def _rows(conn: sqlite3.Connection, query: str) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(query).fetchall()]


def _parse_json_list(raw_value: Any) -> list[str]:
    if raw_value is None:
        return []
    if isinstance(raw_value, list):
        source = raw_value
    else:
        try:
            source = json.loads(str(raw_value))
        except json.JSONDecodeError:
            return []
    if not isinstance(source, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in source:
        tag = str(item or "").strip().lower()
        if not tag or tag in seen:
            continue
        seen.add(tag)
        result.append(tag)
    return result


def _parse_timestamp(raw_value: Any) -> datetime | None:
    value = str(raw_value or "").strip()
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _normalize_status(raw_value: Any) -> str:
    value = str(raw_value or "").strip().lower()
    if value == "rejected":
        return "declined"
    if value in _VALID_STATUS:
        return value
    return "pending"


def _slugify(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower())
    return normalized.strip("-") or "value"


def _upsert_person(session, name: str) -> int:
    person_name = str(name or "").strip() or "Unknown"
    existing_id = session.execute(
        text(
            """
            SELECT id
            FROM app.people
            WHERE lower(name) = lower(:name)
            ORDER BY id ASC
            LIMIT 1
            """
        ),
        {"name": person_name},
    ).scalar_one_or_none()
    if existing_id is not None:
        return int(existing_id)
    return int(
        session.execute(
            text(
                """
                INSERT INTO app.people (name)
                VALUES (:name)
                RETURNING id
                """
            ),
            {"name": person_name},
        ).scalar_one()
    )


def _upsert_title(session, title: str) -> int:
    label = str(title or "").strip() or "Unassigned"
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


def _sync_person_tags(session, *, person_id: int, tags: list[str]) -> int:
    normalized_person_id = int(person_id or 0)
    if normalized_person_id <= 0:
        return 0
    deduped_tags: list[str] = []
    seen: set[str] = set()
    for raw_tag in tags:
        tag = str(raw_tag or "").strip().lower()
        if not tag or tag in seen:
            continue
        seen.add(tag)
        deduped_tags.append(tag)

    session.execute(
        text("DELETE FROM app.people_person_tags WHERE person_id = :person_id"),
        {"person_id": normalized_person_id},
    )
    for tag in deduped_tags:
        tag_id = int(
            session.execute(
                text(
                    """
                    INSERT INTO app.people_tags (code, label)
                    VALUES (:code, :label)
                    ON CONFLICT (label) DO UPDATE
                    SET code = EXCLUDED.code,
                        updated_at = now()
                    RETURNING id
                    """
                ),
                {"code": _slugify(tag), "label": tag},
            ).scalar_one()
        )
        session.execute(
            text(
                """
                INSERT INTO app.people_person_tags (person_id, tag_id)
                VALUES (:person_id, :tag_id)
                ON CONFLICT (person_id, tag_id) DO NOTHING
                """
            ),
            {"person_id": normalized_person_id, "tag_id": tag_id},
        )
    return len(deduped_tags)


def _ensure_target_schema(session) -> None:
    session.execute(text("CREATE SCHEMA IF NOT EXISTS app"))
    session.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS app."user" (
              id         BIGSERIAL PRIMARY KEY,
              name     TEXT NOT NULL,
              username   TEXT,
              email     TEXT NOT NULL UNIQUE,
              is_active  BOOLEAN NOT NULL DEFAULT TRUE
            )
            """
        )
    )
    session.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS app.user_privileges (
              email            TEXT PRIMARY KEY REFERENCES app."user"(email) ON UPDATE CASCADE ON DELETE CASCADE,
              base_user         BOOLEAN NOT NULL DEFAULT FALSE,
              reviewer          BOOLEAN NOT NULL DEFAULT FALSE,
              admin             BOOLEAN NOT NULL DEFAULT FALSE,
              creator           BOOLEAN NOT NULL DEFAULT FALSE
            )
            """
        )
    )
    session.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS app.people (
              id          BIGSERIAL PRIMARY KEY,
              name        TEXT NOT NULL,
              created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
              updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
    )
    session.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS app.people_titles (
              id          BIGSERIAL PRIMARY KEY,
              code        TEXT NOT NULL,
              label       TEXT NOT NULL UNIQUE,
              created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
              updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
    )
    session.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS app.people_tags (
              id          BIGSERIAL PRIMARY KEY,
              code        TEXT NOT NULL,
              label       TEXT NOT NULL UNIQUE,
              created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
              updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
    )
    session.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS app.people_cards (
              id          BIGSERIAL PRIMARY KEY,
              slug        TEXT NOT NULL UNIQUE,
              person_id   BIGINT NOT NULL REFERENCES app.people(id) ON UPDATE CASCADE ON DELETE CASCADE,
              bucket      TEXT NOT NULL,
              title_id    BIGINT NOT NULL REFERENCES app.people_titles(id) ON UPDATE CASCADE ON DELETE RESTRICT,
              image_url   TEXT NOT NULL,
              created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
              updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
    )
    session.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS app.people_articles (
              id          BIGSERIAL PRIMARY KEY,
              person_slug TEXT NOT NULL UNIQUE REFERENCES app.people_cards(slug) ON UPDATE CASCADE ON DELETE CASCADE,
              markdown    TEXT NOT NULL,
              created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
              updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
    )
    session.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS app.people_person_tags (
              person_id   BIGINT NOT NULL REFERENCES app.people(id) ON UPDATE CASCADE ON DELETE CASCADE,
              tag_id      BIGINT NOT NULL REFERENCES app.people_tags(id) ON UPDATE CASCADE ON DELETE RESTRICT,
              created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
              PRIMARY KEY (person_id, tag_id)
            )
            """
        )
    )
    session.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS app.people_change_proposals (
              id                 BIGSERIAL PRIMARY KEY,
              person_slug        TEXT NOT NULL REFERENCES app.people_cards(slug) ON UPDATE CASCADE ON DELETE RESTRICT,
              person_id          BIGINT NOT NULL REFERENCES app.people(id) ON UPDATE CASCADE ON DELETE CASCADE,
              proposer_user_id   BIGINT NOT NULL REFERENCES app."user"(id) ON UPDATE CASCADE ON DELETE RESTRICT,
              proposal_scope     TEXT NOT NULL DEFAULT 'article',
              base_payload       TEXT NOT NULL,
              proposed_payload   TEXT NOT NULL,
              note               TEXT,
              status             TEXT NOT NULL DEFAULT 'pending',
              created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
              reviewed_at        TIMESTAMPTZ,
              reviewer_user_id   BIGINT REFERENCES app."user"(id) ON UPDATE CASCADE ON DELETE RESTRICT,
              review_note        TEXT,
              report_triggered   INTEGER NOT NULL DEFAULT 0
            )
            """
        )
    )
    session.execute(text("CREATE INDEX IF NOT EXISTS idx_people_change_proposals_person_id ON app.people_change_proposals(person_id)"))
    session.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS app.people_change_events (
              id            BIGSERIAL PRIMARY KEY,
              proposal_id   BIGINT NOT NULL REFERENCES app.people_change_proposals(id) ON DELETE CASCADE,
              event_type    TEXT NOT NULL,
              actor_user_id BIGINT REFERENCES app."user"(id) ON UPDATE CASCADE ON DELETE SET NULL,
              notes         TEXT,
              payload_json  TEXT NOT NULL DEFAULT '{}',
              created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
    )


def _upsert_diff_payload_qualified(
    session,
    *,
    proposal_id: int,
    person_id: int,
    scope: str,
    base_payload: str,
    proposed_payload: str,
    base_image_url: str,
    proposed_image_url: str,
) -> None:
    normalized_scope = normalize_proposal_scope(scope)
    normalized_person_id = int(person_id or 0)
    if normalized_person_id <= 0:
        raise ValueError(f"Could not resolve person_id for proposal_id={int(proposal_id)}")

    def _merge_card_payload_with_image(raw_payload: str, image_url: str) -> str:
        parsed: dict[str, Any] = {}
        try:
            parsed_candidate = json.loads(str(raw_payload or "").strip())
            if isinstance(parsed_candidate, dict):
                parsed = dict(parsed_candidate)
        except json.JSONDecodeError:
            parsed = {}
        merged_payload = {
            "name": str(parsed.get("name") or "").strip(),
            "title": str(parsed.get("title") or parsed.get("bucket") or "").strip(),
            "tags": [
                str(tag).strip().lower()
                for tag in (parsed.get("tags") if isinstance(parsed.get("tags"), list) else [])
                if str(tag).strip()
            ],
            "image_url": str(image_url or "").strip() or str(parsed.get("image_url") or "").strip(),
        }
        return json.dumps(merged_payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))

    base_payload_value = str(base_payload or "")
    proposed_payload_value = str(proposed_payload or "")
    if normalized_scope == "card":
        base_payload_value = _merge_card_payload_with_image(base_payload_value, str(base_image_url or ""))
        proposed_payload_value = _merge_card_payload_with_image(proposed_payload_value, str(proposed_image_url or ""))

    session.execute(
        text(
            """
            UPDATE app.people_change_proposals
            SET person_id = :person_id,
                proposal_scope = :proposal_scope,
                base_payload = :base_payload,
                proposed_payload = :proposed_payload
            WHERE id = :proposal_id
            """
        ),
        {
            "proposal_id": int(proposal_id),
            "person_id": normalized_person_id,
            "proposal_scope": normalized_scope,
            "base_payload": base_payload_value,
            "proposed_payload": proposed_payload_value,
        },
    )


def _resolve_local_image_path(raw_url: str, project_root: Path) -> Path | None:
    value = str(raw_url or "").strip()
    if not value:
        return None
    if value.startswith("/images/"):
        rel = value[len("/images/") :].lstrip("/")
        candidate = project_root / "images" / rel
        return candidate if candidate.is_file() else None
    candidate = Path(value).expanduser()
    if candidate.is_file():
        return candidate
    if not candidate.is_absolute():
        from_root = (project_root / value.lstrip("/")).resolve()
        if from_root.is_file():
            return from_root
    return None


def _rewrite_image_paths_in_text(
    text_value: str,
    *,
    transform_image_url,
) -> str:
    if not text_value or "/images/" not in text_value:
        return text_value

    def _replace(match: re.Match[str]) -> str:
        return transform_image_url(match.group(0))

    return _IMAGE_PATH_RE.sub(_replace, text_value)


def _rewrite_payload_json(raw_payload: str, *, transform_image_url) -> str:
    payload_text = str(raw_payload or "").strip()
    if not payload_text:
        return "{}"
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError:
        return _rewrite_image_paths_in_text(payload_text, transform_image_url=transform_image_url)

    def _rewrite(value: Any) -> Any:
        if isinstance(value, dict):
            rewritten: dict[str, Any] = {}
            for key, item in value.items():
                if isinstance(item, str) and key in {"image_url", "base_image_url", "proposed_image_url"}:
                    rewritten[key] = transform_image_url(item)
                else:
                    rewritten[key] = _rewrite(item)
            return rewritten
        if isinstance(value, list):
            return [_rewrite(item) for item in value]
        if isinstance(value, str):
            return _rewrite_image_paths_in_text(value, transform_image_url=transform_image_url)
        return value

    rewritten_payload = _rewrite(payload)
    return json.dumps(rewritten_payload, ensure_ascii=True)


def _collect_unique_users(
    local_privileges: list[dict[str, Any]],
    proposals: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> list[tuple[str, str]]:
    users: list[tuple[str, str]] = []
    seen: set[str] = set()

    def _add(email: Any, name_hint: Any) -> None:
        email_value = str(email or "").strip().lower()
        if not email_value or email_value in seen:
            return
        seen.add(email_value)
        display = str(name_hint or "").strip() or email_value.split("@", 1)[0]
        users.append((email_value, display))

    for row in local_privileges:
        _add(row.get("email"), None)
    for row in proposals:
        _add(row.get("proposer_email"), row.get("proposer_name"))
        _add(row.get("reviewer_email"), None)
    for row in events:
        _add(row.get("actor_email"), row.get("actor_name"))
    return users


def run_migration(args: argparse.Namespace) -> MigrationStats:
    project_root = Path(__file__).resolve().parents[1]
    sqlite_path = Path(args.sqlite_path).expanduser().resolve()
    if not sqlite_path.is_file():
        raise FileNotFoundError(f"SQLite source database not found: {sqlite_path}")

    env_path = Path(args.env_file).expanduser().resolve()
    if env_path.is_file():
        load_dotenv(env_path, override=True)
    _ensure_tls_cert_bundle()

    stats = MigrationStats()

    resolved_bucket = (
        (args.bucket_name or "").strip()
        or str(os.getenv("BUCKET_NAME") or "").strip()
        or str(os.getenv("API_STORAGE_BUCKET") or "").strip()
        or gcs_storage.DEFAULT_BUCKET
    )
    gcs_storage.DEFAULT_BUCKET = resolved_bucket

    print(f"[migration] sqlite source: {sqlite_path}", flush=True)
    conn = sqlite3.connect(str(sqlite_path))
    conn.row_factory = sqlite3.Row
    try:
        cards = _rows(conn, "SELECT * FROM people_cards")
        proposals = (
            _rows(conn, "SELECT * FROM people_change_proposals ORDER BY id")
            if _has_sqlite_table(conn, "people_change_proposals")
            else []
        )
        events = (
            _rows(conn, "SELECT * FROM people_change_events ORDER BY id")
            if _has_sqlite_table(conn, "people_change_events")
            else []
        )
        local_privileges = (
            _rows(conn, "SELECT * FROM local_user_privileges")
            if _has_sqlite_table(conn, "local_user_privileges")
            else []
        )
    finally:
        conn.close()
    print(
        f"[migration] loaded sqlite rows: cards={len(cards)} proposals={len(proposals)} events={len(events)} local_privileges={len(local_privileges)}",
        flush=True,
    )

    media_bucket = resolved_bucket
    media_prefix = (args.media_prefix or _DEFAULT_MEDIA_PREFIX).strip("/ ")
    image_url_cache: dict[str, str] = {}

    def transform_image_url(raw_url: Any) -> str:
        original = str(raw_url or "").strip()
        if not original:
            return ""
        if original in image_url_cache:
            return image_url_cache[original]
        if original.startswith("/media/"):
            image_url_cache[original] = original
            return original
        if original.startswith("http://") or original.startswith("https://"):
            image_url_cache[original] = original
            return original

        local_path = _resolve_local_image_path(original, project_root=project_root)
        if local_path is None:
            stats.warn(f"Could not resolve image path for `{original}`; keeping original URL.")
            image_url_cache[original] = original
            return original

        payload = local_path.read_bytes()
        digest = hashlib.sha1(payload).hexdigest()[:16]
        filename = f"{digest}-{local_path.name}"
        blob_name = f"{media_prefix}/{filename}"
        content_type = mimetypes.guess_type(str(local_path))[0] or "application/octet-stream"

        if args.skip_bucket_upload:
            stats.images_reused += 1
        elif args.dry_run:
            stats.images_reused += 1
        else:
            if blob_exists(blob_name):
                stats.images_reused += 1
            else:
                upload_bytes(payload, blob_name, content_type=content_type, cache_seconds=3600)
                stats.images_uploaded += 1

        mapped = media_path(blob_name)
        image_url_cache[original] = mapped
        return mapped

    user_id_by_email: dict[str, int] = {}

    print("[migration] connecting to Cloud SQL and ensuring target schema...", flush=True)
    with session_scope() as session:
        _ensure_target_schema(session)
        print("[migration] target schema ensured.", flush=True)

        if args.replace_existing:
            print("[migration] replacing existing target data...", flush=True)
            session.execute(text("DELETE FROM app.people_change_events"))
            session.execute(text("DELETE FROM app.people_change_proposals"))
            session.execute(text("DELETE FROM app.people_person_tags"))
            session.execute(text("DELETE FROM app.people_articles"))
            session.execute(text("DELETE FROM app.people_cards"))
            session.execute(text("DELETE FROM app.people_tags"))
            session.execute(text("DELETE FROM app.people_titles"))
            session.execute(text("DELETE FROM app.people"))
            print("[migration] existing target data cleared.", flush=True)

        for email, display_name in _collect_unique_users(local_privileges, proposals, events):
            user_id, resolved_email, _ = ensure_user(
                session,
                user_identifier=email,
                display_name=display_name,
            )
            user_id_by_email[resolved_email.lower()] = int(user_id)
            stats.users_ensured += 1
        print(f"[migration] ensured users: {stats.users_ensured}", flush=True)

        for row in local_privileges:
            email = str(row.get("email") or "").strip().lower()
            if not email:
                continue
            user_id, resolved_email, _ = ensure_user(
                session,
                user_identifier=email,
                display_name=email.split("@", 1)[0],
            )
            user_id_by_email[resolved_email.lower()] = int(user_id)
            enabled = bool(int(row.get("user_enabled") or 0))
            programmer = bool(int(row.get("programmer") or 0))
            session.execute(
                text(
                    """
                    INSERT INTO app.user_privileges (email, base_user, reviewer, admin, creator)
                    VALUES (:email, :base_user, :reviewer, :admin, :creator)
                    ON CONFLICT (email) DO UPDATE
                    SET base_user = app.user_privileges.base_user OR EXCLUDED.base_user,
                        reviewer = app.user_privileges.reviewer OR EXCLUDED.reviewer,
                        admin = app.user_privileges.admin OR EXCLUDED.admin,
                        creator = app.user_privileges.creator OR EXCLUDED.creator
                    """
                ),
                {
                    "email": resolved_email,
                    "base_user": enabled,
                    "reviewer": programmer,
                    "admin": programmer,
                    "creator": programmer,
                },
            )
            session.execute(
                text(
                    """
                    UPDATE app."user"
                    SET is_active = :is_active
                    WHERE lower(email) = lower(:email)
                    """
                ),
                {"is_active": enabled, "email": resolved_email},
            )
            stats.privileges_synced += 1
        print(f"[migration] synced privileges rows: {stats.privileges_synced}", flush=True)

        person_id_by_slug: dict[str, int] = {}
        for idx, row in enumerate(cards, start=1):
            slug = _slugify(str(row.get("slug") or ""))
            name = str(row.get("name") or "").strip() or slug
            title = str(row.get("bucket") or "").strip() or "Unassigned"
            markdown = _rewrite_image_paths_in_text(
                str(row.get("markdown") or ""),
                transform_image_url=transform_image_url,
            )
            tags = _parse_json_list(row.get("tags_json"))
            image_url = transform_image_url(row.get("image_url"))
            if not image_url:
                image_url = "/images/Logo.png"
            person_id = _upsert_person(session, name)
            title_id = _upsert_title(session, title)
            session.execute(
                text(
                    """
                    INSERT INTO app.people_cards (slug, person_id, bucket, title_id, image_url)
                    VALUES (:slug, :person_id, :bucket, :title_id, :image_url)
                    ON CONFLICT (slug) DO UPDATE
                    SET person_id = EXCLUDED.person_id,
                        bucket = EXCLUDED.bucket,
                        title_id = EXCLUDED.title_id,
                        image_url = EXCLUDED.image_url,
                        updated_at = now()
                    """
                ),
                {
                    "slug": slug,
                    "person_id": person_id,
                    "bucket": media_bucket,
                    "title_id": title_id,
                    "image_url": image_url,
                },
            )
            session.execute(
                text(
                    """
                    INSERT INTO app.people_articles (person_slug, markdown)
                    VALUES (:person_slug, :markdown)
                    ON CONFLICT (person_slug) DO UPDATE
                    SET markdown = EXCLUDED.markdown,
                        updated_at = now()
                    """
                ),
                {"person_slug": slug, "markdown": markdown},
            )
            synced_tags = _sync_person_tags(session, person_id=person_id, tags=tags)
            person_id_by_slug[slug] = int(person_id)
            stats.cards_upserted += 1
            stats.articles_upserted += 1
            stats.people_synced += 1
            stats.tags_synced += synced_tags
            if idx % 5 == 0 or idx == len(cards):
                print(
                    f"[migration] cards processed: {idx}/{len(cards)} (images uploaded={stats.images_uploaded}, reused={stats.images_reused})",
                    flush=True,
                )

        migrated_proposal_ids: set[int] = set()
        for idx, row in enumerate(proposals, start=1):
            proposal_id = int(row.get("id") or 0)
            if proposal_id <= 0:
                continue
            person_slug = _slugify(str(row.get("person_slug") or ""))
            person_id_for_diff = int(person_id_by_slug.get(person_slug) or 0)
            if person_id_for_diff <= 0:
                person_id_for_diff = int(
                    session.execute(
                        text(
                            """
                            SELECT person_id
                            FROM app.people_cards
                            WHERE slug = :person_slug
                            LIMIT 1
                            """
                        ),
                        {"person_slug": person_slug},
                    ).scalar_one_or_none()
                    or 0
                )
                if person_id_for_diff > 0:
                    person_id_by_slug[person_slug] = person_id_for_diff
            proposer_email = str(row.get("proposer_email") or "").strip().lower()
            proposer_name = str(row.get("proposer_name") or "").strip()
            if not proposer_email:
                stats.warn(f"Skipping proposal id={proposal_id} without proposer_email.")
                continue

            proposer_user_id, proposer_email, _ = ensure_user(
                session,
                user_identifier=proposer_email,
                display_name=proposer_name or proposer_email.split("@", 1)[0],
            )
            user_id_by_email[proposer_email.lower()] = int(proposer_user_id)

            reviewer_user_id: int | None = None
            reviewer_email = str(row.get("reviewer_email") or "").strip().lower()
            if reviewer_email:
                reviewer_user_id, reviewer_email, _ = ensure_user(
                    session,
                    user_identifier=reviewer_email,
                    display_name=reviewer_email.split("@", 1)[0],
                )
                user_id_by_email[reviewer_email.lower()] = int(reviewer_user_id)

            scope = normalize_proposal_scope(row.get("proposal_scope"))
            base_markdown = _rewrite_image_paths_in_text(
                str(row.get("base_markdown") or ""),
                transform_image_url=transform_image_url,
            )
            proposed_markdown = _rewrite_image_paths_in_text(
                str(row.get("proposed_markdown") or ""),
                transform_image_url=transform_image_url,
            )
            base_image_url = transform_image_url(row.get("base_image_url"))
            proposed_image_url = transform_image_url(row.get("proposed_image_url"))
            created_at = _parse_timestamp(row.get("created_at"))
            reviewed_at = _parse_timestamp(row.get("reviewed_at"))
            status = _normalize_status(row.get("status"))
            note = str(row.get("note") or "").strip()
            review_note = str(row.get("review_note") or "").strip()
            report_triggered = 1 if int(row.get("report_triggered") or 0) else 0

            session.execute(
                text(
                    """
                    INSERT INTO app.people_change_proposals (
                        id,
                        person_slug,
                        person_id,
                        proposer_user_id,
                        proposal_scope,
                        base_payload,
                        proposed_payload,
                        note,
                        status,
                        created_at,
                        reviewed_at,
                        reviewer_user_id,
                        review_note,
                        report_triggered
                    )
                    VALUES (
                        :id,
                        :person_slug,
                        :person_id,
                        :proposer_user_id,
                        :proposal_scope,
                        :base_payload,
                        :proposed_payload,
                        :note,
                        :status,
                        COALESCE(:created_at, now()),
                        :reviewed_at,
                        :reviewer_user_id,
                        :review_note,
                        :report_triggered
                    )
                    ON CONFLICT (id) DO UPDATE
                    SET person_slug = EXCLUDED.person_slug,
                        person_id = EXCLUDED.person_id,
                        proposer_user_id = EXCLUDED.proposer_user_id,
                        proposal_scope = EXCLUDED.proposal_scope,
                        base_payload = EXCLUDED.base_payload,
                        proposed_payload = EXCLUDED.proposed_payload,
                        note = EXCLUDED.note,
                        status = EXCLUDED.status,
                        created_at = EXCLUDED.created_at,
                        reviewed_at = EXCLUDED.reviewed_at,
                        reviewer_user_id = EXCLUDED.reviewer_user_id,
                        review_note = EXCLUDED.review_note,
                        report_triggered = EXCLUDED.report_triggered
                    """
                ),
                {
                    "id": proposal_id,
                    "person_slug": person_slug,
                    "person_id": person_id_for_diff,
                    "proposer_user_id": int(proposer_user_id),
                    "proposal_scope": scope,
                    "base_payload": base_markdown,
                    "proposed_payload": proposed_markdown,
                    "note": note,
                    "status": status,
                    "created_at": created_at,
                    "reviewed_at": reviewed_at,
                    "reviewer_user_id": int(reviewer_user_id) if reviewer_user_id else None,
                    "review_note": review_note,
                    "report_triggered": report_triggered,
                },
            )
            _upsert_diff_payload_qualified(
                session,
                proposal_id=proposal_id,
                person_id=person_id_for_diff,
                scope=scope,
                base_payload=base_markdown,
                proposed_payload=proposed_markdown,
                base_image_url=base_image_url,
                proposed_image_url=proposed_image_url,
            )
            migrated_proposal_ids.add(proposal_id)
            stats.proposals_upserted += 1
            if scope == "card":
                stats.card_diffs_upserted += 1
            else:
                stats.article_diffs_upserted += 1
            if idx % 5 == 0 or idx == len(proposals):
                print(f"[migration] proposals processed: {idx}/{len(proposals)}", flush=True)

        for idx, row in enumerate(events, start=1):
            event_id = int(row.get("id") or 0)
            proposal_id = int(row.get("proposal_id") or 0)
            if event_id <= 0 or proposal_id <= 0:
                continue
            if migrated_proposal_ids and proposal_id not in migrated_proposal_ids:
                stats.warn(
                    f"Skipping event id={event_id}: proposal_id={proposal_id} not present in migrated proposals."
                )
                continue

            actor_user_id: int | None = None
            actor_email = str(row.get("actor_email") or "").strip().lower()
            actor_name = str(row.get("actor_name") or "").strip()
            if actor_email:
                if actor_email in user_id_by_email:
                    actor_user_id = user_id_by_email[actor_email]
                else:
                    actor_user_id, resolved_email, _ = ensure_user(
                        session,
                        user_identifier=actor_email,
                        display_name=actor_name or actor_email.split("@", 1)[0],
                    )
                    user_id_by_email[resolved_email.lower()] = int(actor_user_id)

            payload_json = _rewrite_payload_json(
                str(row.get("payload_json") or "{}"),
                transform_image_url=transform_image_url,
            )
            created_at = _parse_timestamp(row.get("created_at"))
            notes = str(row.get("notes") or "").strip()
            event_type = str(row.get("event_type") or "").strip() or "unknown"

            session.execute(
                text(
                    """
                    INSERT INTO app.people_change_events (
                        id,
                        proposal_id,
                        event_type,
                        actor_user_id,
                        notes,
                        payload_json,
                        created_at
                    )
                    VALUES (
                        :id,
                        :proposal_id,
                        :event_type,
                        :actor_user_id,
                        :notes,
                        :payload_json,
                        COALESCE(:created_at, now())
                    )
                    ON CONFLICT (id) DO UPDATE
                    SET proposal_id = EXCLUDED.proposal_id,
                        event_type = EXCLUDED.event_type,
                        actor_user_id = EXCLUDED.actor_user_id,
                        notes = EXCLUDED.notes,
                        payload_json = EXCLUDED.payload_json,
                        created_at = EXCLUDED.created_at
                    """
                ),
                {
                    "id": event_id,
                    "proposal_id": proposal_id,
                    "event_type": event_type,
                    "actor_user_id": int(actor_user_id) if actor_user_id else None,
                    "notes": notes,
                    "payload_json": payload_json,
                    "created_at": created_at,
                },
            )
            stats.events_upserted += 1
            if idx % 10 == 0 or idx == len(events):
                print(f"[migration] events processed: {idx}/{len(events)}", flush=True)

        session.execute(
            text(
                """
                SELECT setval(
                    pg_get_serial_sequence('app.people_change_proposals', 'id'),
                    COALESCE((SELECT MAX(id) FROM app.people_change_proposals), 1),
                    TRUE
                )
                """
            )
        )
        session.execute(
            text(
                """
                SELECT setval(
                    pg_get_serial_sequence('app.people_change_events', 'id'),
                    COALESCE((SELECT MAX(id) FROM app.people_change_events), 1),
                    TRUE
                )
                """
            )
        )
        print("[migration] sequence values updated.", flush=True)

    return stats


def _print_summary(stats: MigrationStats, args: argparse.Namespace) -> None:
    print("Migration completed.")
    print(f"- sqlite source: {args.sqlite_path}")
    print(f"- bucket target: {args.bucket_name or bucket_name()}")
    print(f"- cards upserted: {stats.cards_upserted}")
    print(f"- articles upserted: {stats.articles_upserted}")
    print(f"- people synced: {stats.people_synced}")
    print(f"- tags synced: {stats.tags_synced}")
    print(f"- users ensured: {stats.users_ensured}")
    print(f"- privileges synced: {stats.privileges_synced}")
    print(f"- proposals upserted: {stats.proposals_upserted}")
    print(f"- events upserted: {stats.events_upserted}")
    print(f"- article diffs upserted: {stats.article_diffs_upserted}")
    print(f"- card diffs upserted: {stats.card_diffs_upserted}")
    print(f"- images uploaded: {stats.images_uploaded}")
    print(f"- images reused: {stats.images_reused}")
    if stats.warnings:
        print("- warnings:")
        for warning in stats.warnings:
            print(f"  - {warning}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Migrate legacy SQLite people_cards.db into Cloud SQL normalized schema + GCS bucket media.",
    )
    parser.add_argument(
        "--sqlite-path",
        default="bases_de_datos/people_cards.db",
        help="Path to legacy sqlite database.",
    )
    parser.add_argument(
        "--env-file",
        default="secrets/env.dev",
        help="dotenv file to load before opening Cloud SQL/GCS clients.",
    )
    parser.add_argument(
        "--bucket-name",
        default="",
        help="Override destination bucket name. Defaults to BUCKET_NAME env.",
    )
    parser.add_argument(
        "--media-prefix",
        default=_DEFAULT_MEDIA_PREFIX,
        help="Destination blob prefix for migrated images.",
    )
    parser.add_argument(
        "--replace-existing",
        action="store_true",
        help="Delete existing app.people* / app.people_change* target data before import.",
    )
    parser.add_argument(
        "--skip-bucket-upload",
        action="store_true",
        help="Do not upload image files (URLs are still rewritten deterministically).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate upload operations; database writes still occur.",
    )
    return parser


def _ensure_tls_cert_bundle() -> None:
    ssl_cert_file = os.getenv("SSL_CERT_FILE", "")
    if ssl_cert_file and Path(ssl_cert_file).exists():
        return
    try:
        import certifi  # type: ignore

        cert_path = certifi.where()
    except Exception:
        return
    os.environ.setdefault("SSL_CERT_FILE", cert_path)
    os.environ.setdefault("REQUESTS_CA_BUNDLE", cert_path)


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        stats = run_migration(args)
    except (FileNotFoundError, SQLAlchemyError, OSError, ValueError) as exc:
        print(f"Migration failed: {exc}")
        return 1
    _print_summary(stats, args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
