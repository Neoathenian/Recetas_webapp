from __future__ import annotations

import base64
import binascii
import html
import io
import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Dict, List, Sequence, Tuple
from urllib.parse import quote, urlparse
from uuid import uuid4

import gradio as gr
from sqlalchemy import text
from PIL import Image, ImageOps

from src.db import readonly_session_scope, session_scope
from src.gcs_storage import media_path, upload_bytes
from src.login_logic import get_user
from src.people_proposal_diffs import ensure_people_diff_tables, upsert_people_diff_payload
from src.people_taxonomy import (
    ensure_people_cards_refs,
    ensure_people_name_available,
    ensure_people_person,
    ensure_people_taxonomy_schema,
    ensure_people_title,
    sync_people_card_taxonomy,
)

logger = logging.getLogger(__name__)

MAX_IMAGE_BYTES = 8 * 1024 * 1024
CARD_IMAGE_SIZE = (360, 270)
ALLOWED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}
ALLOWED_IMAGE_MIME_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/svg+xml": ".svg",
}
DATA_URL_IMAGE_RE = re.compile(r"^data:(image/[a-z0-9.+-]+);base64,([a-z0-9+/=\s]+)$", re.IGNORECASE)
MARKDOWN_H1_RE = re.compile(r"(?m)^\s*#\s+(.+?)\s*$")
MARKDOWN_TITLE_RE = re.compile(r"(?mi)^\s*-\s*\*\*(?:Bucket|Title)\*\*:\s*(.+?)\s*$")
MARKDOWN_TAGS_RE = re.compile(r"(?mi)^\s*-\s*\*\*Tags\*\*:\s*(.+?)\s*$")
MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*]\(([^)]+)\)")
CITATION_DEFINITION_RE = re.compile(r"^\s*\[(\d{1,4})\]\s*:\s*(.+?)\s*$")
CITATION_SECTION_HEADING_RE = re.compile(r"^\s*#{1,6}\s+references\s*$", re.IGNORECASE)
REFERENCE_ORDERED_ITEM_RE = re.compile(r"^\s*(\d{1,4})[.)]\s+(.+?)\s*$")
REFERENCE_BULLET_ITEM_RE = re.compile(r"^\s*[-*+]\s+(.+?)\s*$")
MARKDOWN_INLINE_LINK_RE = re.compile(r"^\[(.+?)\]\(([^)]+)\)\s*$")
CITE_MACRO_RE = re.compile(r"\\cite\{([^{}\n]+)\}", re.IGNORECASE)
BIB_MACRO_RE = re.compile(r"\\bib\{([^{}\n]+)\}", re.IGNORECASE)
INLINE_CITATION_RE = re.compile(r"(?<!\[)\[(\d{1,4})\](?!\()")
SOURCE_SLUG_REFERENCE_RE = re.compile(r"^source:(.+)$", re.IGNORECASE)
SOURCE_ID_REFERENCE_RE = re.compile(r"^source-id:(\d+)$", re.IGNORECASE)
THEORY_SLUG_REFERENCE_RE = re.compile(r"^theory:(.+)$", re.IGNORECASE)
THEORY_ID_REFERENCE_RE = re.compile(r"^theory-id:(\d+)$", re.IGNORECASE)
HTTP_REFERENCE_RE = re.compile(r"^https?://[^\s]+$", re.IGNORECASE)
CITE_KEY_TOKEN_RE = re.compile(r"@@CITEKEY:([a-z0-9][a-z0-9._-]{0,79})@@")
IMAGE_CONTENT_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
}
THE_LIST_MEDIA_PREFIX = (os.getenv("THE_LIST_MEDIA_PREFIX") or "the-list/uploads").strip("/ ")
DEFAULT_MEDIA_BUCKET = (os.getenv("BUCKET_NAME") or "media-db-dev").strip() or "media-db-dev"
TRUE_VALUES = {"1", "true", "yes", "on"}
SEED_DEMO_DATA = str(os.getenv("THE_LIST_SEED_DEMO_DATA") or "").strip().lower() in TRUE_VALUES
_runtime_bootstrap_default = "0" if os.getenv("INSTANCE_CONNECTION_NAME") else "1"
RUNTIME_SCHEMA_BOOTSTRAP = (
    str(os.getenv("THE_LIST_RUNTIME_SCHEMA_BOOTSTRAP", _runtime_bootstrap_default)).strip().lower() in TRUE_VALUES
)
_DB_INIT_LOCK = threading.Lock()
_DB_INIT_DONE = False
_PEOPLE_SCHEMA_LOCK = threading.Lock()
_PEOPLE_SCHEMA_NAME: str | None = None
_DEFAULT_PEOPLE_SCHEMA = (str(os.getenv("THE_LIST_PEOPLE_SCHEMA") or "app").strip().lower() or "app")
if _DEFAULT_PEOPLE_SCHEMA not in {"app", "public"}:
    _DEFAULT_PEOPLE_SCHEMA = "app"
MARKDOWN_VIEW_RAW = "raw"
MARKDOWN_VIEW_PREVIEW = "preview"
DEFAULT_MARKDOWN_VIEW = MARKDOWN_VIEW_PREVIEW
EDIT_TOGGLE_BUTTON_LABEL = " "
REVIEW_BUTTON_ICON_SRC = "/images/the-list-review-icon.svg"
PROPOSAL_SCOPE_ARTICLE = "article"
LEGACY_PROPOSAL_SCOPE_DESCRIPTION = "description"
PROPOSAL_SCOPE_CARD = "card"
PROPOSAL_SCOPE_CARD_ARTICLE = "card_article"

_IMAGE_POOL: Sequence[str] = (
    "/images/Logo.png",
    "/images/Logo_raw.png",
    "/images/Logo_with_text.png",
    "/images/Logo_text.png",
    "/images/The-list-logo.png",
    "/images/The-list-logo2.png",
    "/images/The-list-logo2_old.png",
    "/images/eye.svg",
    "/images/eye-off.svg",
)

_DUMMY_PEOPLE: Sequence[Tuple[str, str, Tuple[str, ...]]] = (
    ("Adrian Holt", "Goalkeeper", ("captain", "shot-stopper", "distribution")),
    ("Bruno Silva", "Goalkeeper", ("reflexes", "1v1", "sweeper-keeper")),
    ("Caleb Nunez", "Defender", ("aggressive", "aerial", "leader")),
    ("Dario Quinn", "Defender", ("left-footed", "crossing", "stamina")),
    ("Eli Archer", "Defender", ("positioning", "calm", "long-passes")),
    ("Fabio Young", "Defender", ("press-resistant", "interceptions", "duels")),
    ("Gavin Porter", "Midfielder", ("box-to-box", "engine", "pressing")),
    ("Hector Doyle", "Midfielder", ("playmaker", "vision", "tempo-control")),
    ("Isaac Bennett", "Midfielder", ("set-pieces", "range", "discipline")),
    ("Jamal Rivers", "Midfielder", ("ball-winner", "coverage", "teamwork")),
    ("Kai Morales", "Midfielder", ("creativity", "through-balls", "control")),
    ("Liam Walker", "Forward", ("finisher", "movement", "off-ball")),
    ("Marco Stone", "Forward", ("dribbler", "pace", "1v1")),
    ("Niko Foster", "Forward", ("target-man", "hold-up", "aerial")),
    ("Owen Price", "Forward", ("poacher", "positioning", "instinct")),
    ("Pablo Reed", "Forward", ("inside-forward", "right-foot", "cut-inside")),
    ("Quentin Hale", "Defender", ("recovery-speed", "timing", "focus")),
    ("Rafael Mendez", "Defender", ("tackles", "marking", "consistency")),
    ("Soren Blake", "Midfielder", ("deep-lying", "distribution", "composure")),
    ("Thiago King", "Midfielder", ("line-breaker", "progressive-passes", "agility")),
    ("Uriel Grant", "Forward", ("counter-attack", "pace", "composure")),
    ("Victor Lane", "Forward", ("left-wing", "crossing", "work-rate")),
    ("Wyatt Green", "Defender", ("overlaps", "stamina", "pressing")),
    ("Xander Shaw", "Midfielder", ("half-spaces", "link-play", "awareness")),
    ("Yasin Clarke", "Forward", ("right-wing", "acceleration", "decision-making")),
    ("Zane Brooks", "Goalkeeper", ("command", "communication", "distribution")),
)


def _optimize_uploaded_image_bytes(image_bytes: bytes, extension: str) -> tuple[bytes, str]:
    ext = str(extension or "").lower()
    if ext in {".svg", ".gif"}:
        return image_bytes, ext

    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            img = ImageOps.exif_transpose(img)
            resampling = getattr(Image, "Resampling", Image).LANCZOS
            # Normalize uploaded card images to the app's canonical card size.
            img = ImageOps.fit(img, CARD_IMAGE_SIZE, method=resampling)

            out = io.BytesIO()
            if ext in {".jpg", ".jpeg"}:
                if img.mode not in ("RGB", "L"):
                    img = img.convert("RGB")
                img.save(out, format="JPEG", optimize=True, quality=82, progressive=True)
                return out.getvalue(), ".jpg"

            if ext == ".png":
                if img.mode not in ("RGB", "RGBA", "L", "LA", "P"):
                    img = img.convert("RGBA")
                img.save(out, format="PNG", optimize=True)
                return out.getvalue(), ".png"

            if ext == ".webp":
                if img.mode not in ("RGB", "RGBA", "L", "LA"):
                    img = img.convert("RGBA")
                img.save(out, format="WEBP", quality=80, method=6)
                return out.getvalue(), ".webp"
    except Exception:
        logger.exception("Image optimization failed; uploading original bytes")

    return image_bytes, ext


def _is_truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in TRUE_VALUES


def _slugify(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower())
    return normalized.strip("-") or "profile"


def _display_name_from_slug(slug: str) -> str:
    parts = [chunk for chunk in re.split(r"[-_]+", str(slug or "").strip()) if chunk]
    if not parts:
        return "Unknown"
    return " ".join(part.capitalize() for part in parts)


def _normalize_name_key(value: str) -> str:
    return str(value or "").strip().lower()


def _parse_inline_tags(raw_value: str) -> List[str]:
    parsed: List[str] = []
    seen: set[str] = set()

    for match in re.findall(r"`([^`]+)`", str(raw_value or "")):
        normalized = _normalize_tag(match)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        parsed.append(normalized)
    if parsed:
        return parsed

    for chunk in re.split(r",+", str(raw_value or "")):
        normalized = _normalize_tag(re.sub(r"[*_`]", "", chunk))
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        parsed.append(normalized)
    return parsed


def _person_from_article_fallback(slug: str, markdown: str) -> Dict[str, object]:
    normalized_slug = (slug or "").strip().lower()
    markdown_value = str(markdown or "")

    heading_match = MARKDOWN_H1_RE.search(markdown_value)
    name = str(heading_match.group(1)).strip() if heading_match else _display_name_from_slug(normalized_slug)

    title_match = MARKDOWN_TITLE_RE.search(markdown_value)
    title = str(title_match.group(1)).strip() if title_match else "Unassigned"

    tags_match = MARKDOWN_TAGS_RE.search(markdown_value)
    tags = _parse_inline_tags(tags_match.group(1)) if tags_match else []

    image_match = MARKDOWN_IMAGE_RE.search(markdown_value)
    image_url = "/images/Logo.png"
    if image_match:
        resolved_image = str(image_match.group(1) or "").strip()
        if resolved_image:
            image_url = resolved_image

    return {
        "slug": normalized_slug,
        "person_id": 0,
        "name": name or _display_name_from_slug(normalized_slug),
        "title": title or "Unassigned",
        "bucket": title or "Unassigned",
        "image_url": image_url,
        "tags": tags,
        "markdown": markdown_value,
    }


def _resolve_people_schema(session) -> str:
    _ = session
    global _PEOPLE_SCHEMA_NAME
    if _PEOPLE_SCHEMA_NAME:
        return _PEOPLE_SCHEMA_NAME
    with _PEOPLE_SCHEMA_LOCK:
        if _PEOPLE_SCHEMA_NAME:
            return _PEOPLE_SCHEMA_NAME
        _PEOPLE_SCHEMA_NAME = _DEFAULT_PEOPLE_SCHEMA
        return _DEFAULT_PEOPLE_SCHEMA


def _markdown_for_dummy_person(name: str, title: str, tags: Sequence[str], index: int) -> str:
    tags_md = ", ".join(f"`{tag}`" for tag in tags)
    strengths = "\n".join(f"- {tag.replace('-', ' ').title()}" for tag in tags)
    return (
        f"# {name}\n\n"
        "## Snapshot\n"
        f"- **Title:** {title}\n"
        f"- **Tags:** {tags_md}\n"
        f"- **Dummy ID:** P-{index:03d}\n\n"
        "## Background\n"
        f"{name} is a placeholder profile generated for testing card density, click-through navigation, and markdown rendering.\n\n"
        "## Strengths\n"
        f"{strengths}\n\n"
        "## Recent Notes\n"
        "| Match | Result | Notes |\n"
        "|---|---|---|\n"
        f"| Friendly {index} | 2-1 | Created multiple high-value actions |\n"
        f"| Friendly {index + 1} | 1-1 | Strong in transition and shape |\n"
    )


def _ensure_local_db() -> None:
    global _DB_INIT_DONE
    if _DB_INIT_DONE:
        return
    with _DB_INIT_LOCK:
        if _DB_INIT_DONE:
            return
        if not RUNTIME_SCHEMA_BOOTSTRAP:
            _DB_INIT_DONE = True
            return
        _ensure_local_db_once()
        _DB_INIT_DONE = True


def _ensure_local_db_once() -> None:
    with session_scope() as session:
        ensure_people_taxonomy_schema(session)
        session.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS app.people_cards (
                    id BIGSERIAL PRIMARY KEY,
                    slug TEXT NOT NULL UNIQUE,
                    person_id BIGINT NOT NULL REFERENCES app.people(id) ON UPDATE CASCADE ON DELETE CASCADE,
                    title_id BIGINT NOT NULL REFERENCES app.people_titles(id) ON UPDATE CASCADE ON DELETE RESTRICT,
                    bucket TEXT NOT NULL,
                    image_url TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
        )
        session.execute(text("CREATE INDEX IF NOT EXISTS idx_people_cards_bucket ON app.people_cards(bucket)"))
        session.execute(text("CREATE INDEX IF NOT EXISTS idx_people_cards_person_id ON app.people_cards(person_id)"))
        session.execute(text("CREATE INDEX IF NOT EXISTS idx_people_cards_title_id ON app.people_cards(title_id)"))
        ensure_people_cards_refs(session)
        session.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS app.people_articles (
                    id BIGSERIAL PRIMARY KEY,
                    person_slug TEXT NOT NULL UNIQUE REFERENCES app.people_cards(slug) ON UPDATE CASCADE ON DELETE CASCADE,
                    markdown TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
        )
        session.execute(text("CREATE INDEX IF NOT EXISTS idx_people_articles_person_slug ON app.people_articles(person_slug)"))

        has_legacy_markdown = bool(
            session.execute(
                text(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM information_schema.columns
                        WHERE table_schema = 'app'
                          AND table_name = 'people_cards'
                          AND column_name = 'markdown'
                    )
                    """
                )
            ).scalar_one()
        )
        if has_legacy_markdown:
            session.execute(
                text(
                    """
                    INSERT INTO app.people_articles (person_slug, markdown)
                    SELECT slug, markdown
                    FROM app.people_cards
                    WHERE markdown IS NOT NULL
                    ON CONFLICT (person_slug) DO NOTHING
                    """
                )
            )

        session.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS app.people_change_proposals (
                    id BIGSERIAL PRIMARY KEY,
                    person_slug TEXT NOT NULL,
                    person_id BIGINT REFERENCES app.people(id) ON UPDATE CASCADE ON DELETE CASCADE,
                    proposer_user_id BIGINT NOT NULL REFERENCES app."user"(id) ON UPDATE CASCADE ON DELETE RESTRICT,
                    proposal_scope TEXT NOT NULL DEFAULT 'article',
                    base_payload TEXT,
                    proposed_payload TEXT,
                    note TEXT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    reviewed_at TIMESTAMPTZ,
                    reviewer_user_id BIGINT REFERENCES app."user"(id) ON UPDATE CASCADE ON DELETE RESTRICT,
                    review_note TEXT,
                    report_triggered INTEGER NOT NULL DEFAULT 0
                )
                """
            )
        )
        session.execute(text("ALTER TABLE app.people_change_proposals ADD COLUMN IF NOT EXISTS proposer_user_id BIGINT"))
        session.execute(text("ALTER TABLE app.people_change_proposals ADD COLUMN IF NOT EXISTS reviewer_user_id BIGINT"))
        session.execute(text("ALTER TABLE app.people_change_proposals ADD COLUMN IF NOT EXISTS person_id BIGINT"))
        session.execute(text("ALTER TABLE app.people_change_proposals ADD COLUMN IF NOT EXISTS base_payload TEXT"))
        session.execute(text("ALTER TABLE app.people_change_proposals ADD COLUMN IF NOT EXISTS proposed_payload TEXT"))
        _drop_people_change_proposals_slug_fk(session)
        session.execute(
            text(
                """
                UPDATE app.people_change_proposals
                SET proposal_scope = :article_scope
                WHERE COALESCE(lower(proposal_scope), '') = :legacy_scope
                """
            ),
            {
                "article_scope": PROPOSAL_SCOPE_ARTICLE,
                "legacy_scope": LEGACY_PROPOSAL_SCOPE_DESCRIPTION,
            },
        )
        session.execute(text("CREATE INDEX IF NOT EXISTS idx_people_change_proposals_slug ON app.people_change_proposals(person_slug)"))
        session.execute(
            text("CREATE INDEX IF NOT EXISTS idx_people_change_proposals_person_id ON app.people_change_proposals(person_id)")
        )
        session.execute(text("CREATE INDEX IF NOT EXISTS idx_people_change_proposals_status ON app.people_change_proposals(status)"))
        session.execute(
            text(
                "CREATE INDEX IF NOT EXISTS idx_people_change_proposals_proposer_user_id "
                "ON app.people_change_proposals(proposer_user_id)"
            )
        )
        session.execute(
            text(
                "CREATE INDEX IF NOT EXISTS idx_people_change_proposals_reviewer_user_id "
                "ON app.people_change_proposals(reviewer_user_id)"
            )
        )
        ensure_people_diff_tables(session)

        session.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS app.people_change_events (
                    id BIGSERIAL PRIMARY KEY,
                    proposal_id BIGINT NOT NULL REFERENCES app.people_change_proposals(id) ON DELETE CASCADE,
                    event_type TEXT NOT NULL,
                    actor_user_id BIGINT REFERENCES app."user"(id) ON UPDATE CASCADE ON DELETE SET NULL,
                    notes TEXT,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
        )
        session.execute(text("ALTER TABLE app.people_change_events ADD COLUMN IF NOT EXISTS actor_user_id BIGINT"))
        session.execute(text("CREATE INDEX IF NOT EXISTS idx_people_change_events_proposal ON app.people_change_events(proposal_id)"))
        session.execute(
            text(
                "CREATE INDEX IF NOT EXISTS idx_people_change_events_actor_user_id "
                "ON app.people_change_events(actor_user_id)"
            )
        )

        count = int(session.execute(text("SELECT COUNT(1) FROM app.people_cards")).scalar_one())
        if SEED_DEMO_DATA and count == 0:
            seed_card_rows: List[Dict[str, object]] = []
            seed_article_rows: List[Dict[str, object]] = []
            seed_taxonomy_rows: List[Dict[str, object]] = []
            for index, (name, title, tags) in enumerate(_DUMMY_PEOPLE, start=1):
                slug = _slugify(name)
                image_url = _IMAGE_POOL[(index - 1) % len(_IMAGE_POOL)]
                markdown = _markdown_for_dummy_person(name, title, tags, index)
                person_id = ensure_people_person(session, name)
                seed_card_rows.append(
                    {
                        "slug": slug,
                        "person_id": person_id,
                        "title_id": ensure_people_title(session, title),
                        "bucket": DEFAULT_MEDIA_BUCKET,
                        "image_url": image_url,
                    }
                )
                seed_taxonomy_rows.append({"person_id": person_id, "title": title, "tags": list(tags)})
                seed_article_rows.append({"person_slug": slug, "markdown": markdown})

            session.execute(
                text(
                    """
                    INSERT INTO app.people_cards (slug, person_id, title_id, bucket, image_url)
                    VALUES (:slug, :person_id, :title_id, :bucket, :image_url)
                    ON CONFLICT (slug) DO NOTHING
                    """
                ),
                seed_card_rows,
            )
            session.execute(
                text(
                    """
                    INSERT INTO app.people_articles (person_slug, markdown)
                    VALUES (:person_slug, :markdown)
                    ON CONFLICT (person_slug) DO NOTHING
                    """
                ),
                seed_article_rows,
            )
            for row in seed_taxonomy_rows:
                sync_people_card_taxonomy(
                    session,
                    person_id=int(row["person_id"]),
                    title=str(row["title"]),
                    tags=row["tags"],
                )

        session.execute(
            text(
                """
                INSERT INTO app.people_articles (person_slug, markdown)
                SELECT
                    c.slug,
                    '# ' || COALESCE(NULLIF(p.name, ''), c.slug) || E'\n\nProfile pending article content.'
                FROM app.people_cards c
                JOIN app.people p
                    ON p.id = c.person_id
                LEFT JOIN app.people_articles a
                    ON a.person_slug = c.slug
                WHERE a.person_slug IS NULL
                ON CONFLICT (person_slug) DO NOTHING
                """
            )
        )

        if SEED_DEMO_DATA:
            current_markdown = session.execute(
                text(
                    """
                    SELECT markdown
                    FROM app.people_articles
                    WHERE person_slug = 'dario-quinn'
                    """
                )
            ).scalar_one_or_none()
            if current_markdown is not None:
                marker = "![Profile image preview]"
                current_markdown_str = str(current_markdown or "")
                if marker not in current_markdown_str:
                    updated_markdown = (
                        current_markdown_str
                        + "\n\n## Profile Image\n"
                        + "![Profile image preview](/images/Logo_with_text.png)\n"
                    )
                    session.execute(
                        text(
                            """
                            UPDATE app.people_articles
                            SET markdown = :markdown
                            WHERE person_slug = 'dario-quinn'
                            """
                        ),
                        {"markdown": updated_markdown},
                    )

def _decode_tags(raw_value: object) -> List[str]:
    if raw_value is None:
        return []
    if isinstance(raw_value, list):
        return [str(item).strip() for item in raw_value if str(item).strip()]
    try:
        parsed = json.loads(str(raw_value))
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item).strip() for item in parsed if str(item).strip()]


def _normalize_tag(value: str) -> str:
    return str(value or "").strip().lower()


def _parse_tags_input(raw_value: object) -> List[str]:
    parsed: List[str] = []
    seen: set[str] = set()
    for chunk in re.split(r"[,\n]+", str(raw_value or "")):
        cleaned = _normalize_tag(chunk)
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        parsed.append(cleaned)
    return parsed


def _normalize_citation_key(value: str) -> str:
    raw_value = str(value or "").strip().lower()
    if not raw_value:
        return ""
    normalized = re.sub(r"\s+", "-", raw_value)
    normalized = re.sub(r"[^a-z0-9._-]+", "-", normalized)
    normalized = re.sub(r"-{2,}", "-", normalized).strip("-.")
    if not normalized:
        return ""
    if len(normalized) > 80:
        normalized = normalized[:80].rstrip("-.")
    if not normalized or not re.match(r"^[a-z0-9]", normalized):
        return ""
    return normalized


def _looks_like_reference_target(value: str) -> bool:
    candidate = str(value or "").strip()
    if not candidate:
        return False
    return bool(
        SOURCE_SLUG_REFERENCE_RE.match(candidate)
        or SOURCE_ID_REFERENCE_RE.match(candidate)
        or THEORY_SLUG_REFERENCE_RE.match(candidate)
        or THEORY_ID_REFERENCE_RE.match(candidate)
        or HTTP_REFERENCE_RE.match(candidate)
    )


def _ensure_unique_citation_key(candidate_key: str, existing_keys: set[str]) -> str:
    base_key = _normalize_citation_key(candidate_key) or "ref"
    if base_key not in existing_keys:
        existing_keys.add(base_key)
        return base_key
    suffix = 2
    while True:
        next_key = f"{base_key}-{suffix}"
        if next_key not in existing_keys:
            existing_keys.add(next_key)
            return next_key
        suffix += 1


def _split_citation_definition_payload(raw_payload: str) -> tuple[str, str]:
    payload = str(raw_payload or "").strip()
    if not payload:
        return "", ""
    if "|" not in payload:
        return payload, ""
    target_value, label_value = payload.split("|", 1)
    return target_value.strip(), label_value.strip()


def _parse_citation_definitions(markdown: str) -> tuple[str, Dict[int, str]]:
    body_lines: List[str] = []
    definitions: Dict[int, str] = {}
    normalized_markdown = str(markdown or "").replace("\r\n", "\n")
    for line in normalized_markdown.split("\n"):
        match = CITATION_DEFINITION_RE.match(line)
        if not match:
            body_lines.append(line)
            continue
        number = int(match.group(1))
        payload = str(match.group(2) or "").strip()
        if payload:
            definitions[number] = payload

    body_markdown = "\n".join(body_lines)
    return body_markdown.strip(), definitions


def _normalize_reference_target_from_href(raw_href: str) -> str:
    href = str(raw_href or "").strip()
    if not href:
        return ""
    if href.startswith("<") and href.endswith(">") and len(href) > 2:
        href = href[1:-1].strip()
    if not href:
        return ""

    if href.startswith("/"):
        parsed = urlparse(href)
    elif re.match(r"^https?://", href, flags=re.IGNORECASE):
        parsed = urlparse(href)
    else:
        return href

    return href


def _reference_payload_from_list_item(item_text: str) -> str:
    text = str(item_text or "").strip()
    if not text:
        return ""

    link_match = MARKDOWN_INLINE_LINK_RE.match(text)
    if link_match:
        label = str(link_match.group(1) or "").strip()
        href_value = str(link_match.group(2) or "").strip()
        if href_value:
            href_value = href_value.split(None, 1)[0].strip()
        target = _normalize_reference_target_from_href(href_value)
        if not target:
            target = label
            label = ""
        if label and label != target:
            return f"{target} | {label}"
        return target

    target = _normalize_reference_target_from_href(text)
    return target or text


def _parse_reference_list_entry_line(line: str) -> tuple[int | None, str]:
    ordered_match = REFERENCE_ORDERED_ITEM_RE.match(line)
    if ordered_match:
        payload = _reference_payload_from_list_item(ordered_match.group(2))
        if not payload:
            return None, ""
        return int(ordered_match.group(1)), payload

    bullet_match = REFERENCE_BULLET_ITEM_RE.match(line)
    if bullet_match:
        payload = _reference_payload_from_list_item(bullet_match.group(1))
        if not payload:
            return None, ""
        return None, payload

    return None, ""


def _extract_legacy_reference_sections(
    markdown: str,
    occupied_numbers: Sequence[int] | None = None,
) -> tuple[str, Dict[int, str]]:
    normalized_markdown = str(markdown or "").replace("\r\n", "\n")
    lines = normalized_markdown.split("\n")
    output_lines: List[str] = []
    definitions: Dict[int, str] = {}
    used_numbers: set[int] = set()

    for raw_number in occupied_numbers or []:
        try:
            parsed_number = int(raw_number)
        except (TypeError, ValueError):
            continue
        if parsed_number > 0:
            used_numbers.add(parsed_number)

    line_index = 0
    while line_index < len(lines):
        line = lines[line_index]
        if not CITATION_SECTION_HEADING_RE.match(line):
            output_lines.append(line)
            line_index += 1
            continue

        section_lines: List[str] = []
        section_index = line_index + 1
        while section_index < len(lines):
            next_line = lines[section_index]
            if re.match(r"^\s*#{1,6}\s+\S+", next_line):
                break
            section_lines.append(next_line)
            section_index += 1

        non_empty_section_lines = [row for row in section_lines if str(row or "").strip()]
        if not non_empty_section_lines:
            line_index = section_index
            continue

        parsed_rows: List[tuple[int | None, str]] = []
        can_consume_section = True
        for section_line in non_empty_section_lines:
            parsed_number, parsed_payload = _parse_reference_list_entry_line(section_line)
            if not parsed_payload:
                can_consume_section = False
                break
            parsed_rows.append((parsed_number, parsed_payload))

        if not can_consume_section:
            output_lines.append(line)
            output_lines.extend(section_lines)
            line_index = section_index
            continue

        for parsed_number, parsed_payload in parsed_rows:
            reference_number = int(parsed_number) if isinstance(parsed_number, int) else 0
            if reference_number <= 0 or reference_number in used_numbers:
                reference_number = 1
                while reference_number in used_numbers:
                    reference_number += 1
            used_numbers.add(reference_number)
            definitions.setdefault(reference_number, parsed_payload)

        line_index = section_index

    return "\n".join(output_lines).strip(), definitions


def _parse_bib_macro_payload(raw_payload: str) -> tuple[str, str]:
    payload = str(raw_payload or "").strip()
    if not payload:
        return "", ""

    key_value = ""
    reference_value = ""

    if "=>" in payload:
        left, right = payload.split("=>", 1)
        key_value = _normalize_citation_key(left)
        reference_value = str(right or "").strip()
    elif "|" in payload:
        parts = [str(part or "").strip() for part in payload.split("|")]
        first = parts[0] if parts else ""
        second = parts[1] if len(parts) >= 2 else ""
        trailing = "|".join(parts[2:]).strip() if len(parts) > 2 else ""
        if _looks_like_reference_target(first) and len(parts) == 2:
            key_value = ""
            reference_value = f"{first} | {second}".strip()
        else:
            key_value = _normalize_citation_key(first)
            if trailing:
                reference_value = f"{second} | {trailing}".strip()
            else:
                reference_value = second
    else:
        key_target_match = re.match(r"^([A-Za-z0-9._-]+)\s*:\s*(.+)$", payload)
        if key_target_match and not _looks_like_reference_target(payload):
            key_value = _normalize_citation_key(key_target_match.group(1))
            reference_value = str(key_target_match.group(2) or "").strip()
        else:
            key_value = ""
            reference_value = payload

    target_value, label_value = _split_citation_definition_payload(reference_value)
    target_value = str(target_value or "").strip()
    label_value = str(label_value or "").strip()
    if not target_value and label_value:
        target_value = label_value
        label_value = ""
    if not target_value:
        return "", ""
    if label_value:
        return key_value, f"{target_value} | {label_value}"
    return key_value, target_value


def _parse_bibliography_macros(markdown: str) -> tuple[str, Dict[str, str]]:
    normalized_markdown = str(markdown or "").replace("\r\n", "\n")
    bib_definitions: Dict[str, str] = {}
    seen_keys: set[str] = set()

    def _strip_macro(match: re.Match[str]) -> str:
        raw_payload = str(match.group(1) or "").strip()
        parsed_key, parsed_payload = _parse_bib_macro_payload(raw_payload)
        if not parsed_payload:
            return ""
        effective_key = parsed_key
        if not effective_key:
            target_value, label_value = _split_citation_definition_payload(parsed_payload)
            fallback_base = _normalize_citation_key(label_value or target_value) or "ref"
            effective_key = _ensure_unique_citation_key(fallback_base, seen_keys)
        elif effective_key in seen_keys:
            effective_key = _ensure_unique_citation_key(effective_key, seen_keys)
        else:
            seen_keys.add(effective_key)
        bib_definitions[effective_key] = parsed_payload
        return ""

    body_markdown = BIB_MACRO_RE.sub(_strip_macro, normalized_markdown)
    return body_markdown.strip(), bib_definitions


def _fetch_sources_for_citations(
    source_slugs: Sequence[str],
    source_ids: Sequence[int],
) -> tuple[Dict[str, Dict[str, object]], Dict[int, Dict[str, object]]]:
    slug_map: Dict[str, Dict[str, object]] = {}
    id_map: Dict[int, Dict[str, object]] = {}

    normalized_slugs: List[str] = []
    seen_slugs: set[str] = set()
    for raw_slug in source_slugs:
        normalized = str(raw_slug or "").strip().lower()
        if not normalized or normalized in seen_slugs:
            continue
        seen_slugs.add(normalized)
        normalized_slugs.append(normalized)

    normalized_ids: List[int] = []
    seen_ids: set[int] = set()
    for raw_id in source_ids:
        try:
            parsed_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if parsed_id <= 0 or parsed_id in seen_ids:
            continue
        seen_ids.add(parsed_id)
        normalized_ids.append(parsed_id)

    if not normalized_slugs and not normalized_ids:
        return slug_map, id_map

    try:
        with readonly_session_scope() as session:
            for source_slug in normalized_slugs:
                row = session.execute(
                    text(
                        """
                        SELECT id, slug, name
                        FROM app.sources_cards
                        WHERE slug = :slug
                        LIMIT 1
                        """
                    ),
                    {"slug": source_slug},
                ).mappings().first()
                if row is None:
                    continue
                source_row = {
                    "id": int(row["id"]),
                    "slug": str(row["slug"] or "").strip().lower(),
                    "name": str(row["name"] or "").strip(),
                }
                if not source_row["slug"]:
                    continue
                slug_map[source_row["slug"]] = source_row
                id_map[source_row["id"]] = source_row

            for source_id in normalized_ids:
                if source_id in id_map:
                    continue
                row = session.execute(
                    text(
                        """
                        SELECT id, slug, name
                        FROM app.sources_cards
                        WHERE id = :source_id
                        LIMIT 1
                        """
                    ),
                    {"source_id": source_id},
                ).mappings().first()
                if row is None:
                    continue
                source_row = {
                    "id": int(row["id"]),
                    "slug": str(row["slug"] or "").strip().lower(),
                    "name": str(row["name"] or "").strip(),
                }
                if not source_row["slug"]:
                    continue
                slug_map[source_row["slug"]] = source_row
                id_map[source_row["id"]] = source_row
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not resolve source references for citations: %s", exc)

    return slug_map, id_map


def _fetch_theories_for_citations(
    theory_slugs: Sequence[str],
    theory_ids: Sequence[int],
) -> tuple[Dict[str, Dict[str, object]], Dict[int, Dict[str, object]]]:
    slug_map: Dict[str, Dict[str, object]] = {}
    id_map: Dict[int, Dict[str, object]] = {}

    normalized_slugs: List[str] = []
    seen_slugs: set[str] = set()
    for raw_slug in theory_slugs:
        normalized = str(raw_slug or "").strip().lower()
        if not normalized or normalized in seen_slugs:
            continue
        seen_slugs.add(normalized)
        normalized_slugs.append(normalized)

    normalized_ids: List[int] = []
    seen_ids: set[int] = set()
    for raw_id in theory_ids:
        try:
            parsed_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if parsed_id <= 0 or parsed_id in seen_ids:
            continue
        seen_ids.add(parsed_id)
        normalized_ids.append(parsed_id)

    if not normalized_slugs and not normalized_ids:
        return slug_map, id_map

    try:
        with readonly_session_scope() as session:
            for theory_slug in normalized_slugs:
                row = session.execute(
                    text(
                        """
                        SELECT c.id, c.slug, COALESCE(NULLIF(BTRIM(t.name), ''), c.slug) AS name
                        FROM app.theory_cards c
                        LEFT JOIN app.theories t
                            ON t.id = c.person_id
                        WHERE c.slug = :slug
                        LIMIT 1
                        """
                    ),
                    {"slug": theory_slug},
                ).mappings().first()
                if row is None:
                    continue
                theory_row = {
                    "id": int(row["id"]),
                    "slug": str(row["slug"] or "").strip().lower(),
                    "name": str(row["name"] or "").strip(),
                }
                if not theory_row["slug"]:
                    continue
                slug_map[theory_row["slug"]] = theory_row
                id_map[theory_row["id"]] = theory_row

            for theory_id in normalized_ids:
                if theory_id in id_map:
                    continue
                row = session.execute(
                    text(
                        """
                        SELECT c.id, c.slug, COALESCE(NULLIF(BTRIM(t.name), ''), c.slug) AS name
                        FROM app.theory_cards c
                        LEFT JOIN app.theories t
                            ON t.id = c.person_id
                        WHERE c.id = :theory_id
                        LIMIT 1
                        """
                    ),
                    {"theory_id": theory_id},
                ).mappings().first()
                if row is None:
                    continue
                theory_row = {
                    "id": int(row["id"]),
                    "slug": str(row["slug"] or "").strip().lower(),
                    "name": str(row["name"] or "").strip(),
                }
                if not theory_row["slug"]:
                    continue
                slug_map[theory_row["slug"]] = theory_row
                id_map[theory_row["id"]] = theory_row
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not resolve theory references for citations: %s", exc)

    return slug_map, id_map


def _fetch_source_citation_options(limit: int = 300) -> List[Dict[str, object]]:
    normalized_limit = max(1, min(int(limit or 300), 1000))
    source_options: List[Dict[str, object]] = []
    theory_options: List[Dict[str, object]] = []
    try:
        with readonly_session_scope() as session:
            source_rows = session.execute(
                text(
                    """
                    SELECT id, slug, name
                    FROM app.sources_cards
                    ORDER BY LOWER(name), LOWER(slug)
                    LIMIT :limit
                    """
                ),
                {"limit": normalized_limit},
            ).mappings().all()
            theory_rows = session.execute(
                text(
                    """
                    SELECT
                        c.id,
                        c.slug,
                        COALESCE(NULLIF(BTRIM(t.name), ''), c.slug) AS name
                    FROM app.theory_cards c
                    LEFT JOIN app.theories t
                        ON t.id = c.person_id
                    ORDER BY LOWER(COALESCE(NULLIF(BTRIM(t.name), ''), c.slug)), LOWER(c.slug)
                    LIMIT :limit
                    """
                ),
                {"limit": normalized_limit},
            ).mappings().all()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not load citation options: %s", exc)
        return source_options + theory_options

    for row in source_rows:
        source_slug = str(row.get("slug") or "").strip().lower()
        if not source_slug:
            continue
        source_name = str(row.get("name") or "").strip() or source_slug
        source_options.append(
            {
                "id": int(row.get("id") or 0),
                "slug": source_slug,
                "name": source_name,
                "kind": "source",
                "target": f"source:{source_slug}",
            }
        )

    for row in theory_rows:
        theory_slug = str(row.get("slug") or "").strip().lower()
        if not theory_slug:
            continue
        theory_name = str(row.get("name") or "").strip() or theory_slug
        theory_options.append(
            {
                "id": int(row.get("id") or 0),
                "slug": theory_slug,
                "name": theory_name,
                "kind": "theory",
                "target": f"theory:{theory_slug}",
            }
        )

    return source_options + theory_options


def _build_citation_reference_entry(
    number: int,
    raw_payload: str,
    source_slug_map: Dict[str, Dict[str, object]],
    source_id_map: Dict[int, Dict[str, object]],
    theory_slug_map: Dict[str, Dict[str, object]],
    theory_id_map: Dict[int, Dict[str, object]],
) -> Dict[str, object]:
    target_value, label_override = _split_citation_definition_payload(raw_payload)
    target_value = str(target_value or "").strip()
    label_override = str(label_override or "").strip()

    if not target_value and label_override:
        target_value = label_override
        label_override = ""

    source_slug_match = SOURCE_SLUG_REFERENCE_RE.match(target_value)
    if source_slug_match:
        source_slug = str(source_slug_match.group(1) or "").strip().lower()
        source_row = source_slug_map.get(source_slug)
        source_name = (
            str(source_row.get("name") or "").strip()
            if isinstance(source_row, dict)
            else ""
        )
        label = label_override or source_name or f"source:{source_slug}"
        if source_name and label_override:
            preview = f"{label_override} ({source_name})"
        elif source_name:
            preview = f"Source: {source_name}"
        else:
            preview = f"Source slug: {source_slug}"
        return {
            "number": number,
            "label": label,
            "preview": preview,
            "href": "",
            "external": False,
        }

    source_id_match = SOURCE_ID_REFERENCE_RE.match(target_value)
    if source_id_match:
        source_id = int(source_id_match.group(1))
        source_row = source_id_map.get(source_id)
        source_slug = (
            str(source_row.get("slug") or "").strip().lower()
            if isinstance(source_row, dict)
            else ""
        )
        source_name = (
            str(source_row.get("name") or "").strip()
            if isinstance(source_row, dict)
            else ""
        )
        label = label_override or source_name or f"source-id:{source_id}"
        if source_name and label_override:
            preview = f"{label_override} ({source_name})"
        elif source_name:
            preview = f"Source: {source_name}"
        else:
            preview = f"Source id: {source_id}"
        return {
            "number": number,
            "label": label,
            "preview": preview,
            "href": "",
            "external": False,
        }

    theory_slug_match = THEORY_SLUG_REFERENCE_RE.match(target_value)
    if theory_slug_match:
        theory_slug = str(theory_slug_match.group(1) or "").strip().lower()
        theory_row = theory_slug_map.get(theory_slug)
        theory_name = (
            str(theory_row.get("name") or "").strip()
            if isinstance(theory_row, dict)
            else ""
        )
        label = label_override or theory_name or f"theory:{theory_slug}"
        if theory_name and label_override:
            preview = f"{label_override} ({theory_name})"
        elif theory_name:
            preview = f"Theory: {theory_name}"
        else:
            preview = f"Theory slug: {theory_slug}"
        return {
            "number": number,
            "label": label,
            "preview": preview,
            "href": "",
            "external": False,
        }

    theory_id_match = THEORY_ID_REFERENCE_RE.match(target_value)
    if theory_id_match:
        theory_id = int(theory_id_match.group(1))
        theory_row = theory_id_map.get(theory_id)
        theory_slug = (
            str(theory_row.get("slug") or "").strip().lower()
            if isinstance(theory_row, dict)
            else ""
        )
        theory_name = (
            str(theory_row.get("name") or "").strip()
            if isinstance(theory_row, dict)
            else ""
        )
        label = label_override or theory_name or f"theory-id:{theory_id}"
        if theory_name and label_override:
            preview = f"{label_override} ({theory_name})"
        elif theory_name:
            preview = f"Theory: {theory_name}"
        else:
            preview = f"Theory id: {theory_id}"
        return {
            "number": number,
            "label": label,
            "preview": preview,
            "href": "",
            "external": False,
        }

    if HTTP_REFERENCE_RE.match(target_value):
        label = label_override or target_value
        preview = f"{label_override} ({target_value})" if label_override else target_value
        return {
            "number": number,
            "label": label,
            "preview": preview,
            "href": target_value,
            "external": True,
        }

    text_label = label_override or target_value or f"Reference [{number}]"
    return {
        "number": number,
        "label": text_label,
        "preview": text_label,
        "href": "",
        "external": False,
    }


def _missing_citation_reference_entry(number: int) -> Dict[str, object]:
    label = f"Missing reference [{number}]"
    return {
        "number": number,
        "label": label,
        "preview": label,
        "href": "",
        "external": False,
    }


def _render_article_markdown(markdown: str) -> str:
    normalized_markdown = str(markdown or "").replace("\r\n", "\n").strip()
    if not normalized_markdown:
        return ""

    body_markdown, numeric_reference_definitions = _parse_citation_definitions(normalized_markdown)
    body_markdown, bib_reference_definitions = _parse_bibliography_macros(body_markdown)
    body_markdown, legacy_reference_definitions = _extract_legacy_reference_sections(
        body_markdown,
        occupied_numbers=list(numeric_reference_definitions.keys()),
    )
    for legacy_number, legacy_payload in legacy_reference_definitions.items():
        if legacy_number not in numeric_reference_definitions and legacy_payload:
            numeric_reference_definitions[legacy_number] = legacy_payload

    def _replace_cite_macro(match: re.Match[str]) -> str:
        raw_macro_value = str(match.group(1) or "").strip()
        if re.fullmatch(r"\d{1,4}", raw_macro_value):
            return f"[{int(raw_macro_value)}]"
        citation_key = _normalize_citation_key(raw_macro_value)
        if not citation_key:
            return ""
        return f"@@CITEKEY:{citation_key}@@"

    body_with_cite_tokens = CITE_MACRO_RE.sub(_replace_cite_macro, body_markdown)

    ordered_numeric_citations: List[int] = []
    seen_numeric_citations: set[int] = set()
    for match in INLINE_CITATION_RE.finditer(body_with_cite_tokens):
        number = int(match.group(1))
        if number in seen_numeric_citations:
            continue
        seen_numeric_citations.add(number)
        ordered_numeric_citations.append(number)

    ordered_citation_keys: List[str] = []
    seen_citation_keys: set[str] = set()
    for match in CITE_KEY_TOKEN_RE.finditer(body_with_cite_tokens):
        citation_key = _normalize_citation_key(match.group(1))
        if not citation_key or citation_key in seen_citation_keys:
            continue
        seen_citation_keys.add(citation_key)
        ordered_citation_keys.append(citation_key)

    citation_key_number_map: Dict[str, int] = {}
    number_key_map: Dict[int, str] = {}
    reference_payloads_by_number: Dict[int, str] = {}
    ordered_numbers: List[int] = []
    seen_ordered_numbers: set[int] = set()

    def _append_ordered_number(value: int) -> None:
        if value <= 0 or value in seen_ordered_numbers:
            return
        seen_ordered_numbers.add(value)
        ordered_numbers.append(value)

    if bib_reference_definitions:
        # Bib order is authoritative: first \bib is [1], second is [2], etc.
        next_number = 1
        for citation_key, raw_payload in bib_reference_definitions.items():
            citation_key_number_map[citation_key] = next_number
            number_key_map[next_number] = citation_key
            reference_payloads_by_number[next_number] = raw_payload
            _append_ordered_number(next_number)
            next_number += 1

        # Keep explicit numeric definitions only when they are cited and do not
        # overlap with bib-assigned numbers.
        for number in ordered_numeric_citations:
            if number in reference_payloads_by_number:
                continue
            numeric_payload = numeric_reference_definitions.get(number)
            if not numeric_payload:
                continue
            reference_payloads_by_number[number] = numeric_payload
            _append_ordered_number(number)
    else:
        used_numbers: set[int] = set()
        used_numbers.update(number for number in ordered_numeric_citations if 1 <= number <= 9999)
        used_numbers.update(number for number in numeric_reference_definitions if 1 <= number <= 9999)

        next_auto_number = 1
        for citation_key in ordered_citation_keys:
            if citation_key in citation_key_number_map:
                continue
            while next_auto_number in used_numbers and next_auto_number <= 9999:
                next_auto_number += 1
            if next_auto_number > 9999:
                break
            citation_key_number_map[citation_key] = next_auto_number
            used_numbers.add(next_auto_number)
            next_auto_number += 1

        for number in ordered_numeric_citations:
            _append_ordered_number(number)
        for citation_key in ordered_citation_keys:
            assigned_number = citation_key_number_map.get(citation_key, 0)
            _append_ordered_number(assigned_number)
        for number in sorted(numeric_reference_definitions):
            _append_ordered_number(number)

        for number, raw_payload in numeric_reference_definitions.items():
            if raw_payload:
                reference_payloads_by_number[number] = raw_payload

    if not ordered_numbers:
        def _restore_cite_key_macro(match: re.Match[str]) -> str:
            restored_key = _normalize_citation_key(match.group(1))
            if not restored_key:
                return ""
            return f"\\cite{{{restored_key}}}"

        restored_body = CITE_KEY_TOKEN_RE.sub(_restore_cite_key_macro, body_with_cite_tokens)
        return restored_body or normalized_markdown

    source_slug_refs: List[str] = []
    source_id_refs: List[int] = []
    theory_slug_refs: List[str] = []
    theory_id_refs: List[int] = []
    for payload in reference_payloads_by_number.values():
        target_value, _label_override = _split_citation_definition_payload(payload)
        target_value = str(target_value or "").strip()
        source_slug_match = SOURCE_SLUG_REFERENCE_RE.match(target_value)
        if source_slug_match:
            source_slug_refs.append(str(source_slug_match.group(1) or "").strip().lower())
            continue
        source_id_match = SOURCE_ID_REFERENCE_RE.match(target_value)
        if source_id_match:
            source_id_refs.append(int(source_id_match.group(1)))
            continue
        theory_slug_match = THEORY_SLUG_REFERENCE_RE.match(target_value)
        if theory_slug_match:
            theory_slug_refs.append(str(theory_slug_match.group(1) or "").strip().lower())
            continue
        theory_id_match = THEORY_ID_REFERENCE_RE.match(target_value)
        if theory_id_match:
            theory_id_refs.append(int(theory_id_match.group(1)))
    source_slug_map, source_id_map = _fetch_sources_for_citations(source_slug_refs, source_id_refs)
    theory_slug_map, theory_id_map = _fetch_theories_for_citations(theory_slug_refs, theory_id_refs)

    reference_entries: Dict[int, Dict[str, object]] = {}
    for number in ordered_numbers:
        mapped_key = number_key_map.get(number, "")
        raw_payload = reference_payloads_by_number.get(number)
        if raw_payload is None:
            reference_entries[number] = _missing_citation_reference_entry(number)
            continue
        built_entry = _build_citation_reference_entry(
            number,
            raw_payload,
            source_slug_map,
            source_id_map,
            theory_slug_map,
            theory_id_map,
        )
        target_value, label_value = _split_citation_definition_payload(raw_payload)
        target_value = str(target_value or "").strip()
        label_value = str(label_value or "").strip()
        if not target_value and label_value:
            target_value = label_value
            label_value = ""
        if mapped_key:
            built_entry["cite_key"] = mapped_key
        if target_value:
            built_entry["target"] = target_value
        if label_value:
            built_entry["definition_label"] = label_value
        reference_entries[number] = built_entry

    def _render_citation_anchor(number: int) -> str:
        entry = reference_entries.get(number) or _missing_citation_reference_entry(number)
        fallback_href = f"#person-article-reference-{number}"
        href = str(entry.get("href") or "").strip() or fallback_href
        preview = str(entry.get("preview") or entry.get("label") or fallback_href)
        cite_key = _normalize_citation_key(str(entry.get("cite_key") or ""))
        safe_href = html.escape(href, quote=True)
        safe_preview = html.escape(preview, quote=True)
        safe_cite_key = html.escape(cite_key, quote=True)
        link_attrs = ""
        if bool(entry.get("external")) and href:
            link_attrs = " target='_blank' rel='noopener noreferrer'"
        key_attr = f" data-cite-key='{safe_cite_key}'" if cite_key else ""
        return (
            "<sup class='person-article-citation-sup'>"
            f"<a class='person-article-citation' href='{safe_href}' data-cite-preview='{safe_preview}' "
            f"aria-label='{safe_preview}'{key_attr}{link_attrs}>"
            f"[{number}]"
            "</a>"
            "</sup>"
        )

    def _replace_inline_citation(match: re.Match[str]) -> str:
        number = int(match.group(1))
        if number not in reference_entries:
            return f"[{number}]"
        return _render_citation_anchor(number)

    def _replace_key_citation_token(match: re.Match[str]) -> str:
        citation_key = _normalize_citation_key(match.group(1))
        if not citation_key:
            return ""
        number = citation_key_number_map.get(citation_key)
        if number is None:
            return f"\\cite{{{citation_key}}}"
        return _render_citation_anchor(number)

    rendered_body = INLINE_CITATION_RE.sub(_replace_inline_citation, body_with_cite_tokens)
    rendered_body = CITE_KEY_TOKEN_RE.sub(_replace_key_citation_token, rendered_body)

    reference_rows: List[str] = []
    for number in ordered_numbers:
        entry = reference_entries.get(number) or _missing_citation_reference_entry(number)
        label = html.escape(str(entry.get("label") or f"Reference [{number}]"))
        href = str(entry.get("href") or "").strip()
        cite_key = _normalize_citation_key(str(entry.get("cite_key") or ""))
        target_value = str(entry.get("target") or "").strip()
        definition_label = str(entry.get("definition_label") or "").strip()
        item_attrs = [
            f"id='person-article-reference-{number}'",
            f"data-reference-number='{number}'",
            f"value='{number}'",
        ]
        if cite_key:
            item_attrs.append(f"data-reference-key='{html.escape(cite_key, quote=True)}'")
        if target_value:
            item_attrs.append(f"data-reference-target='{html.escape(target_value, quote=True)}'")
        if definition_label:
            item_attrs.append(f"data-reference-label='{html.escape(definition_label, quote=True)}'")
        if href:
            safe_href = html.escape(href, quote=True)
            link_attrs = " target='_blank' rel='noopener noreferrer'" if bool(entry.get("external")) else ""
            row_body = f"<a class='person-article-reference-link' href='{safe_href}'{link_attrs}>{label}</a>"
        else:
            row_body = f"<span class='person-article-reference-text'>{label}</span>"
        reference_rows.append(
            f"<li class='person-article-reference-item' {' '.join(item_attrs)}>{row_body}</li>"
        )

    references_markup = (
        "<div class='person-article-references' id='person-article-references'>"
        "<h2>References</h2>"
        f"<ol>{''.join(reference_rows)}</ol>"
        "</div>"
    )

    if rendered_body.strip():
        return f"{rendered_body.strip()}\n\n{references_markup}"
    return references_markup


def _tags_to_text(tags: Sequence[str]) -> str:
    return ", ".join(_normalize_tag(tag) for tag in tags if _normalize_tag(tag))


def _card_snapshot_from_person(person: Dict[str, object]) -> Dict[str, object]:
    return {
        "name": str(person.get("name") or "").strip(),
        "title": str(person.get("title") or person.get("bucket") or "").strip(),
        "tags": [_normalize_tag(str(tag)) for tag in person.get("tags", []) if _normalize_tag(str(tag))],
        "image_url": str(person.get("image_url") or "").strip(),
    }


def _serialize_card_snapshot(snapshot: Dict[str, object]) -> str:
    name = str(snapshot.get("name") or "").strip()
    title = str(snapshot.get("title") or snapshot.get("bucket") or "").strip()
    image_url = str(snapshot.get("image_url") or "").strip()
    raw_tags = snapshot.get("tags", [])
    if not isinstance(raw_tags, (list, tuple)):
        raw_tags = []
    tags = [_normalize_tag(str(tag)) for tag in raw_tags if _normalize_tag(str(tag))]
    payload = {"name": name, "title": title, "tags": tags, "image_url": image_url}
    return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _serialize_card_article_snapshot(card_snapshot: Dict[str, object], article_markdown: str) -> str:
    """Serialize both card and article into a single JSON payload."""
    name = str(card_snapshot.get("name") or "").strip()
    title = str(card_snapshot.get("title") or card_snapshot.get("bucket") or "").strip()
    image_url = str(card_snapshot.get("image_url") or "").strip()
    raw_tags = card_snapshot.get("tags", [])
    if not isinstance(raw_tags, (list, tuple)):
        raw_tags = []
    tags = [_normalize_tag(str(tag)) for tag in raw_tags if _normalize_tag(str(tag))]
    payload = {
        "card": {"name": name, "title": title, "tags": tags, "image_url": image_url},
        "article": str(article_markdown or "").strip(),
    }
    return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _deserialize_card_article_snapshot(raw_payload: str) -> Dict[str, object]:
    """Deserialize a combined card+article payload."""
    try:
        parsed = json.loads(raw_payload or "{}")
    except (json.JSONDecodeError, TypeError):
        return {"card": {}, "article": ""}
    return {
        "card": parsed.get("card") or {},
        "article": str(parsed.get("article") or ""),
    }


def _fetch_person(slug: str) -> Dict[str, object] | None:
    normalized_slug = (slug or "").strip().lower()
    if not normalized_slug:
        return None

    _ensure_local_db()
    row = None
    fallback_row = None
    with readonly_session_scope() as session:
        schema_name = _resolve_people_schema(session)
        row = session.execute(
            text(
                f"""
                SELECT
                    c.slug,
                    c.person_id,
                    p.name,
                    COALESCE(t.label, 'Unassigned') AS title,
                    c.bucket,
                    c.image_url,
                    COALESCE(
                        (
                            SELECT json_agg(tag_row.label ORDER BY tag_row.label)
                            FROM (
                                SELECT DISTINCT tg.label
                                FROM {schema_name}.people_person_tags ppt
                                JOIN {schema_name}.people_tags tg
                                    ON tg.id = ppt.tag_id
                                WHERE ppt.person_id = c.person_id
                            ) AS tag_row
                        ),
                        '[]'::json
                    )::text AS tags_json,
                    COALESCE(a.markdown, '') AS markdown
                FROM {schema_name}.people_cards c
                JOIN {schema_name}.people p
                    ON p.id = c.person_id
                LEFT JOIN {schema_name}.people_titles t
                    ON t.id = c.title_id
                LEFT JOIN {schema_name}.people_articles a
                    ON a.person_slug = c.slug
                WHERE c.slug = :slug
                """
            ),
            {"slug": normalized_slug},
        ).mappings().first()

        if row is None:
            fallback_row = session.execute(
                text(
                    f"""
                    SELECT
                        person_slug AS slug,
                        COALESCE(markdown, '') AS markdown
                    FROM {schema_name}.people_articles
                    WHERE person_slug = :slug
                    LIMIT 1
                    """
                ),
                {"slug": normalized_slug},
            ).mappings().first()

    if row is None and fallback_row is None:
        return None

    if row is None:
        logger.warning(
            "people_display.fallback_profile_source slug=%s source=%s.people_articles_only",
            normalized_slug,
            schema_name,
        )
        return _person_from_article_fallback(
            slug=str(fallback_row.get("slug") or normalized_slug),
            markdown=str(fallback_row.get("markdown") or ""),
        )

    return {
        "slug": row["slug"],
        "person_id": int(row["person_id"] or 0),
        "name": row["name"],
        "title": row["title"],
        "bucket": row["bucket"],
        "image_url": row["image_url"],
        "tags": _decode_tags(row["tags_json"]),
        "markdown": row["markdown"],
    }


def _fetch_tag_catalog() -> List[str]:
    _ensure_local_db()
    with readonly_session_scope() as session:
        schema_name = _resolve_people_schema(session)
        rows = session.execute(
            text(
                f"""
                SELECT DISTINCT LOWER(BTRIM(label)) AS tag_label
                FROM {schema_name}.people_tags
                WHERE NULLIF(BTRIM(label), '') IS NOT NULL
                ORDER BY LOWER(BTRIM(label))
                """
            )
        ).scalars().all()

    tags: List[str] = []
    seen: set[str] = set()
    for raw_label in rows:
        normalized = _normalize_tag(str(raw_label))
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        tags.append(normalized)
    return tags


def _record_proposal_event(
    session,
    proposal_id: int,
    event_type: str,
    actor_user_id: int | None,
    notes: str,
    payload: Dict[str, object] | None = None,
) -> None:
    payload_json = json.dumps(payload or {}, ensure_ascii=True)
    session.execute(
        text(
            """
            INSERT INTO app.people_change_events (
                proposal_id,
                event_type,
                actor_user_id,
                notes,
                payload_json
            )
            VALUES (:proposal_id, :event_type, :actor_user_id, :notes, :payload_json)
            """
        ),
        {
            "proposal_id": int(proposal_id),
            "event_type": (event_type or "").strip() or "unknown",
            "actor_user_id": int(actor_user_id) if actor_user_id and int(actor_user_id) > 0 else None,
            "notes": (notes or "").strip(),
            "payload_json": payload_json,
        },
    )


def _mark_proposal_accepted(
    session,
    *,
    proposal_id: int,
    person_id: int,
    person_slug: str,
    scope: str,
    reviewer_user_id: int,
    proposed_payload: str,
    proposed_image_url: str = "",
) -> None:
    review_note = "Auto-accepted on submit via editor privilege."
    session.execute(
        text(
            """
            UPDATE app.people_change_proposals
            SET status = 'accepted',
                reviewed_at = CURRENT_TIMESTAMP,
                reviewer_user_id = :reviewer_user_id,
                review_note = :review_note,
                person_id = :person_id,
                proposed_payload = :proposed_payload,
                report_triggered = 0
            WHERE id = :proposal_id
            """
        ),
        {
            "proposal_id": int(proposal_id),
            "person_id": int(person_id),
            "reviewer_user_id": int(reviewer_user_id),
            "review_note": review_note,
            "proposed_payload": str(proposed_payload or ""),
        },
    )
    _record_proposal_event(
        session,
        proposal_id,
        event_type="proposal_accepted",
        actor_user_id=reviewer_user_id,
        notes=review_note,
        payload={
            "person_slug": (person_slug or "").strip().lower(),
            "proposal_scope": (scope or PROPOSAL_SCOPE_ARTICLE).strip().lower(),
            "proposed_image_url": str(proposed_image_url or "").strip(),
            "auto_accepted": True,
        },
    )


def _drop_people_change_proposals_slug_fk(session) -> None:
    try:
        fk_rows = session.execute(
            text(
                """
                SELECT tc.constraint_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                    ON kcu.constraint_name = tc.constraint_name
                   AND kcu.table_schema = tc.table_schema
                   AND kcu.table_name = tc.table_name
                WHERE tc.table_schema = 'app'
                  AND tc.table_name = 'people_change_proposals'
                  AND tc.constraint_type = 'FOREIGN KEY'
                  AND kcu.column_name = 'person_slug'
                """
            )
        ).scalars().all()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not inspect people_change_proposals FK constraints: %s", exc)
        return
    for constraint_name in fk_rows:
        escaped = str(constraint_name).replace('"', '""')
        try:
            session.execute(text(f'ALTER TABLE app.people_change_proposals DROP CONSTRAINT IF EXISTS "{escaped}"'))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not drop people_change_proposals constraint `%s`: %s", constraint_name, exc)


def _slug_is_reserved(session, slug: str) -> bool:
    normalized_slug = (slug or "").strip().lower()
    if not normalized_slug:
        return True
    return bool(
        session.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM app.people_cards
                    WHERE slug = :slug
                    UNION ALL
                    SELECT 1
                    FROM app.people_change_proposals
                    WHERE person_slug = :slug
                )
                """
            ),
            {"slug": normalized_slug},
        ).scalar_one()
    )


def _next_available_proposal_slug(session, seed_value: str) -> str:
    base_slug = _slugify(seed_value)
    candidate = base_slug
    suffix = 2
    while _slug_is_reserved(session, candidate):
        candidate = f"{base_slug}-{suffix}"
        suffix += 1
    return candidate


def _extract_upload_path(uploaded_image: object) -> str:
    if not uploaded_image:
        return ""
    if isinstance(uploaded_image, Path):
        return str(uploaded_image)
    if isinstance(uploaded_image, str):
        return uploaded_image
    if isinstance(uploaded_image, dict):
        return str(uploaded_image.get("path") or uploaded_image.get("name") or "")
    if isinstance(uploaded_image, (list, tuple)):
        for item in uploaded_image:
            candidate = _extract_upload_path(item)
            if candidate:
                return candidate
    return ""


def _persist_uploaded_image(upload_path: str, slug: str, actor_email: str) -> str:
    source = Path((upload_path or "").strip())
    if not source.is_file():
        raise ValueError("Uploaded image could not be read.")

    extension = source.suffix.lower()
    if extension not in ALLOWED_IMAGE_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_IMAGE_EXTENSIONS))
        raise ValueError(f"Unsupported image format. Allowed: {allowed}")
    image_bytes = source.read_bytes()
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise ValueError(f"Image exceeds {MAX_IMAGE_BYTES // (1024 * 1024)} MB limit.")
    image_bytes, extension = _optimize_uploaded_image_bytes(image_bytes, extension)

    email_slug = _slugify((actor_email or "anon").split("@", 1)[0])
    filename = f"{email_slug}-{uuid4().hex[:10]}{extension}"
    blob_name = f"{THE_LIST_MEDIA_PREFIX}/{_slugify(slug)}/{filename}"
    upload_bytes(
        image_bytes,
        blob_name,
        content_type=IMAGE_CONTENT_TYPES.get(extension),
        cache_seconds=3600,
    )
    return media_path(blob_name)


def _persist_uploaded_image_data_url(image_data_url: str, slug: str, actor_email: str) -> str:
    raw_payload = str(image_data_url or "").strip()
    if not raw_payload:
        raise ValueError("Cropped image payload is empty.")

    match = DATA_URL_IMAGE_RE.match(raw_payload)
    if not match:
        raise ValueError("Cropped image payload is invalid.")

    mime_type = str(match.group(1) or "").strip().lower()
    extension = ALLOWED_IMAGE_MIME_TYPES.get(mime_type)
    if not extension or extension not in ALLOWED_IMAGE_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_IMAGE_MIME_TYPES))
        raise ValueError(f"Unsupported cropped image type `{mime_type}`. Allowed: {allowed}")

    base64_payload = re.sub(r"\s+", "", str(match.group(2) or ""))
    try:
        image_bytes = base64.b64decode(base64_payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("Cropped image payload could not be decoded.") from exc

    if not image_bytes:
        raise ValueError("Cropped image payload is empty.")
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise ValueError(f"Image exceeds {MAX_IMAGE_BYTES // (1024 * 1024)} MB limit.")
    image_bytes, extension = _optimize_uploaded_image_bytes(image_bytes, extension)

    email_slug = _slugify((actor_email or "anon").split("@", 1)[0])
    filename = f"{email_slug}-{uuid4().hex[:10]}{extension}"
    blob_name = f"{THE_LIST_MEDIA_PREFIX}/{_slugify(slug)}/{filename}"
    upload_bytes(
        image_bytes,
        blob_name,
        content_type=IMAGE_CONTENT_TYPES.get(extension),
        cache_seconds=3600,
    )
    return media_path(blob_name)


def _query_param(request: gr.Request | None, key: str) -> str:
    if request is None:
        return ""
    request_obj = getattr(request, "request", request)
    query_params = getattr(request_obj, "query_params", None)
    if not query_params:
        return ""
    return str(query_params.get(key, "")).strip()


def _role_flags_from_request(request: gr.Request | None) -> tuple[Dict[str, object], bool, bool]:
    # People page callbacks are high-frequency; use session-cached privileges first.
    user = get_user(request, refresh_privileges=False) or {}
    privileges = user.get("privileges")
    if not isinstance(privileges, dict):
        privileges = {}
    if user and not privileges:
        refreshed_user = get_user(
            request,
            refresh_privileges=True,
            force_privileges_refresh=True,
        ) or {}
        if refreshed_user:
            user = refreshed_user
            refreshed_privileges = refreshed_user.get("privileges")
            privileges = refreshed_privileges if isinstance(refreshed_privileges, dict) else {}
    can_review = _is_truthy(privileges.get("reviewer"))
    can_submit = _is_truthy(privileges.get("base_user"))
    if user and not can_submit:
        # Avoid stale-deny UX: if submit is blocked from cached privileges, force one refresh.
        refreshed_user = get_user(
            request,
            refresh_privileges=True,
            force_privileges_refresh=True,
        ) or {}
        if refreshed_user:
            user = refreshed_user
            refreshed_privileges = refreshed_user.get("privileges")
            privileges = refreshed_privileges if isinstance(refreshed_privileges, dict) else {}
            can_review = _is_truthy(privileges.get("reviewer"))
            can_submit = _is_truthy(privileges.get("base_user"))
    return user, can_review, can_submit


def _user_has_editor_privilege(user: Dict[str, object]) -> bool:
    privileges = user.get("privileges")
    if not isinstance(privileges, dict):
        return False
    return _is_truthy(privileges.get("editor"))


def _user_has_admin_privilege(user: Dict[str, object]) -> bool:
    privileges = user.get("privileges")
    if not isinstance(privileges, dict):
        return False
    return _is_truthy(privileges.get("admin")) or _is_truthy(privileges.get("creator"))


def _resolve_request_user_id(user: Dict[str, object]) -> int:
    for key in ("user_id", "employee_id", "id"):
        raw_value = user.get(key)
        if raw_value in (None, ""):
            continue
        try:
            resolved = int(raw_value)
        except (TypeError, ValueError):
            continue
        if resolved > 0:
            return resolved

    raw_email = str(user.get("email") or "").strip().lower()
    if raw_email:
        with readonly_session_scope() as session:
            resolved = session.execute(
                text(
                    """
                    SELECT id
                    FROM app."user"
                    WHERE lower(email) = :email
                    LIMIT 1
                    """
                ),
                {"email": raw_email},
            ).scalar_one_or_none()
        try:
            resolved_id = int(resolved or 0)
        except (TypeError, ValueError):
            resolved_id = 0
        if resolved_id > 0:
            # Keep downstream callbacks from repeating the lookup in this request context.
            user["user_id"] = resolved_id
            user["employee_id"] = resolved_id
            return resolved_id
    return 0


def _render_tag_chips(tags: Sequence[str]) -> str:
    if not tags:
        return '<span class="person-tag person-tag--muted">no-tags</span>'
    parts = []
    for tag in tags:
        safe_tag = html.escape(tag)
        parts.append(f'<span class="person-tag">{safe_tag}</span>')
    return "".join(parts)


def _render_person_hero(person: Dict[str, object]) -> str:
    name = html.escape(str(person.get("name") or "Unknown"))
    title = html.escape(str(person.get("title") or person.get("bucket") or "Unassigned"))
    image_url = html.escape(str(person.get("image_url") or "/images/Logo.png"), quote=True)
    tags_source = person.get("tags", [])
    if not isinstance(tags_source, (list, tuple, set)):
        tags_source = []
    tags_markup = _render_tag_chips(tags_source)
    tag_catalog_source = person.get("tag_catalog", [])
    if not isinstance(tag_catalog_source, (list, tuple, set)):
        tag_catalog_source = []
    tag_catalog_values: List[str] = []
    seen_catalog_tags: set[str] = set()
    for raw_tag in [*tag_catalog_source, *tags_source]:
        normalized_tag = _normalize_tag(str(raw_tag))
        if not normalized_tag or normalized_tag in seen_catalog_tags:
            continue
        seen_catalog_tags.add(normalized_tag)
        tag_catalog_values.append(normalized_tag)
    tag_catalog_json = html.escape(json.dumps(tag_catalog_values, ensure_ascii=True), quote=True)
    return f"""
    <section class="person-detail-card" id="person-detail-card">
      <div class="person-detail-card__media" id="person-detail-card-media">
        <img src="{image_url}" alt="{name}" loading="lazy" />
      </div>
      <div class="person-detail-card__body">
        <div class="person-detail-card__title-row">
          <h2 class="person-detail-card__title" id="person-detail-card-title">{name}</h2>
          <div class="person-detail-card__title-actions-slot" id="person-detail-card-title-actions-slot"></div>
        </div>
        <p class="person-detail-card__bucket" id="person-detail-card-bucket">{title}</p>
        <div class="person-detail-card__tags" id="person-detail-card-tags" data-tag-catalog="{tag_catalog_json}">{tags_markup}</div>
        <div class="person-detail-card__inline-actions-slot" id="person-detail-card-inline-actions-slot"></div>
      </div>
    </section>
    """


def _render_missing_person(slug: str) -> str:
    safe_slug = html.escape(slug or "unknown")
    return (
        "<section class='person-detail-card person-detail-card--missing'>"
        "<div class='person-detail-card__body'>"
        "<h2>Profile not found</h2>"
        f"<p>No player matched slug <code>{safe_slug}</code>.</p>"
        "</div></section>"
    )


def _render_review_link_button(slug: str) -> str:
    href = f"/review/?slug={quote((slug or '').strip().lower(), safe='-')}"
    return (
        f"<a class='the-list-review-btn' href='{href}' title='Review' aria-label='Review'>"
        "<span class='the-list-review-btn__label'>Review</span>"
        f"<img class='the-list-review-btn__icon' src='{REVIEW_BUTTON_ICON_SRC}' alt='' aria-hidden='true' loading='lazy'/>"
        "</a>"
    )


def _build_proposal_help_messages(
    user_name: str,
    user_email: str,
    can_submit: bool,
    is_editor: bool = False,
) -> tuple[str, str]:
    _ = (user_name, user_email, is_editor)
    if not can_submit:
        disabled_message = (
            "Your `base_user` privilege is currently disabled. Contact a creator if this was removed by mistake."
        )
        return disabled_message, disabled_message

    return (
        "Submit an article proposal and creators will review the tracked diff.",
        "Submit a card proposal and creators will review the tracked diff.",
    )


def _toggle_proposal_markdown_view(view_mode: str, proposal_markdown: str):
    normalized_mode = (view_mode or DEFAULT_MARKDOWN_VIEW).strip().lower()
    is_preview = normalized_mode in {
        str(MARKDOWN_VIEW_PREVIEW).strip().lower(),
        "compiled",
        "preview",
    }
    preview_value = _render_article_markdown(proposal_markdown or "") if is_preview else (proposal_markdown or "")
    return (
        gr.update(visible=not is_preview),
        gr.update(value=preview_value, visible=is_preview),
    )


def _toggle_markdown_editor(
    edit_mode: bool,
    current_slug: str,
    current_markdown: str,
    request: gr.Request,
):
    user, _, can_submit = _role_flags_from_request(request)
    slug = (current_slug or "").strip().lower()
    if not user or not can_submit or not slug:
        return (
            False,
            gr.update(visible=True),
            gr.update(visible=False),
            gr.update(value=current_markdown or "", visible=False),
            gr.update(value=_render_article_markdown(current_markdown or ""), visible=True),
            gr.update(value=DEFAULT_MARKDOWN_VIEW),
            gr.update(value=EDIT_TOGGLE_BUTTON_LABEL, visible=False),
            "",
        )

    next_mode = not bool(edit_mode)
    if next_mode:
        return (
            True,
            gr.update(visible=False),
            gr.update(visible=True),
            gr.update(value=current_markdown or "", visible=False),
            gr.update(value=_render_article_markdown(current_markdown or ""), visible=True),
            gr.update(value=DEFAULT_MARKDOWN_VIEW),
            gr.update(value=EDIT_TOGGLE_BUTTON_LABEL, visible=True),
            "",
        )

    return (
        False,
        gr.update(visible=True),
        gr.update(visible=False),
        gr.update(value=current_markdown or "", visible=False),
        gr.update(value=_render_article_markdown(current_markdown or ""), visible=True),
        gr.update(value=DEFAULT_MARKDOWN_VIEW),
        gr.update(value=EDIT_TOGGLE_BUTTON_LABEL, visible=True),
        "",
    )


def _cancel_markdown_editor(current_markdown: str):
    return (
        False,
        gr.update(visible=True),
        gr.update(visible=False),
        gr.update(value=current_markdown or "", visible=False),
        gr.update(value=_render_article_markdown(current_markdown or ""), visible=True),
        gr.update(value=DEFAULT_MARKDOWN_VIEW),
        gr.update(value=EDIT_TOGGLE_BUTTON_LABEL, visible=True),
        "",
        "",
    )


def _append_markdown_image(
    current_slug: str,
    proposal_markdown: str,
    uploaded_image: object,
    request: gr.Request,
):
    try:
        user, _, can_submit = _role_flags_from_request(request)
        if not user:
            return (
                gr.update(),
                gr.update(),
                "❌ You must be logged in to upload images.",
                gr.update(value=None),
            )
        if not can_submit:
            return (
                gr.update(),
                gr.update(),
                "❌ Your `base_user` privilege is disabled. Ask a creator to restore access.",
                gr.update(value=None),
            )

        uploaded_path = _extract_upload_path(uploaded_image)
        if not uploaded_path:
            return (
                gr.update(),
                gr.update(),
                "",
                gr.update(value=None),
            )

        actor_user_id = _resolve_request_user_id(user)
        if actor_user_id <= 0:
            return (
                gr.update(),
                gr.update(),
                "❌ Could not resolve your user id.",
                gr.update(value=None),
            )
        actor_email = (user.get("email") or "").strip().lower()
        actor_storage_identity = actor_email or f"user-{actor_user_id}"

        slug = (current_slug or "").strip().lower() or "profile"
        image_url = _persist_uploaded_image(uploaded_path, slug, actor_storage_identity)
        image_markdown = f"![Profile image]({image_url})"
        next_markdown = str(proposal_markdown or "").rstrip()
        if next_markdown:
            next_markdown = f"{next_markdown}\n\n{image_markdown}\n"
        else:
            next_markdown = f"{image_markdown}\n"

        return (
            gr.update(value=next_markdown),
            gr.update(value=next_markdown),
            "",
            gr.update(value=None),
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to append markdown image: %s", exc)
        return (
            gr.update(),
            gr.update(),
            f"❌ Could not upload image: {exc}",
            gr.update(value=None),
        )


def _toggle_card_editor(
    edit_mode: bool,
    current_slug: str,
    current_name: str,
    current_bucket: str,
    current_tags: str,
    request: gr.Request,
):
    user, _, can_submit = _role_flags_from_request(request)
    slug = (current_slug or "").strip().lower()
    if not user or not can_submit or not slug:
        return (
            False,
            gr.update(visible=False),
            gr.update(value=current_name or ""),
            gr.update(value=current_bucket or ""),
            gr.update(value=current_tags or ""),
            gr.update(value=EDIT_TOGGLE_BUTTON_LABEL, visible=False),
            "",
            gr.update(value=""),
        )

    next_mode = not bool(edit_mode)
    return (
        next_mode,
        gr.update(visible=next_mode),
        gr.update(value=current_name or ""),
        gr.update(value=current_bucket or ""),
        gr.update(value=current_tags or ""),
        gr.update(value=EDIT_TOGGLE_BUTTON_LABEL, visible=True),
        "",
        gr.update(value=""),
    )


def _cancel_card_editor(current_name: str, current_bucket: str, current_tags: str):
    return (
        False,
        gr.update(visible=False),
        gr.update(value=current_name or ""),
        gr.update(value=current_bucket or ""),
        gr.update(value=current_tags or ""),
        gr.update(value=EDIT_TOGGLE_BUTTON_LABEL, visible=True),
        "",
        "",
        gr.update(value=None),
        gr.update(value=""),
    )


def _delete_person_card_article(current_slug: str, request: gr.Request) -> tuple[bool, str]:
    try:
        user, _, _ = _role_flags_from_request(request)
        if not user:
            return False, "❌ You must be logged in to delete a profile."
        if not _user_has_admin_privilege(user):
            return False, "❌ Only admins can delete card + article profiles."

        slug = str(current_slug or "").strip().lower()
        if not slug:
            return False, "❌ Open a profile before trying to delete it."

        _ensure_local_db()
        with session_scope() as session:
            schema_name = _resolve_people_schema(session)
            deleted_slug = session.execute(
                text(
                    f"""
                    DELETE FROM {schema_name}.people_cards
                    WHERE slug = :slug
                    RETURNING slug
                    """
                ),
                {"slug": slug},
            ).scalar_one_or_none()
            deleted_orphan_article_slug = None
            if not deleted_slug:
                deleted_orphan_article_slug = session.execute(
                    text(
                        f"""
                        DELETE FROM {schema_name}.people_articles
                        WHERE person_slug = :slug
                        RETURNING person_slug
                        """
                    ),
                    {"slug": slug},
                ).scalar_one_or_none()

        if not deleted_slug and not deleted_orphan_article_slug:
            return False, "❌ Profile not found or already deleted."

        actor_email = str(user.get("email") or "").strip().lower()
        logger.info(
            "people_display.profile_deleted slug=%s actor=%s deleted_card=%s deleted_orphan_article=%s",
            slug,
            actor_email or "unknown",
            bool(deleted_slug),
            bool(deleted_orphan_article_slug),
        )
        return True, f"✅ Deleted `{slug}` card + article."
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to delete people profile: %s", exc)
        return False, f"❌ Could not delete profile: {exc}"


def _submit_markdown_proposal(
    current_slug: str,
    proposal_note: str,
    proposal_markdown: str,
    current_markdown: str,
    edit_mode: bool,
    request: gr.Request,
):
    def _response(message: str, next_note: str, close_editor: bool = False):
        if close_editor:
            (
                next_mode,
                detail_markdown_update,
                proposal_shell_update,
                proposal_markdown_update,
                proposal_preview_update,
                proposal_view_mode_update,
                edit_button_update,
                _reset_note,
                _reset_status,
            ) = _cancel_markdown_editor(current_markdown)
            return (
                message,
                next_note,
                next_mode,
                detail_markdown_update,
                proposal_shell_update,
                proposal_markdown_update,
                proposal_preview_update,
                proposal_view_mode_update,
                edit_button_update,
            )

        return (
            message,
            next_note,
            bool(edit_mode),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
        )

    try:
        user, _, can_submit = _role_flags_from_request(request)
        if not user:
            return _response("❌ You must be logged in to submit a proposal.", proposal_note)
        if not can_submit:
            return _response(
                "❌ Your `base_user` privilege is disabled. Ask a creator to restore access.",
                proposal_note,
            )
        auto_accept = _user_has_editor_privilege(user)

        slug = (current_slug or "").strip().lower()
        if not slug:
            return _response("❌ Open a player profile before submitting a proposal.", proposal_note)

        person = _fetch_person(slug)
        if person is None:
            return _response("❌ Player profile not found.", proposal_note)
        person_id = int(person.get("person_id") or 0)
        if person_id <= 0:
            return _response("❌ Could not resolve player id for this profile.", proposal_note)

        proposed_markdown = (proposal_markdown or "").strip()
        if not proposed_markdown:
            return _response("❌ Proposed markdown cannot be empty.", proposal_note)
        if len(proposed_markdown) > 60000:
            return _response("❌ Proposed markdown is too large (max 60,000 chars).", proposal_note)

        actor_user_id = _resolve_request_user_id(user)
        if actor_user_id <= 0:
            return _response("❌ Could not resolve your user id.", proposal_note)

        note_value = (proposal_note or "").strip()
        base_markdown = str(person.get("markdown") or "")
        if proposed_markdown == base_markdown:
            return _response("❌ No changes detected in the article.", proposal_note)

        _ensure_local_db()
        with session_scope() as session:
            proposal_id = int(
                session.execute(
                    text(
                        """
                        INSERT INTO app.people_change_proposals (
                            person_slug,
                            person_id,
                            proposer_user_id,
                            proposal_scope,
                            base_payload,
                            proposed_payload,
                            note,
                            status
                        )
                        VALUES (
                            :person_slug,
                            :person_id,
                            :proposer_user_id,
                            :proposal_scope,
                            :base_payload,
                            :proposed_payload,
                            :note,
                            'pending'
                        )
                        RETURNING id
                        """
                    ),
                    {
                        "person_slug": slug,
                        "person_id": person_id,
                        "proposer_user_id": actor_user_id,
                        "proposal_scope": PROPOSAL_SCOPE_ARTICLE,
                        "base_payload": base_markdown,
                        "proposed_payload": proposed_markdown,
                        "note": note_value,
                    },
                ).scalar_one()
            )
            upsert_people_diff_payload(
                session,
                proposal_id=proposal_id,
                person_id=person_id,
                scope=PROPOSAL_SCOPE_ARTICLE,
                base_payload=base_markdown,
                proposed_payload=proposed_markdown,
                base_image_url="",
                proposed_image_url="",
            )
            _record_proposal_event(
                session,
                proposal_id,
                event_type="article_proposal_submitted",
                actor_user_id=actor_user_id,
                notes=note_value,
                payload={
                    "person_slug": slug,
                    "proposal_scope": PROPOSAL_SCOPE_ARTICLE,
                },
            )
            if auto_accept:
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
                    {
                        "person_slug": slug,
                        "markdown": proposed_markdown,
                    },
                )
                _mark_proposal_accepted(
                    session,
                    proposal_id=proposal_id,
                    person_id=person_id,
                    person_slug=slug,
                    scope=PROPOSAL_SCOPE_ARTICLE,
                    reviewer_user_id=actor_user_id,
                    proposed_payload=proposed_markdown,
                )

        if auto_accept:
            return _response(
                f"✅ Article proposal #{proposal_id} submitted and auto-accepted (Editor privilege).",
                "",
                close_editor=True,
            )
        return _response(
            f"✅ Article proposal #{proposal_id} submitted. It is now tracked and pending creator review.",
            "",
            close_editor=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to submit change proposal: %s", exc)
        return _response(f"❌ Could not submit proposal: {exc}", proposal_note)


def _submit_card_proposal(
    current_slug: str,
    proposal_note: str,
    proposal_name: str,
    proposal_bucket: str,
    proposal_tags: str,
    proposal_image: object,
    proposal_image_data: str,
    current_name: str,
    current_bucket: str,
    current_tags: str,
    edit_mode: bool,
    request: gr.Request,
):
    def _response(
        message: str,
        next_note: str,
        image_update,
        image_data_update,
        close_editor: bool = False,
    ):
        if close_editor:
            (
                next_mode,
                card_shell_update,
                card_name_update,
                card_bucket_update,
                card_tags_update,
                card_edit_btn_update,
                _reset_note,
                _reset_status,
                _reset_image,
                _reset_image_data,
            ) = _cancel_card_editor(current_name, current_bucket, current_tags)
            return (
                message,
                next_note,
                image_update,
                image_data_update,
                next_mode,
                card_shell_update,
                card_name_update,
                card_bucket_update,
                card_tags_update,
                card_edit_btn_update,
            )

        return (
            message,
            next_note,
            image_update,
            image_data_update,
            bool(edit_mode),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
        )

    try:
        user, _, can_submit = _role_flags_from_request(request)
        if not user:
            return _response(
                "❌ You must be logged in to submit a proposal.",
                proposal_note,
                gr.update(value=None),
                gr.update(),
            )
        if not can_submit:
            return _response(
                "❌ Your `base_user` privilege is disabled. Ask a creator to restore access.",
                proposal_note,
                gr.update(value=None),
                gr.update(),
            )
        auto_accept = _user_has_editor_privilege(user)

        slug = (current_slug or "").strip().lower()
        if not slug:
            return _response(
                "❌ Open a player profile before submitting a proposal.",
                proposal_note,
                gr.update(value=None),
                gr.update(),
            )

        person = _fetch_person(slug)
        if person is None:
            return _response(
                "❌ Player profile not found.",
                proposal_note,
                gr.update(value=None),
                gr.update(),
            )
        person_id = int(person.get("person_id") or 0)
        if person_id <= 0:
            return _response(
                "❌ Could not resolve player id for this profile.",
                proposal_note,
                gr.update(value=None),
                gr.update(),
            )

        actor_user_id = _resolve_request_user_id(user)
        if actor_user_id <= 0:
            return _response(
                "❌ Could not resolve your user id.",
                proposal_note,
                gr.update(value=None),
                gr.update(),
            )
        actor_email = (user.get("email") or "").strip().lower()
        actor_storage_identity = actor_email or f"user-{actor_user_id}"

        proposed_name = str(proposal_name or "").strip()
        proposed_title = str(proposal_bucket or "").strip()
        proposed_tags = _parse_tags_input(proposal_tags)
        if not proposed_name:
            return _response(
                "❌ Card name cannot be empty.",
                proposal_note,
                gr.update(value=None),
                gr.update(),
            )
        if not proposed_title:
            return _response(
                "❌ Card title cannot be empty.",
                proposal_note,
                gr.update(value=None),
                gr.update(),
            )
        base_name_key = _normalize_name_key(str(person.get("name") or ""))
        proposed_name_key = _normalize_name_key(proposed_name)
        if proposed_name_key != base_name_key:
            with readonly_session_scope() as session:
                ensure_people_name_available(session, proposed_name, exclude_slug=slug)

        base_snapshot = _card_snapshot_from_person(person)
        proposed_snapshot = {
            "name": proposed_name,
            "title": proposed_title,
            "tags": proposed_tags,
        }

        uploaded_path = _extract_upload_path(proposal_image)
        uploaded_data_url = str(proposal_image_data or "").strip()
        base_image_url = str(person.get("image_url") or "")
        proposed_image_url = base_image_url
        if uploaded_data_url:
            proposed_image_url = _persist_uploaded_image_data_url(uploaded_data_url, slug, actor_storage_identity)
        elif uploaded_path:
            proposed_image_url = _persist_uploaded_image(uploaded_path, slug, actor_storage_identity)
        proposed_snapshot["image_url"] = proposed_image_url

        if (
            _serialize_card_snapshot(base_snapshot) == _serialize_card_snapshot(proposed_snapshot)
            and base_image_url == proposed_image_url
        ):
            return _response(
                "❌ No card changes detected.",
                proposal_note,
                gr.update(value=None),
                gr.update(),
            )

        note_value = (proposal_note or "").strip()
        base_payload = _serialize_card_snapshot(base_snapshot)
        proposed_payload = _serialize_card_snapshot(proposed_snapshot)
        base_name = str(base_snapshot.get("name") or "").strip()
        base_title = str(base_snapshot.get("title") or base_snapshot.get("bucket") or "").strip()
        base_tags = [_normalize_tag(str(tag)) for tag in base_snapshot.get("tags", []) if _normalize_tag(str(tag))]
        should_update_name = proposed_name != base_name
        should_update_card_row = (proposed_title != base_title) or (proposed_image_url != base_image_url)
        should_sync_tags = set(base_tags) != set(proposed_tags)

        _ensure_local_db()
        with session_scope() as session:
            if proposed_name_key != base_name_key:
                ensure_people_name_available(session, proposed_name, exclude_slug=slug)
            proposal_id = int(
                session.execute(
                    text(
                        """
                        INSERT INTO app.people_change_proposals (
                            person_slug,
                            person_id,
                            proposer_user_id,
                            proposal_scope,
                            base_payload,
                            proposed_payload,
                            note,
                            status
                        )
                        VALUES (
                            :person_slug,
                            :person_id,
                            :proposer_user_id,
                            :proposal_scope,
                            :base_payload,
                            :proposed_payload,
                            :note,
                            'pending'
                        )
                        RETURNING id
                        """
                    ),
                    {
                        "person_slug": slug,
                        "person_id": person_id,
                        "proposer_user_id": actor_user_id,
                        "proposal_scope": PROPOSAL_SCOPE_CARD,
                        "base_payload": base_payload,
                        "proposed_payload": proposed_payload,
                        "note": note_value,
                    },
                ).scalar_one()
            )
            _record_proposal_event(
                session,
                proposal_id,
                event_type="card_proposal_submitted",
                actor_user_id=actor_user_id,
                notes=note_value,
                payload={
                    "person_slug": slug,
                    "proposal_scope": PROPOSAL_SCOPE_CARD,
                    "proposed_image_url": proposed_image_url,
                },
            )
            if auto_accept:
                if should_update_name:
                    session.execute(
                        text(
                            """
                            UPDATE app.people
                            SET name = :name,
                                updated_at = now()
                            WHERE id = :person_id
                            """
                        ),
                        {
                            "name": proposed_name,
                            "person_id": person_id,
                        },
                    )
                if should_update_card_row:
                    session.execute(
                        text(
                            """
                            UPDATE app.people_cards
                            SET title_id = :title_id,
                                bucket = :bucket,
                                image_url = :image_url,
                                updated_at = now()
                            WHERE slug = :person_slug
                            """
                        ),
                        {
                            "title_id": ensure_people_title(session, proposed_title),
                            "bucket": proposed_title,
                            "image_url": proposed_image_url,
                            "person_slug": slug,
                        },
                    )
                if should_sync_tags:
                    sync_people_card_taxonomy(
                        session,
                        person_id=person_id,
                        title=proposed_title,
                        tags=proposed_tags,
                        ensure_title=False,
                    )
                _mark_proposal_accepted(
                    session,
                    proposal_id=proposal_id,
                    person_id=person_id,
                    person_slug=slug,
                    scope=PROPOSAL_SCOPE_CARD,
                    reviewer_user_id=actor_user_id,
                    proposed_payload=proposed_payload,
                    proposed_image_url=proposed_image_url,
                )

        if auto_accept:
            return _response(
                f"✅ Card proposal #{proposal_id} submitted and auto-accepted (Editor privilege).",
                "",
                gr.update(value=None),
                gr.update(value=""),
                close_editor=True,
            )
        return _response(
            f"✅ Card proposal #{proposal_id} submitted. It is now tracked and pending creator review.",
            "",
            gr.update(value=None),
            gr.update(value=""),
            close_editor=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to submit card proposal: %s", exc)
        return _response(
            f"❌ Could not submit proposal: {exc}",
            proposal_note,
            gr.update(value=None),
            gr.update(),
        )


def _submit_new_profile_proposal(
    proposal_note: str,
    proposal_name: str,
    proposal_bucket: str,
    proposal_tags: str,
    proposal_image: object,
    proposal_image_data: str,
    proposal_markdown: str,
    request: gr.Request,
):
    def _response(
        message: str,
        next_note: str,
        next_name: str,
        next_bucket: str,
        next_tags: str,
        next_markdown: str,
        image_update,
        image_data_update,
    ):
        return (
            message,
            next_note,
            next_name,
            next_bucket,
            next_tags,
            image_update,
            image_data_update,
            next_markdown,
            _render_article_markdown(next_markdown),
        )

    try:
        user, _, can_submit = _role_flags_from_request(request)
        if not user:
            return _response(
                "❌ You must be logged in to submit a proposal.",
                proposal_note,
                proposal_name,
                proposal_bucket,
                proposal_tags,
                proposal_markdown,
                gr.update(),
                gr.update(),
            )
        if not can_submit:
            return _response(
                "❌ Your `base_user` privilege is disabled. Ask a creator to restore access.",
                proposal_note,
                proposal_name,
                proposal_bucket,
                proposal_tags,
                proposal_markdown,
                gr.update(),
                gr.update(),
            )
        auto_accept = _user_has_editor_privilege(user)

        actor_user_id = _resolve_request_user_id(user)
        if actor_user_id <= 0:
            return _response(
                "❌ Could not resolve your user id.",
                proposal_note,
                proposal_name,
                proposal_bucket,
                proposal_tags,
                proposal_markdown,
                gr.update(),
                gr.update(),
            )

        proposed_name = str(proposal_name or "").strip()
        proposed_title = str(proposal_bucket or "").strip()
        proposed_tags = _parse_tags_input(proposal_tags)
        proposed_markdown = str(proposal_markdown or "").strip()
        if not proposed_name:
            return _response(
                "❌ Card name cannot be empty.",
                proposal_note,
                proposal_name,
                proposal_bucket,
                proposal_tags,
                proposal_markdown,
                gr.update(),
                gr.update(),
            )
        if not proposed_title:
            return _response(
                "❌ Card title cannot be empty.",
                proposal_note,
                proposal_name,
                proposal_bucket,
                proposal_tags,
                proposal_markdown,
                gr.update(),
                gr.update(),
            )
        if not proposed_markdown:
            return _response(
                "❌ Proposed markdown cannot be empty.",
                proposal_note,
                proposal_name,
                proposal_bucket,
                proposal_tags,
                proposal_markdown,
                gr.update(),
                gr.update(),
            )
        if len(proposed_markdown) > 60000:
            return _response(
                "❌ Proposed markdown is too large (max 60,000 chars).",
                proposal_note,
                proposal_name,
                proposal_bucket,
                proposal_tags,
                proposal_markdown,
                gr.update(),
                gr.update(),
            )

        actor_email = (user.get("email") or "").strip().lower()
        actor_storage_identity = actor_email or f"user-{actor_user_id}"
        note_value = str(proposal_note or "").strip()

        _ensure_local_db()
        with readonly_session_scope() as session:
            ensure_people_name_available(session, proposed_name)
        with session_scope() as session:
            _drop_people_change_proposals_slug_fk(session)
            ensure_people_name_available(session, proposed_name)

            person_id = ensure_people_person(session, proposed_name)
            slug = _next_available_proposal_slug(session, proposed_name)

            uploaded_path = _extract_upload_path(proposal_image)
            uploaded_data_url = str(proposal_image_data or "").strip()
            proposed_image_url = "/images/Logo.png"
            if uploaded_data_url:
                proposed_image_url = _persist_uploaded_image_data_url(uploaded_data_url, slug, actor_storage_identity)
            elif uploaded_path:
                proposed_image_url = _persist_uploaded_image(uploaded_path, slug, actor_storage_identity)

            base_payload = _serialize_card_article_snapshot(
                {"name": "", "title": "", "tags": [], "image_url": ""},
                "",
            )
            proposed_payload = _serialize_card_article_snapshot(
                {
                    "name": proposed_name,
                    "title": proposed_title,
                    "tags": proposed_tags,
                    "image_url": proposed_image_url,
                },
                proposed_markdown,
            )

            proposal_id = int(
                session.execute(
                    text(
                        """
                        INSERT INTO app.people_change_proposals (
                            person_slug,
                            person_id,
                            proposer_user_id,
                            proposal_scope,
                            base_payload,
                            proposed_payload,
                            note,
                            status
                        )
                        VALUES (
                            :person_slug,
                            :person_id,
                            :proposer_user_id,
                            :proposal_scope,
                            :base_payload,
                            :proposed_payload,
                            :note,
                            'pending'
                        )
                        RETURNING id
                        """
                    ),
                    {
                        "person_slug": slug,
                        "person_id": person_id,
                        "proposer_user_id": actor_user_id,
                        "proposal_scope": PROPOSAL_SCOPE_CARD_ARTICLE,
                        "base_payload": base_payload,
                        "proposed_payload": proposed_payload,
                        "note": note_value,
                    },
                ).scalar_one()
            )
            upsert_people_diff_payload(
                session,
                proposal_id=proposal_id,
                person_id=person_id,
                scope=PROPOSAL_SCOPE_CARD_ARTICLE,
                base_payload=base_payload,
                proposed_payload=proposed_payload,
                base_image_url="",
                proposed_image_url=proposed_image_url,
            )
            _record_proposal_event(
                session,
                proposal_id,
                event_type="card_article_proposal_submitted",
                actor_user_id=actor_user_id,
                notes=note_value,
                payload={
                    "person_slug": slug,
                    "proposal_scope": PROPOSAL_SCOPE_CARD_ARTICLE,
                    "is_new_profile": True,
                    "proposed_image_url": proposed_image_url,
                },
            )
            if auto_accept:
                session.execute(
                    text(
                        """
                        UPDATE app.people
                        SET name = :name,
                            updated_at = now()
                        WHERE id = :person_id
                        """
                    ),
                    {
                        "name": proposed_name,
                        "person_id": person_id,
                    },
                )
                title_id = ensure_people_title(session, proposed_title)
                session.execute(
                    text(
                        """
                        INSERT INTO app.people_cards (slug, person_id, title_id, bucket, image_url)
                        VALUES (:slug, :person_id, :title_id, :bucket, :image_url)
                        ON CONFLICT (slug) DO UPDATE
                        SET person_id = EXCLUDED.person_id,
                            title_id = EXCLUDED.title_id,
                            bucket = EXCLUDED.bucket,
                            image_url = EXCLUDED.image_url,
                            updated_at = now()
                        """
                    ),
                    {
                        "slug": slug,
                        "person_id": person_id,
                        "title_id": title_id,
                        "bucket": proposed_title,
                        "image_url": proposed_image_url,
                    },
                )
                sync_people_card_taxonomy(
                    session,
                    person_id=person_id,
                    title=proposed_title,
                    tags=proposed_tags,
                    ensure_title=False,
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
                    {
                        "person_slug": slug,
                        "markdown": proposed_markdown,
                    },
                )
                _mark_proposal_accepted(
                    session,
                    proposal_id=proposal_id,
                    person_id=person_id,
                    person_slug=slug,
                    scope=PROPOSAL_SCOPE_CARD_ARTICLE,
                    reviewer_user_id=actor_user_id,
                    proposed_payload=proposed_payload,
                    proposed_image_url=proposed_image_url,
                )

        if auto_accept:
            return _response(
                f"✅ New profile proposal #{proposal_id} for `{slug}` was submitted and auto-accepted (Editor privilege).",
                "",
                "",
                "",
                "",
                "",
                gr.update(value=None),
                gr.update(value=""),
            )
        return _response(
            (
                f"✅ New profile proposal #{proposal_id} submitted for `{slug}`. "
                "It is now pending creator review."
            ),
            "",
            "",
            "",
            "",
            "",
            gr.update(value=None),
            gr.update(value=""),
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to submit new profile proposal: %s", exc)
        return _response(
            f"❌ Could not submit new profile proposal: {exc}",
            proposal_note,
            proposal_name,
            proposal_bucket,
            proposal_tags,
            proposal_markdown,
            gr.update(),
            gr.update(),
        )
