from __future__ import annotations

import difflib
import html
import json
import logging
import os
import re
import threading
import textwrap
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Sequence, Tuple
from urllib.parse import quote
from uuid import uuid4

import gradio as gr
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError as SQLAlchemyDatabaseError

from src.db import readonly_session_scope, session_scope
from src.gcs_storage import get_bucket, media_path, upload_bytes
from src.local_user_roles import report_and_disable_user
from src.login_logic import get_user
from src.page_timing import timed_page_load
from src.pages.header import render_header, with_light_mode_head
from src.pages.recetas_display.core_people import (
    _optimize_uploaded_image_bytes,
    _render_article_markdown as _render_citation_compiled_markdown,
)
from src.people_proposal_diffs import ensure_people_diff_tables, upsert_people_diff_payload
from src.people_taxonomy import (
    ensure_people_cards_refs,
    ensure_people_name_available,
    ensure_people_person,
    ensure_people_taxonomy_schema,
    ensure_people_title,
    sync_people_card_taxonomy,
)
from src.theory_proposal_diffs import upsert_theory_diff_payload
from src.theory_taxonomy import (
    ensure_theory_name_available,
    ensure_theory_person,
    ensure_theory_title,
    sync_theory_card_taxonomy,
)

def _store_unsorted_files_for_source(*args, **kwargs):
    raise RuntimeError("Los flujos de fuentes están deshabilitados en esta versión solo de recetas.")

logger = logging.getLogger(__name__)

ASSETS_DIR = Path(__file__).resolve().parent
CSS_PATH = ASSETS_DIR / "css" / "people_page.css"
EDITOR_JS_PATH = ASSETS_DIR / "js" / "people_editor.js"
REVIEW_JS_PATH = ASSETS_DIR / "js" / "review_change_picker.js"
MAX_IMAGE_BYTES = 8 * 1024 * 1024
ALLOWED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}
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


def _parse_cache_seconds(raw_value: str | None, default: float) -> float:
    try:
        return max(0.0, float(raw_value or default))
    except (TypeError, ValueError):
        return default


TABLE_EXISTS_CACHE_SECONDS = _parse_cache_seconds(
    os.getenv("THE_LIST_TABLE_EXISTS_CACHE_SECONDS"),
    30.0,
)
_TABLE_EXISTS_CACHE_LOCK = threading.Lock()
_TABLE_EXISTS_CACHE: Dict[str, Tuple[float, bool]] = {}
REVIEW_TABLES_CACHE_SECONDS = _parse_cache_seconds(
    os.getenv("THE_LIST_REVIEW_TABLES_CACHE_SECONDS"),
    15.0,
)
_REVIEW_TABLES_CACHE_LOCK = threading.Lock()
_REVIEW_TABLES_CACHE: Tuple[float, Tuple[str, ...]] | None = None
_DB_INIT_LOCK = threading.Lock()
_DB_INIT_DONE = False
MARKDOWN_VIEW_RAW = "raw"
MARKDOWN_VIEW_PREVIEW = "preview"
DEFAULT_MARKDOWN_VIEW = MARKDOWN_VIEW_PREVIEW
REVIEW_VIEW_COMPILED = "compiled"
REVIEW_VIEW_RAW = "raw"
DEFAULT_REVIEW_VIEW = REVIEW_VIEW_COMPILED
EDIT_TOGGLE_BUTTON_LABEL = " "
REVIEW_BUTTON_ICON_SRC = "/images/the-list-review-icon.svg"
TAG_FILTER_ALL_OPTION = "Todas"
PROPOSAL_SCOPE_ARTICLE = "article"
LEGACY_PROPOSAL_SCOPE_DESCRIPTION = "description"
PROPOSAL_SCOPE_CARD = "card"
PROPOSAL_SCOPE_CARD_ARTICLE = "card_article"
PROPOSAL_SCOPE_SOURCE = "source"
PROPOSAL_SOURCE_PEOPLE = "people"
PROPOSAL_SOURCE_THEORY = "theory"
PROPOSAL_SOURCE_SOURCE = "source"
SOURCE_PROPOSAL_TYPE_PUSH = "push"
SOURCE_PROPOSAL_TYPE_TAGS = "tags"
_DIFF_TOKEN_RE = re.compile(r"\s+|[^\s]+")
_MARKDOWN_LINE_PREFIX_RE = re.compile(r"^(\s{0,3}(?:#{1,6}\s+|[-*+]\s+|\d+\.\s+|>\s+))(.*)$")
_TABLE_SEPARATOR_CELL_RE = re.compile(r"^:?-{3,}:?$")
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_INLINE_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_INLINE_BOLD_RE = re.compile(r"\*\*([^*\n]+)\*\*")
_INLINE_ITALIC_RE = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")
MARKDOWN_H1_RE = re.compile(r"(?m)^\s*#\s+(.+?)\s*$")
MARKDOWN_TITLE_RE = re.compile(r"(?mi)^\s*-\s*\*\*(?:Bucket|Title|Título|Titulo)\*\*:\s*(.+?)\s*$")
MARKDOWN_TAGS_RE = re.compile(r"(?mi)^\s*-\s*\*\*(?:Tags|Etiquetas)\*\*:\s*(.+?)\s*$")
MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*]\(([^)]+)\)")
REFERENCE_HEADING_LINE_RE = re.compile(r"^\s{0,3}#{1,6}\s+references\s*$", re.IGNORECASE)
REFERENCE_DEFINITION_LINE_RE = re.compile(r"^\s*\[\d{1,4}\]\s*:.+$")

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


def _read_asset(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning("Missing people asset at %s", path)
        return ""


def _load_css() -> str:
    return _read_asset(CSS_PATH)


def _load_editor_js() -> str:
    script = _read_asset(EDITOR_JS_PATH)
    if not script:
        return ""
    return f"<script>\n{script}\n</script>"


def _load_review_js() -> str:
    script = _read_asset(REVIEW_JS_PATH)
    if not script:
        return ""
    return f"<script>\n{script}\n</script>"


def _markdown_component_allow_raw_html(**kwargs):
    # Keep compatibility with older Gradio builds that may not expose sanitize_html.
    try:
        return gr.Markdown(sanitize_html=False, **kwargs)
    except TypeError:
        return gr.Markdown(**kwargs)


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
        return "Desconocido"
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
    title = str(title_match.group(1)).strip() if title_match else "Sin asignar"

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
        "title": title or "Sin asignar",
        "bucket": title or "Sin asignar",
        "image_url": image_url,
        "tags": tags,
        "markdown": markdown_value,
    }


def _table_exists_in_schema(session, schema: str, table_name: str) -> bool:
    normalized_schema = str(schema or "").strip().lower()
    normalized_table = str(table_name or "").strip().lower()
    if normalized_schema not in {"app", "public"} or not normalized_table:
        return False
    cache_key = f"schema:{normalized_schema}.{normalized_table}"
    cached_value = _get_cached_table_exists(cache_key)
    if cached_value is not None:
        return cached_value
    exists = bool(
        session.execute(
            text("SELECT to_regclass(:qualified_name) IS NOT NULL"),
            {"qualified_name": f"{normalized_schema}.{normalized_table}"},
        ).scalar_one()
    )
    _set_cached_table_exists(cache_key, exists)
    return exists


def _resolve_people_schema(session) -> str:
    for schema_name in ("app", "public"):
        if _table_exists_in_schema(session, schema_name, "people_cards"):
            return schema_name
    for schema_name in ("app", "public"):
        if _table_exists_in_schema(session, schema_name, "people_articles"):
            return schema_name
    return "app"


def _resolve_theory_schema(session) -> str:
    for schema_name in ("app", "public"):
        if _table_exists_in_schema(session, schema_name, "theory_cards"):
            return schema_name
    for schema_name in ("app", "public"):
        if _table_exists_in_schema(session, schema_name, "theory_articles"):
            return schema_name
    return "app"


def _table_exists_in_search_path(session, table_name: str) -> bool:
    normalized_table = str(table_name or "").strip().lower()
    if not normalized_table:
        return False
    cache_key = f"schema:app:{normalized_table}"
    cached_value = _get_cached_table_exists(cache_key)
    if cached_value is not None:
        return cached_value
    exists = _table_exists_in_schema(session, "app", normalized_table)
    _set_cached_table_exists(cache_key, exists)
    return exists


def _get_cached_table_exists(cache_key: str) -> bool | None:
    ttl_seconds = float(TABLE_EXISTS_CACHE_SECONDS)
    if ttl_seconds <= 0:
        return None
    now = time.monotonic()
    with _TABLE_EXISTS_CACHE_LOCK:
        entry = _TABLE_EXISTS_CACHE.get(cache_key)
        if entry is None:
            return None
        expires_at, exists = entry
        if now >= float(expires_at):
            _TABLE_EXISTS_CACHE.pop(cache_key, None)
            return None
        return bool(exists)


def _set_cached_table_exists(cache_key: str, exists: bool) -> None:
    ttl_seconds = float(TABLE_EXISTS_CACHE_SECONDS)
    if ttl_seconds <= 0:
        return
    expires_at = time.monotonic() + ttl_seconds
    with _TABLE_EXISTS_CACHE_LOCK:
        _TABLE_EXISTS_CACHE[cache_key] = (expires_at, bool(exists))


def _clear_table_exists_cache() -> None:
    with _TABLE_EXISTS_CACHE_LOCK:
        _TABLE_EXISTS_CACHE.clear()


def _missing_tables_in_search_path(session, required_tables: Sequence[str]) -> List[str]:
    missing_tables: List[str] = []
    for table_name in required_tables:
        normalized_table = str(table_name or "").strip().lower()
        if not normalized_table:
            continue
        if _table_exists_in_search_path(session, normalized_table):
            continue
        missing_tables.append(normalized_table)
    return missing_tables


def _warn_missing_review_tables(context: str, missing_tables: Sequence[str]) -> None:
    names = sorted({str(name or "").strip().lower() for name in missing_tables if str(name or "").strip()})
    if not names:
        return
    logger.warning(
        "review_display.%s.missing_tables tables=%s runtime_schema_bootstrap=%s",
        context,
        ",".join(names),
        RUNTIME_SCHEMA_BOOTSTRAP,
    )


def _is_missing_relation_error(exc: Exception) -> bool:
    sqlstate = ""
    detail = ""
    orig = getattr(exc, "orig", None)
    args = getattr(orig, "args", ())
    if args:
        first_arg = args[0]
        if isinstance(first_arg, dict):
            sqlstate = str(first_arg.get("C") or "").strip()
            detail = str(first_arg.get("M") or "").strip()
        else:
            detail = str(first_arg)

    normalized_message = f"{exc} {detail}".lower()
    return sqlstate == "42P01" or ("relation" in normalized_message and "does not exist" in normalized_message)


def _is_missing_column_error(exc: Exception, column_name: str) -> bool:
    sqlstate = ""
    detail = ""
    orig = getattr(exc, "orig", None)
    args = getattr(orig, "args", ())
    if args:
        first_arg = args[0]
        if isinstance(first_arg, dict):
            sqlstate = str(first_arg.get("C") or "").strip()
            detail = str(first_arg.get("M") or "").strip()
        else:
            detail = str(first_arg)

    normalized_message = f"{exc} {detail}".lower()
    target = str(column_name or "").strip().lower()
    return sqlstate == "42703" or ("column" in normalized_message and target in normalized_message and "does not exist" in normalized_message)


def _missing_review_query_tables() -> List[str]:
    cached_missing_tables = _get_cached_missing_review_tables()
    if cached_missing_tables is not None:
        return cached_missing_tables

    _ensure_local_db()
    with readonly_session_scope() as session:
        missing_tables = _missing_tables_in_search_path(
            session,
            (
                "app.people_change_proposals",
            ),
        )
    _set_cached_missing_review_tables(missing_tables)
    return missing_tables


def _get_cached_missing_review_tables() -> List[str] | None:
    ttl_seconds = float(REVIEW_TABLES_CACHE_SECONDS)
    if ttl_seconds <= 0:
        return None
    now = time.monotonic()
    with _REVIEW_TABLES_CACHE_LOCK:
        entry = _REVIEW_TABLES_CACHE
        if entry is None:
            return None
        expires_at, table_names = entry
        if now >= float(expires_at):
            _clear_missing_review_tables_cache_locked()
            return None
        return list(table_names)


def _set_cached_missing_review_tables(missing_tables: Sequence[str]) -> None:
    ttl_seconds = float(REVIEW_TABLES_CACHE_SECONDS)
    if ttl_seconds <= 0:
        return
    normalized = tuple(
        sorted({str(name or "").strip().lower() for name in missing_tables if str(name or "").strip()})
    )
    expires_at = time.monotonic() + ttl_seconds
    with _REVIEW_TABLES_CACHE_LOCK:
        global _REVIEW_TABLES_CACHE
        _REVIEW_TABLES_CACHE = (expires_at, normalized)


def _clear_missing_review_tables_cache() -> None:
    with _REVIEW_TABLES_CACHE_LOCK:
        _clear_missing_review_tables_cache_locked()


def _clear_missing_review_tables_cache_locked() -> None:
    global _REVIEW_TABLES_CACHE
    _REVIEW_TABLES_CACHE = None


def _markdown_for_dummy_person(name: str, title: str, tags: Sequence[str], index: int) -> str:
    tags_md = ", ".join(f"`{tag}`" for tag in tags)
    strengths = "\n".join(f"- {tag.replace('-', ' ').title()}" for tag in tags)
    return (
        f"# {name}\n\n"
        "## Resumen\n"
        f"- **Título:** {title}\n"
        f"- **Etiquetas:** {tags_md}\n"
        f"- **ID de prueba:** P-{index:03d}\n\n"
        "## Contexto\n"
        f"{name} es un perfil de relleno generado para probar densidad de tarjetas, navegación por clic y renderizado de markdown.\n\n"
        "## Fortalezas\n"
        f"{strengths}\n\n"
        "## Notas recientes\n"
        "| Partido | Resultado | Notas |\n"
        "|---|---|---|\n"
        f"| Amistoso {index} | 2-1 | Generó múltiples acciones de alto valor |\n"
        f"| Amistoso {index + 1} | 1-1 | Fuerte en transición y estructura |\n"
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
                    '# ' || COALESCE(NULLIF(p.name, ''), c.slug) || E'\n\nPerfil con contenido de artículo pendiente.'
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
                marker = "![Vista previa de imagen de perfil]"
                current_markdown_str = str(current_markdown or "")
                if marker not in current_markdown_str:
                    updated_markdown = (
                        current_markdown_str
                        + "\n\n## Imagen de perfil\n"
                        + "![Vista previa de imagen de perfil](/images/Logo_with_text.png)\n"
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
    _clear_table_exists_cache()
    _clear_missing_review_tables_cache()

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
    tags = [str(item).strip() for item in parsed if str(item).strip()]
    return tags


def _decode_events(raw_value: object) -> List[Dict[str, object]]:
    if raw_value is None:
        return []
    if isinstance(raw_value, list):
        return [dict(item) for item in raw_value if isinstance(item, dict)]
    try:
        parsed = json.loads(str(raw_value))
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [dict(item) for item in parsed if isinstance(item, dict)]


def _normalize_proposal_scope(value: object) -> str:
    normalized = str(value or "").strip().lower()
    if normalized == PROPOSAL_SCOPE_SOURCE:
        return PROPOSAL_SCOPE_SOURCE
    if normalized == PROPOSAL_SCOPE_CARD:
        return PROPOSAL_SCOPE_CARD
    if normalized == PROPOSAL_SCOPE_CARD_ARTICLE:
        return PROPOSAL_SCOPE_CARD_ARTICLE
    if normalized in {PROPOSAL_SCOPE_ARTICLE, LEGACY_PROPOSAL_SCOPE_DESCRIPTION}:
        return PROPOSAL_SCOPE_ARTICLE
    return PROPOSAL_SCOPE_ARTICLE


def _normalize_proposal_source(value: object) -> str:
    normalized = str(value or "").strip().lower()
    if normalized == PROPOSAL_SOURCE_SOURCE:
        return PROPOSAL_SOURCE_SOURCE
    if normalized == PROPOSAL_SOURCE_THEORY:
        return PROPOSAL_SOURCE_THEORY
    return PROPOSAL_SOURCE_PEOPLE


def _proposal_source_label(source: str) -> str:
    normalized_source = _normalize_proposal_source(source)
    if normalized_source == PROPOSAL_SOURCE_SOURCE:
        return "FUENTE"
    if normalized_source == PROPOSAL_SOURCE_THEORY:
        return "TEORÍA"
    return "RECETAS"


def _normalize_source_proposal_type(value: object) -> str:
    normalized = str(value or "").strip().lower()
    if normalized == SOURCE_PROPOSAL_TYPE_TAGS:
        return SOURCE_PROPOSAL_TYPE_TAGS
    return SOURCE_PROPOSAL_TYPE_PUSH


def _proposal_choice_value(source: str, proposal_id: object, source_proposal_type: object = "") -> str:
    normalized_source = _normalize_proposal_source(source)
    proposal_id_text = str(proposal_id or "").strip()
    if not proposal_id_text.isdigit():
        return ""
    if normalized_source == PROPOSAL_SOURCE_SOURCE:
        normalized_type = _normalize_source_proposal_type(source_proposal_type)
        return f"{normalized_source}:{normalized_type}:{proposal_id_text}"
    return f"{normalized_source}:{proposal_id_text}"


def _parse_proposal_choice_value(raw_value: object) -> tuple[str, int, str] | None:
    text_value = str(raw_value or "").strip()
    if not text_value:
        return None

    if ":" not in text_value:
        if text_value.isdigit():
            return PROPOSAL_SOURCE_PEOPLE, int(text_value), ""
        return None

    parts = text_value.split(":")
    if len(parts) == 2:
        raw_source, raw_id = parts
        raw_type = ""
    elif len(parts) == 3:
        raw_source, raw_type, raw_id = parts
    else:
        return None
    source = _normalize_proposal_source(raw_source)
    source_proposal_type = (
        _normalize_source_proposal_type(raw_type)
        if source == PROPOSAL_SOURCE_SOURCE and str(raw_type or "").strip()
        else ""
    )
    proposal_id_text = str(raw_id or "").strip()
    if not proposal_id_text.isdigit():
        return None
    return source, int(proposal_id_text), source_proposal_type


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


def _deserialize_card_snapshot(raw_value: object, fallback: Dict[str, object] | None = None) -> Dict[str, object]:
    seed = fallback or {"name": "", "title": "", "tags": [], "image_url": ""}
    snapshot = {
        "name": str(seed.get("name") or "").strip(),
        "title": str(seed.get("title") or seed.get("bucket") or "").strip(),
        "tags": [_normalize_tag(str(tag)) for tag in seed.get("tags", []) if _normalize_tag(str(tag))],
        "image_url": str(seed.get("image_url") or "").strip(),
    }
    try:
        parsed = json.loads(str(raw_value or ""))
    except json.JSONDecodeError:
        return snapshot
    if not isinstance(parsed, dict):
        return snapshot
    name = str(parsed.get("name") or "").strip()
    title = str(parsed.get("title") or "").strip()
    image_url = str(parsed.get("image_url") or "").strip()
    legacy_bucket = str(parsed.get("bucket") or "").strip()
    raw_tags = parsed.get("tags", [])
    if not isinstance(raw_tags, (list, tuple)):
        raw_tags = []
    tags = [_normalize_tag(str(tag)) for tag in raw_tags if _normalize_tag(str(tag))]
    if "name" in parsed and name:
        snapshot["name"] = name
    if "title" in parsed and title:
        snapshot["title"] = title
    elif "bucket" in parsed and legacy_bucket:
        # Backward compatibility with older payloads that used `bucket` as title.
        snapshot["title"] = legacy_bucket
    if "tags" in parsed and isinstance(parsed.get("tags"), (list, tuple)):
        snapshot["tags"] = tags
    if "image_url" in parsed:
        snapshot["image_url"] = image_url
    return snapshot


def _deserialize_card_article_snapshot(raw_value: object) -> Dict[str, object]:
    """Deserialize a combined card+article payload."""
    result: Dict[str, object] = {"card": {}, "article": ""}
    try:
        parsed = json.loads(str(raw_value or ""))
    except json.JSONDecodeError:
        return result
    if not isinstance(parsed, dict):
        return result
    card_data = parsed.get("card") or {}
    article_data = str(parsed.get("article") or "")
    result["card"] = _deserialize_card_snapshot(json.dumps(card_data) if isinstance(card_data, dict) else "{}")
    result["article"] = article_data
    return result


def _fetch_all_people() -> List[Dict[str, object]]:
    _ensure_local_db()
    schema_name = "app"
    rows = []
    fallback_rows = []
    with readonly_session_scope() as session:
        schema_name = _resolve_people_schema(session)
        if _table_exists_in_schema(session, schema_name, "people_cards"):
            rows = session.execute(
                text(
                    f"""
                    SELECT
                        c.slug,
                        p.name,
                        COALESCE(t.label, 'Sin asignar') AS title,
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
                        )::text AS tags_json
                    FROM {schema_name}.people_cards c
                    JOIN {schema_name}.people p
                        ON p.id = c.person_id
                    LEFT JOIN {schema_name}.people_titles t
                        ON t.id = c.title_id
                    ORDER BY p.name
                    """
                )
            ).mappings().all()
        elif _table_exists_in_schema(session, schema_name, "people_articles"):
            fallback_rows = session.execute(
                text(
                    f"""
                    SELECT
                        person_slug AS slug,
                        COALESCE(markdown, '') AS markdown
                    FROM {schema_name}.people_articles
                    ORDER BY person_slug
                    """
                )
            ).mappings().all()
        else:
            logger.warning(
                "review_display.fetch_all_people.no_people_tables source=%s",
                schema_name,
            )

    people: List[Dict[str, object]] = []
    for row in rows:
        people.append(
            {
                "slug": row["slug"],
                "name": row["name"],
                "title": row["title"],
                "bucket": row["bucket"],
                "image_url": row["image_url"],
                "tags": _decode_tags(row["tags_json"]),
            }
        )
    if people:
        return people

    for fallback_row in fallback_rows:
        fallback_person = _person_from_article_fallback(
            slug=str(fallback_row.get("slug") or ""),
            markdown=str(fallback_row.get("markdown") or ""),
        )
        people.append(
            {
                "slug": fallback_person["slug"],
                "name": fallback_person["name"],
                "title": fallback_person["title"],
                "bucket": fallback_person["bucket"],
                "image_url": fallback_person["image_url"],
                "tags": fallback_person["tags"],
            }
        )
    if people:
        logger.warning(
            "review_display.fetch_all_people.fallback_source source=%s.people_articles_only rows=%s",
            schema_name,
            len(people),
        )
    return people


def _normalize_tag(value: str) -> str:
    return str(value or "").strip().lower()


def _choice_values(choices: Sequence[object]) -> List[str]:
    values: List[str] = []
    for choice in choices or []:
        if isinstance(choice, (tuple, list)) and len(choice) >= 2:
            raw_value = choice[1]
        else:
            raw_value = choice
        text = str(raw_value or "").strip()
        if text and text not in values:
            values.append(text)
    return values


def _normalize_selection(values: Sequence[object] | None) -> List[str]:
    normalized_values: List[str] = []
    seen: set[str] = set()
    for value in values or []:
        text = str(value or "").strip()
        if not text:
            continue
        normalized = _normalize_tag(text)
        if normalized in seen:
            continue
        seen.add(normalized)
        normalized_values.append(text)
    return normalized_values


def _build_tag_filter_choices(people: Sequence[Dict[str, object]]) -> List[Tuple[str, str]]:
    unique_tags: set[str] = set()
    for person in people:
        for raw_tag in person.get("tags", []):
            normalized_tag = _normalize_tag(str(raw_tag))
            if normalized_tag:
                unique_tags.add(normalized_tag)

    sorted_tags = sorted(unique_tags)
    choices: List[Tuple[str, str]] = [(TAG_FILTER_ALL_OPTION, TAG_FILTER_ALL_OPTION)]
    choices.extend((tag, tag) for tag in sorted_tags)
    return choices


def _parse_tag_query_values(raw_query: str) -> List[str]:
    query = str(raw_query or "").strip()
    if not query:
        return []

    # Accept JSON arrays and comma-separated query values.
    if query.startswith("["):
        try:
            parsed = json.loads(query)
        except json.JSONDecodeError:
            parsed = []
        if isinstance(parsed, list):
            return _normalize_selection(parsed)

    values = [part.strip() for part in query.split(",") if part.strip()]
    return _normalize_selection(values)


def _resolve_tag_filter_selection(
    choices: Sequence[object],
    selected_values: Sequence[object] | None,
    *,
    default_to_all: bool,
) -> List[str]:
    all_values = _choice_values(choices)
    if not all_values:
        return []

    all_option_normalized = _normalize_tag(TAG_FILTER_ALL_OPTION)
    allowed_values = [value for value in all_values if _normalize_tag(value) != all_option_normalized]
    if not allowed_values:
        return []

    selected = _normalize_selection(selected_values)
    if not selected:
        return [TAG_FILTER_ALL_OPTION, *allowed_values] if default_to_all else []

    selected_normalized = {_normalize_tag(value) for value in selected}
    filtered_values = [value for value in allowed_values if _normalize_tag(value) in selected_normalized]
    has_all = all_option_normalized in selected_normalized

    if has_all:
        # "All" by itself means select everything.
        if not filtered_values:
            return [TAG_FILTER_ALL_OPTION, *allowed_values]
        # If all concrete options remain selected, keep "All".
        if len(filtered_values) == len(allowed_values):
            return [TAG_FILTER_ALL_OPTION, *allowed_values]
        # If any concrete option was removed, drop "All".
        return filtered_values

    if not filtered_values:
        return [TAG_FILTER_ALL_OPTION, *allowed_values] if default_to_all else []

    # If every concrete option is selected manually, reflect that as "All".
    if len(filtered_values) == len(allowed_values):
        return [TAG_FILTER_ALL_OPTION, *allowed_values]
    return filtered_values


def _build_tag_filter_update(
    people: Sequence[Dict[str, object]],
    selected_values: Sequence[object] | None = None,
    *,
    default_to_all: bool = True,
) -> tuple[gr.update, List[Tuple[str, str]], List[str]]:
    choices = _build_tag_filter_choices(people)
    resolved_selection = _resolve_tag_filter_selection(choices, selected_values, default_to_all=default_to_all)
    return (
        gr.update(choices=choices, value=resolved_selection, interactive=True),
        choices,
        resolved_selection,
    )


def _filter_people_for_tag_selection(
    people: Sequence[Dict[str, object]],
    selected_values: Sequence[object] | None,
) -> List[Dict[str, object]]:
    selected_normalized = {
        _normalize_tag(value)
        for value in _normalize_selection(selected_values)
        if _normalize_tag(value)
    }
    all_key = _normalize_tag(TAG_FILTER_ALL_OPTION)
    if all_key in selected_normalized:
        return list(people)

    selected_normalized.discard(all_key)
    if not selected_normalized:
        return []

    filtered_people: List[Dict[str, object]] = []
    for person in people:
        person_tags = {
            _normalize_tag(str(tag))
            for tag in person.get("tags", [])
            if _normalize_tag(str(tag))
        }
        if person_tags.intersection(selected_normalized):
            filtered_people.append(person)
    return filtered_people


def _update_people_cards_by_tag_filter(
    current_selection: Sequence[object] | None,
    previous_selection: Sequence[object] | None,
):
    people = _fetch_all_people()
    choices = _build_tag_filter_choices(people)
    choice_values = _choice_values(choices)
    all_key = _normalize_tag(TAG_FILTER_ALL_OPTION)
    allowed_values = [value for value in choice_values if _normalize_tag(value) != all_key]

    current_norm = {_normalize_tag(value) for value in _normalize_selection(current_selection)}
    previous_norm = {_normalize_tag(value) for value in _normalize_selection(previous_selection)}
    current_filtered = [value for value in allowed_values if _normalize_tag(value) in current_norm]
    previous_filtered = [value for value in allowed_values if _normalize_tag(value) in previous_norm]

    current_has_all = all_key in current_norm
    previous_has_all = all_key in previous_norm

    next_selection = current_filtered
    dropdown_update = gr.update()

    if current_has_all and not previous_has_all:
        if (not current_filtered) or (current_filtered == previous_filtered):
            next_selection = [TAG_FILTER_ALL_OPTION, *allowed_values]
            dropdown_update = gr.update(value=next_selection)
        elif len(current_filtered) < len(allowed_values):
            next_selection = current_filtered
        else:
            next_selection = [TAG_FILTER_ALL_OPTION, *allowed_values]
    elif (not current_has_all) and previous_has_all and len(current_filtered) == len(allowed_values):
        next_selection = []
        dropdown_update = gr.update(value=next_selection)
    elif current_has_all and previous_has_all and len(current_filtered) < len(previous_filtered):
        next_selection = current_filtered
    elif current_has_all and len(current_filtered) == len(allowed_values):
        next_selection = [TAG_FILTER_ALL_OPTION, *allowed_values]
    elif len(current_filtered) == len(allowed_values) and allowed_values:
        next_selection = [TAG_FILTER_ALL_OPTION, *allowed_values]
        dropdown_update = gr.update(value=next_selection)

    next_has_all = any(_normalize_tag(value) == all_key for value in next_selection)
    if current_has_all and not next_has_all:
        dropdown_update = gr.update(value=next_selection)

    filtered_people = _filter_people_for_tag_selection(people, next_selection)
    cards_update = gr.update(value=_render_cards(filtered_people), visible=True)
    return dropdown_update, next_selection, cards_update


def _fetch_person(slug: str) -> Dict[str, object] | None:
    normalized_slug = (slug or "").strip().lower()
    if not normalized_slug:
        return None

    _ensure_local_db()
    row = None
    fallback_row = None
    with readonly_session_scope() as session:
        schema_name = _resolve_people_schema(session)
        if _table_exists_in_schema(session, schema_name, "people_cards"):
            row = session.execute(
                text(
                    f"""
                    SELECT
                        c.slug,
                        c.person_id,
                        p.name,
                        COALESCE(t.label, 'Sin asignar') AS title,
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

        if row is None and _table_exists_in_schema(session, schema_name, "people_articles"):
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
            "review_display.fallback_profile_source slug=%s source=%s.people_articles_only",
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


def _fetch_theory(slug: str) -> Dict[str, object] | None:
    normalized_slug = (slug or "").strip().lower()
    if not normalized_slug:
        return None

    _ensure_local_db()
    row = None
    fallback_row = None
    with readonly_session_scope() as session:
        schema_name = _resolve_theory_schema(session)
        if _table_exists_in_schema(session, schema_name, "theory_cards"):
            row = session.execute(
                text(
                    f"""
                    SELECT
                        c.slug,
                        c.person_id,
                        p.name,
                        COALESCE(t.label, 'Sin asignar') AS title,
                        c.bucket,
                        c.image_url,
                        COALESCE(
                            (
                                SELECT json_agg(tag_row.label ORDER BY tag_row.label)
                                FROM (
                                    SELECT DISTINCT tg.label
                                    FROM {schema_name}.theory_person_tags ppt
                                    JOIN {schema_name}.theory_tags tg
                                        ON tg.id = ppt.tag_id
                                    WHERE ppt.person_id = c.person_id
                                ) AS tag_row
                            ),
                            '[]'::json
                        )::text AS tags_json,
                        COALESCE(a.markdown, '') AS markdown
                    FROM {schema_name}.theory_cards c
                    JOIN {schema_name}.theories p
                        ON p.id = c.person_id
                    LEFT JOIN {schema_name}.theory_titles t
                        ON t.id = c.title_id
                    LEFT JOIN {schema_name}.theory_articles a
                        ON a.person_slug = c.slug
                    WHERE c.slug = :slug
                    """
                ),
                {"slug": normalized_slug},
            ).mappings().first()

        if row is None and _table_exists_in_schema(session, schema_name, "theory_articles"):
            fallback_row = session.execute(
                text(
                    f"""
                    SELECT
                        person_slug AS slug,
                        COALESCE(markdown, '') AS markdown
                    FROM {schema_name}.theory_articles
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
            "review_display.fallback_theory_source slug=%s source=%s.theory_articles_only",
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


def _fetch_profile_for_source(slug: str, proposal_source: str) -> Dict[str, object] | None:
    normalized_source = _normalize_proposal_source(proposal_source)
    if normalized_source == PROPOSAL_SOURCE_SOURCE:
        return None
    if normalized_source == PROPOSAL_SOURCE_THEORY:
        return _fetch_theory(slug)
    return _fetch_person(slug)


def _record_proposal_event(
    session,
    proposal_id: int,
    event_type: str,
    actor_user_id: int | None,
    notes: str,
    payload: Dict[str, object] | None = None,
    proposal_source: str = PROPOSAL_SOURCE_PEOPLE,
) -> None:
    payload_json = json.dumps(payload or {}, ensure_ascii=True)
    source = _normalize_proposal_source(proposal_source)
    if source == PROPOSAL_SOURCE_SOURCE:
        return
    events_table = "app.theory_change_events" if source == PROPOSAL_SOURCE_THEORY else "app.people_change_events"
    session.execute(
        text(
            f"""
            INSERT INTO {events_table} (
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


def _materialize_missing_profile_for_proposal(
    *,
    proposal_id: int,
    person_slug: str,
    proposal_person_id: int,
    scope: str,
    proposed_payload: str,
    proposed_image_url: str,
    proposal_source: str = PROPOSAL_SOURCE_PEOPLE,
) -> int:
    normalized_slug = (person_slug or "").strip().lower()
    if not normalized_slug:
        raise ValueError("Missing proposal slug.")

    normalized_scope = _normalize_proposal_scope(scope)
    if normalized_scope == PROPOSAL_SCOPE_CARD:
        snapshot = _deserialize_card_snapshot(proposed_payload)
        seed_name = str(snapshot.get("name") or "").strip() or _display_name_from_slug(normalized_slug)
        seed_title = str(snapshot.get("title") or "").strip() or "Sin asignar"
        seed_tags = [_normalize_tag(str(tag)) for tag in snapshot.get("tags", []) if _normalize_tag(str(tag))]
        seed_image_url = (
            str(proposed_image_url or "").strip()
            or str(snapshot.get("image_url") or "").strip()
            or "/images/Logo.png"
        )
        article_seed_markdown = f"# {seed_name}\n\nPerfil con contenido de artículo pendiente."
    elif normalized_scope == PROPOSAL_SCOPE_CARD_ARTICLE:
        # Handle combined card+article payload
        combined = _deserialize_card_article_snapshot(proposed_payload)
        card_data = combined.get("card", {})
        seed_name = str(card_data.get("name") or "").strip() or _display_name_from_slug(normalized_slug)
        seed_title = str(card_data.get("title") or "").strip() or "Sin asignar"
        seed_tags = [_normalize_tag(str(tag)) for tag in card_data.get("tags", []) if _normalize_tag(str(tag))]
        seed_image_url = (
            str(proposed_image_url or "").strip()
            or str(card_data.get("image_url") or "").strip()
            or "/images/Logo.png"
        )
        article_seed_markdown = str(combined.get("article") or "").strip() or f"# {seed_name}\n\nPerfil con contenido de artículo pendiente."
    else:
        article_seed = _person_from_article_fallback(normalized_slug, proposed_payload)
        seed_name = str(article_seed.get("name") or "").strip() or _display_name_from_slug(normalized_slug)
        seed_title = str(article_seed.get("title") or "").strip() or "Sin asignar"
        seed_tags = [
            _normalize_tag(str(tag))
            for tag in article_seed.get("tags", [])
            if _normalize_tag(str(tag))
        ]
        seed_image_url = str(article_seed.get("image_url") or "").strip() or "/images/Logo.png"
        article_seed_markdown = str(proposed_payload or "").strip() or f"# {seed_name}\n\nPerfil con contenido de artículo pendiente."

    source = _normalize_proposal_source(proposal_source)
    is_theory = source == PROPOSAL_SOURCE_THEORY
    people_table = "app.theories" if is_theory else "app.people"
    cards_table = "app.theory_cards" if is_theory else "app.people_cards"
    articles_table = "app.theory_articles" if is_theory else "app.people_articles"
    proposals_table = "app.theory_change_proposals" if is_theory else "app.people_change_proposals"

    resolved_person_id = int(proposal_person_id or 0)
    with session_scope() as session:
        if not is_theory:
            _drop_people_change_proposals_slug_fk(session)
            ensure_people_name_available(session, seed_name, exclude_slug=normalized_slug)
        else:
            ensure_theory_name_available(session, seed_name, exclude_slug=normalized_slug)
        if resolved_person_id > 0:
            person_exists = bool(
                session.execute(
                    text(f"SELECT EXISTS (SELECT 1 FROM {people_table} WHERE id = :person_id)"),
                    {"person_id": resolved_person_id},
                ).scalar_one()
            )
            if not person_exists:
                resolved_person_id = 0
        if resolved_person_id <= 0:
            if is_theory:
                resolved_person_id = ensure_theory_person(session, seed_name)
            else:
                resolved_person_id = ensure_people_person(session, seed_name)

        session.execute(
            text(
                f"""
                UPDATE {people_table}
                SET name = :name,
                    updated_at = now()
                WHERE id = :person_id
                """
            ),
            {"name": seed_name, "person_id": resolved_person_id},
        )

        title_label = seed_title or "Sin asignar"
        if is_theory:
            title_id = ensure_theory_title(session, title_label)
        else:
            title_id = ensure_people_title(session, title_label)
        session.execute(
            text(
                f"""
                INSERT INTO {cards_table} (slug, person_id, title_id, bucket, image_url)
                VALUES (:slug, :person_id, :title_id, :bucket, :image_url)
                ON CONFLICT (slug) DO UPDATE
                SET person_id = EXCLUDED.person_id,
                    title_id = EXCLUDED.title_id,
                    image_url = EXCLUDED.image_url,
                    updated_at = now()
                """
            ),
            {
                "slug": normalized_slug,
                "person_id": resolved_person_id,
                "title_id": title_id,
                "bucket": title_label if is_theory else DEFAULT_MEDIA_BUCKET,
                "image_url": seed_image_url,
            },
        )
        if is_theory:
            sync_theory_card_taxonomy(
                session,
                person_id=resolved_person_id,
                title=title_label,
                tags=seed_tags,
            )
        else:
            sync_people_card_taxonomy(
                session,
                person_id=resolved_person_id,
                title=title_label,
                tags=seed_tags,
            )
        session.execute(
            text(
                f"""
                INSERT INTO {articles_table} (person_slug, markdown)
                VALUES (:person_slug, :markdown)
                ON CONFLICT (person_slug) DO NOTHING
                """
            ),
            {
                "person_slug": normalized_slug,
                "markdown": article_seed_markdown,
            },
        )
        session.execute(
            text(
                f"""
                UPDATE {proposals_table}
                SET person_id = :person_id
                WHERE id = :proposal_id
                """
            ),
            {
                "person_id": resolved_person_id,
                "proposal_id": int(proposal_id),
            },
        )

    return resolved_person_id


def _fetch_change_proposals_legacy_rows(
    session,
    *,
    normalized_slug: str,
    max_limit: int,
) -> List[Dict[str, object]]:
    rows = session.execute(
        text(
            """
            WITH ranked AS (
                SELECT
                    p.id,
                    p.person_slug,
                    p.proposer_user_id,
                    COALESCE(NULLIF(proposer_user.name, ''), proposer_user.email, '') AS proposer_name,
                    COALESCE(proposer_user.email, '') AS proposer_email,
                    p.proposal_scope,
                    p.note,
                    p.status,
                    p.created_at,
                    p.reviewed_at,
                    p.reviewer_user_id,
                    COALESCE(NULLIF(reviewer_user.name, ''), reviewer_user.email, '') AS reviewer_name,
                    COALESCE(reviewer_user.email, '') AS reviewer_email,
                    p.review_note,
                    p.report_triggered,
                    CASE
                        WHEN COALESCE(lower(p.proposal_scope), '') = 'card' THEN 'card'
                        WHEN COALESCE(lower(p.proposal_scope), '') = 'card_article' THEN 'card_article'
                        ELSE 'article'
                    END AS normalized_scope,
                    ROW_NUMBER() OVER (
                        PARTITION BY CASE
                            WHEN COALESCE(lower(p.proposal_scope), '') = 'card' THEN 'card'
                            WHEN COALESCE(lower(p.proposal_scope), '') = 'card_article' THEN 'card_article'
                            ELSE 'article'
                        END
                        ORDER BY p.id ASC
                    ) AS scope_index
                FROM app.people_change_proposals p
                LEFT JOIN app."user" proposer_user
                    ON proposer_user.id = p.proposer_user_id
                LEFT JOIN app."user" reviewer_user
                    ON reviewer_user.id = p.reviewer_user_id
                WHERE COALESCE(lower(p.status), '') NOT IN ('accepted', 'declined')
            )
            SELECT
                id,
                person_slug,
                proposer_user_id,
                proposer_name,
                proposer_email,
                proposal_scope,
                note,
                status,
                created_at,
                reviewed_at,
                reviewer_user_id,
                reviewer_name,
                reviewer_email,
                review_note,
                report_triggered,
                CASE
                    WHEN normalized_scope = 'card'
                        THEN 'C-' || lpad(scope_index::text, 3, '0')
                    WHEN normalized_scope = 'card_article'
                        THEN 'CA-' || lpad(scope_index::text, 3, '0')
                    ELSE 'A-' || lpad(scope_index::text, 3, '0')
                END AS dataset_entry
            FROM ranked
            WHERE (:person_slug = '' OR person_slug = :person_slug)
            ORDER BY
                CASE status
                    WHEN 'pending' THEN 0
                    WHEN 'reported' THEN 1
                    ELSE 2
                END,
                created_at ASC,
                id ASC
            LIMIT :limit
            """
        ),
        {"person_slug": normalized_slug, "limit": max_limit},
    ).mappings().all()
    return [dict(row) for row in rows]


def _fetch_people_change_proposals(
    limit: int = 120,
    slug_filter: str = "",
    *,
    skip_table_check: bool = False,
) -> List[Dict[str, object]]:
    _ = skip_table_check
    _ensure_local_db()
    normalized_slug = (slug_filter or "").strip().lower()
    max_limit = max(1, int(limit))
    with readonly_session_scope() as session:
        try:
            person_id_filter = 0
            if normalized_slug:
                person_id_filter = int(
                    session.execute(
                        text(
                            """
                            SELECT person_id
                            FROM app.people_cards
                            WHERE slug = :slug
                            LIMIT 1
                            """
                        ),
                        {"slug": normalized_slug},
                    ).scalar_one_or_none()
                    or 0
                )
                if person_id_filter <= 0:
                    logger.warning(
                        "review_display.fetch_change_proposals.person_id_not_found slug=%s fallback=legacy_query",
                        normalized_slug,
                    )
                    legacy_rows = _fetch_change_proposals_legacy_rows(
                        session,
                        normalized_slug=normalized_slug,
                        max_limit=max_limit,
                    )
                    for row in legacy_rows:
                        row["proposal_source"] = PROPOSAL_SOURCE_PEOPLE
                    return legacy_rows

            rows = session.execute(
                text(
                    """
                    WITH ranked AS (
                        SELECT
                            p.id,
                            p.person_slug,
                            p.person_id,
                            p.proposer_user_id,
                            COALESCE(NULLIF(proposer_user.name, ''), proposer_user.email, '') AS proposer_name,
                            COALESCE(proposer_user.email, '') AS proposer_email,
                            p.proposal_scope,
                            p.note,
                            p.status,
                            p.created_at,
                            p.reviewed_at,
                            p.reviewer_user_id,
                            COALESCE(NULLIF(reviewer_user.name, ''), reviewer_user.email, '') AS reviewer_name,
                            COALESCE(reviewer_user.email, '') AS reviewer_email,
                            p.review_note,
                            p.report_triggered,
                            CASE
                                WHEN COALESCE(lower(p.proposal_scope), '') = 'card' THEN 'card'
                                WHEN COALESCE(lower(p.proposal_scope), '') = 'card_article' THEN 'card_article'
                                ELSE 'article'
                            END AS normalized_scope,
                            ROW_NUMBER() OVER (
                                PARTITION BY CASE
                                    WHEN COALESCE(lower(p.proposal_scope), '') = 'card' THEN 'card'
                                    WHEN COALESCE(lower(p.proposal_scope), '') = 'card_article' THEN 'card_article'
                                    ELSE 'article'
                                END
                                ORDER BY p.id ASC
                            ) AS scope_index
                        FROM app.people_change_proposals p
                        LEFT JOIN app."user" proposer_user
                            ON proposer_user.id = p.proposer_user_id
                        LEFT JOIN app."user" reviewer_user
                            ON reviewer_user.id = p.reviewer_user_id
                        WHERE COALESCE(lower(p.status), '') NOT IN ('accepted', 'declined')
                          AND (:person_id_filter <= 0 OR p.person_id = :person_id_filter)
                    )
                    SELECT
                        id,
                        person_slug,
                        person_id,
                        proposer_user_id,
                        proposer_name,
                        proposer_email,
                        proposal_scope,
                        note,
                        status,
                        created_at,
                        reviewed_at,
                        reviewer_user_id,
                        reviewer_name,
                        reviewer_email,
                        review_note,
                        report_triggered,
                        CASE
                            WHEN normalized_scope = 'card'
                                THEN 'C-' || lpad(scope_index::text, 3, '0')
                            WHEN normalized_scope = 'card_article'
                                THEN 'CA-' || lpad(scope_index::text, 3, '0')
                            ELSE 'A-' || lpad(scope_index::text, 3, '0')
                        END AS dataset_entry
                    FROM ranked
                    ORDER BY
                        CASE status
                            WHEN 'pending' THEN 0
                            WHEN 'reported' THEN 1
                            ELSE 2
                        END,
                        created_at ASC,
                        id ASC
                    LIMIT :limit
                    """
                ),
                {"person_id_filter": person_id_filter, "limit": max_limit},
            ).mappings().all()
        except SQLAlchemyDatabaseError as exc:
            if _is_missing_relation_error(exc):
                _warn_missing_review_tables(
                    "fetch_change_proposals",
                    (
                        "app.people_change_proposals",
                    ),
                )
                return []
            if _is_missing_column_error(exc, "person_id"):
                logger.warning(
                    "review_display.fetch_change_proposals.missing_person_id_column fallback=legacy_query"
                )
                legacy_rows = _fetch_change_proposals_legacy_rows(
                    session,
                    normalized_slug=normalized_slug,
                    max_limit=max_limit,
                )
                for row in legacy_rows:
                    row["proposal_source"] = PROPOSAL_SOURCE_PEOPLE
                return legacy_rows
            raise
    result_rows = [dict(row) for row in rows]
    for row in result_rows:
        row["proposal_source"] = PROPOSAL_SOURCE_PEOPLE
    return result_rows


def _fetch_theory_change_proposals(
    limit: int = 120,
    slug_filter: str = "",
    *,
    skip_table_check: bool = False,
) -> List[Dict[str, object]]:
    _ = skip_table_check
    _ensure_local_db()
    normalized_slug = (slug_filter or "").strip().lower()
    max_limit = max(1, int(limit))
    with readonly_session_scope() as session:
        try:
            rows = session.execute(
                text(
                    """
                    WITH ranked AS (
                        SELECT
                            p.id,
                            p.person_slug,
                            p.person_id,
                            p.proposer_user_id,
                            COALESCE(NULLIF(proposer_user.name, ''), proposer_user.email, '') AS proposer_name,
                            COALESCE(proposer_user.email, '') AS proposer_email,
                            p.proposal_scope,
                            p.note,
                            p.status,
                            p.created_at,
                            p.reviewed_at,
                            p.reviewer_user_id,
                            COALESCE(NULLIF(reviewer_user.name, ''), reviewer_user.email, '') AS reviewer_name,
                            COALESCE(reviewer_user.email, '') AS reviewer_email,
                            p.review_note,
                            p.report_triggered,
                            CASE
                                WHEN COALESCE(lower(p.proposal_scope), '') = 'card' THEN 'card'
                                WHEN COALESCE(lower(p.proposal_scope), '') = 'card_article' THEN 'card_article'
                                ELSE 'article'
                            END AS normalized_scope,
                            ROW_NUMBER() OVER (
                                PARTITION BY CASE
                                    WHEN COALESCE(lower(p.proposal_scope), '') = 'card' THEN 'card'
                                    WHEN COALESCE(lower(p.proposal_scope), '') = 'card_article' THEN 'card_article'
                                    ELSE 'article'
                                END
                                ORDER BY p.id ASC
                            ) AS scope_index
                        FROM app.theory_change_proposals p
                        LEFT JOIN app."user" proposer_user
                            ON proposer_user.id = p.proposer_user_id
                        LEFT JOIN app."user" reviewer_user
                            ON reviewer_user.id = p.reviewer_user_id
                        WHERE COALESCE(lower(p.status), '') NOT IN ('accepted', 'declined')
                          AND (:person_slug = '' OR p.person_slug = :person_slug)
                    )
                    SELECT
                        id,
                        person_slug,
                        person_id,
                        proposer_user_id,
                        proposer_name,
                        proposer_email,
                        proposal_scope,
                        note,
                        status,
                        created_at,
                        reviewed_at,
                        reviewer_user_id,
                        reviewer_name,
                        reviewer_email,
                        review_note,
                        report_triggered,
                        CASE
                            WHEN normalized_scope = 'card'
                                THEN 'C-' || lpad(scope_index::text, 3, '0')
                            WHEN normalized_scope = 'card_article'
                                THEN 'CA-' || lpad(scope_index::text, 3, '0')
                            ELSE 'A-' || lpad(scope_index::text, 3, '0')
                        END AS dataset_entry
                    FROM ranked
                    ORDER BY
                        CASE status
                            WHEN 'pending' THEN 0
                            WHEN 'reported' THEN 1
                            ELSE 2
                        END,
                        created_at ASC,
                        id ASC
                    LIMIT :limit
                    """
                ),
                {"person_slug": normalized_slug, "limit": max_limit},
            ).mappings().all()
        except SQLAlchemyDatabaseError as exc:
            if _is_missing_relation_error(exc):
                _warn_missing_review_tables(
                    "fetch_theory_change_proposals",
                    (
                        "app.theory_change_proposals",
                    ),
                )
                return []
            raise
    result_rows = [dict(row) for row in rows]
    for row in result_rows:
        row["proposal_source"] = PROPOSAL_SOURCE_THEORY
    return result_rows


def _build_source_push_review_markdown(
    *,
    unsorted_file_id: int,
    file_name: str,
    source_slug: str,
    source_name: str,
    origin_text: str,
    mime_type: str,
    size_bytes: int,
    note: str,
    media_url: str,
) -> tuple[str, str]:
    safe_file_name = (file_name or "").strip() or f"file-{max(0, int(unsorted_file_id or 0))}"
    safe_source_slug = (source_slug or "").strip().lower()
    safe_source_name = (source_name or "").strip() or safe_source_slug or "fuente"
    safe_origin = (origin_text or "").strip() or "n/d"
    safe_mime = (mime_type or "").strip() or "desconocido"
    safe_size = max(0, int(size_bytes or 0))
    safe_note = (note or "").strip()
    safe_media_url = (media_url or "").strip()

    current_lines = [
        "## Estado actual",
        f"- El archivo sin clasificar `#{max(0, int(unsorted_file_id or 0))}` está pendiente de enrutamiento.",
        f"- Archivo: `{safe_file_name}`",
        "- Ubicación actual: `sin-clasificar`",
    ]

    proposed_lines = [
        "## Estado propuesto",
        f"- Mover archivo sin clasificar `#{max(0, int(unsorted_file_id or 0))}` a la fuente `{safe_source_slug or 'desconocida'}`.",
        f"- Nombre de la fuente: `{safe_source_name}`",
        f"- Archivo: `{safe_file_name}`",
        f"- Tipo MIME: `{safe_mime}`",
        f"- Tamaño del archivo (bytes): `{safe_size}`",
        f"- Origen/descripción: {safe_origin}",
    ]
    if safe_note:
        proposed_lines.append(f"- Nota del usuario: {safe_note}")
    if safe_media_url:
        proposed_lines.append(f"- URL del archivo: {safe_media_url}")

    return ("\n".join(current_lines), "\n".join(proposed_lines))


def _format_tags_markdown(tags: Sequence[str]) -> str:
    cleaned: List[str] = []
    seen: set[str] = set()
    for raw_tag in tags:
        normalized = _normalize_tag(str(raw_tag))
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        cleaned.append(normalized)
    if not cleaned:
        return "(ninguna)"
    return ", ".join(f"`{tag}`" for tag in cleaned)


def _build_source_tags_review_markdown(
    *,
    unsorted_file_id: int,
    file_name: str,
    origin_text: str,
    mime_type: str,
    size_bytes: int,
    current_tags: Sequence[str],
    proposed_tags: Sequence[str],
    note: str,
    media_url: str,
) -> tuple[str, str]:
    safe_file_name = (file_name or "").strip() or f"file-{max(0, int(unsorted_file_id or 0))}"
    safe_origin = (origin_text or "").strip() or "n/d"
    safe_mime = (mime_type or "").strip() or "desconocido"
    safe_size = max(0, int(size_bytes or 0))
    safe_note = (note or "").strip()
    safe_media_url = (media_url or "").strip()
    current_tags_md = _format_tags_markdown(current_tags)
    proposed_tags_md = _format_tags_markdown(proposed_tags)

    current_lines = [
        "## Estado actual",
        f"- Estado de propuesta de etiquetas para archivo sin clasificar `#{max(0, int(unsorted_file_id or 0))}`.",
        f"- Archivo: `{safe_file_name}`",
        f"- Tipo MIME: `{safe_mime}`",
        f"- Tamaño del archivo (bytes): `{safe_size}`",
        f"- Origen/descripción: {safe_origin}",
        f"- Etiquetas aceptadas actuales: {current_tags_md}",
    ]

    proposed_lines = [
        "## Estado propuesto",
        f"- Actualizar etiquetas para el archivo sin clasificar `#{max(0, int(unsorted_file_id or 0))}`.",
        f"- Archivo: `{safe_file_name}`",
        f"- Etiquetas propuestas: {proposed_tags_md}",
    ]
    if safe_note:
        proposed_lines.append(f"- Nota del usuario: {safe_note}")
    if safe_media_url:
        proposed_lines.append(f"- URL del archivo: {safe_media_url}")

    return ("\n".join(current_lines), "\n".join(proposed_lines))


def _fetch_source_change_proposals(
    limit: int = 120,
    slug_filter: str = "",
    *,
    skip_table_check: bool = False,
) -> List[Dict[str, object]]:
    _ = skip_table_check
    _ensure_local_db()
    normalized_slug = (slug_filter or "").strip().lower()
    max_limit = max(1, int(limit))
    with readonly_session_scope() as session:
        has_push_proposals = _table_exists_in_search_path(session, "unsorted_file_push_proposals")
        has_tag_proposals = _table_exists_in_search_path(session, "unsorted_file_tag_proposals")
        has_tag_rows = has_tag_proposals and _table_exists_in_search_path(session, "unsorted_file_tag_proposal_tags")

        if not has_push_proposals and not has_tag_proposals:
            _warn_missing_review_tables(
                "fetch_source_change_proposals",
                (
                    "app.unsorted_file_push_proposals",
                    "app.unsorted_file_tag_proposals",
                ),
            )
            return []

        combined_rows: List[Dict[str, object]] = []
        try:
            if has_push_proposals:
                push_rows = session.execute(
                    text(
                        """
                        SELECT
                            p.id,
                            COALESCE(p.source_slug, '') AS person_slug,
                            p.proposer_user_id,
                            COALESCE(NULLIF(proposer_user.name, ''), proposer_user.email, '') AS proposer_name,
                            COALESCE(proposer_user.email, '') AS proposer_email,
                            'source' AS proposal_scope,
                            COALESCE(p.note, '') AS note,
                            COALESCE(p.status, '') AS status,
                            p.created_at,
                            p.reviewed_at,
                            0::bigint AS reviewer_user_id,
                            ''::text AS reviewer_name,
                            ''::text AS reviewer_email,
                            ''::text AS review_note,
                            0::integer AS report_triggered,
                            p.unsorted_file_id,
                            COALESCE(uf.file_name, '') AS unsorted_file_name,
                            COALESCE(uf.origin_text, '') AS unsorted_origin_text,
                            COALESCE(uf.mime_type, '') AS unsorted_mime_type,
                            COALESCE(uf.size_bytes, 0)::bigint AS unsorted_size_bytes,
                            COALESCE(uf.bucket, '') AS unsorted_bucket,
                            COALESCE(uf.blob_path, '') AS unsorted_blob_path,
                            COALESCE(s.slug, p.source_slug, '') AS source_slug,
                            COALESCE(s.name, '') AS source_name,
                            p.source_id,
                            '[]'::text AS current_tags_json,
                            '[]'::text AS proposed_tags_json,
                            :push_type AS source_proposal_type
                        FROM app.unsorted_file_push_proposals p
                        LEFT JOIN app.unsorted_files uf
                            ON uf.id = p.unsorted_file_id
                        LEFT JOIN app.sources_cards s
                            ON s.id = p.source_id
                        LEFT JOIN app."user" proposer_user
                            ON proposer_user.id = p.proposer_user_id
                        WHERE COALESCE(lower(p.status), '') NOT IN ('accepted', 'declined')
                          AND (:source_slug = '' OR COALESCE(lower(p.source_slug), '') = :source_slug)
                        """
                    ),
                    {
                        "source_slug": normalized_slug,
                        "push_type": SOURCE_PROPOSAL_TYPE_PUSH,
                    },
                ).mappings().all()
                combined_rows.extend(dict(row) for row in push_rows)

            if has_tag_proposals:
                proposed_tags_select = (
                    """
                    COALESCE(
                        (
                            SELECT json_agg(utpt.tag_label ORDER BY lower(utpt.tag_label), utpt.tag_label)
                            FROM app.unsorted_file_tag_proposal_tags utpt
                            WHERE utpt.proposal_id = p.id
                        ),
                        '[]'::json
                    )::text AS proposed_tags_json,
                    """
                    if has_tag_rows
                    else "COALESCE(p.tags_json, '[]') AS proposed_tags_json,"
                )
                tag_rows = session.execute(
                    text(
                        """
                        SELECT
                            p.id,
                            ('unsorted-file-' || p.unsorted_file_id::text) AS person_slug,
                            p.proposer_user_id,
                            COALESCE(NULLIF(proposer_user.name, ''), proposer_user.email, '') AS proposer_name,
                            COALESCE(proposer_user.email, '') AS proposer_email,
                            'source' AS proposal_scope,
                            COALESCE(p.note, '') AS note,
                            COALESCE(p.status, '') AS status,
                            p.created_at,
                            p.reviewed_at,
                            COALESCE(p.reviewer_user_id, 0)::bigint AS reviewer_user_id,
                            COALESCE(NULLIF(reviewer_user.name, ''), reviewer_user.email, '') AS reviewer_name,
                            COALESCE(reviewer_user.email, '') AS reviewer_email,
                            COALESCE(p.review_note, '') AS review_note,
                            0::integer AS report_triggered,
                            p.unsorted_file_id,
                            COALESCE(uf.file_name, '') AS unsorted_file_name,
                            COALESCE(uf.origin_text, '') AS unsorted_origin_text,
                            COALESCE(uf.mime_type, '') AS unsorted_mime_type,
                            COALESCE(uf.size_bytes, 0)::bigint AS unsorted_size_bytes,
                            COALESCE(uf.bucket, '') AS unsorted_bucket,
                            COALESCE(uf.blob_path, '') AS unsorted_blob_path,
                            ('unsorted-file-' || p.unsorted_file_id::text) AS source_slug,
                            ''::text AS source_name,
                            0::bigint AS source_id,
                            COALESCE(
                                (
                                    SELECT COALESCE(prev.tags_json, '[]')
                                    FROM app.unsorted_file_tag_proposals prev
                                    WHERE prev.unsorted_file_id = p.unsorted_file_id
                                      AND COALESCE(lower(prev.status), '') = 'accepted'
                                    ORDER BY prev.reviewed_at DESC NULLS LAST, prev.created_at DESC, prev.id DESC
                                    LIMIT 1
                                ),
                                '[]'
                            ) AS current_tags_json,
                            """
                        + proposed_tags_select
                        + """
                            :tags_type AS source_proposal_type
                        FROM app.unsorted_file_tag_proposals p
                        LEFT JOIN app.unsorted_files uf
                            ON uf.id = p.unsorted_file_id
                        LEFT JOIN app."user" proposer_user
                            ON proposer_user.id = p.proposer_user_id
                        LEFT JOIN app."user" reviewer_user
                            ON reviewer_user.id = p.reviewer_user_id
                        WHERE COALESCE(lower(p.status), '') NOT IN ('accepted', 'declined')
                          AND (
                            :tag_slug_filter = ''
                            OR ('unsorted-file-' || p.unsorted_file_id::text) = :tag_slug_filter
                          )
                        """
                    ),
                    {
                        "tag_slug_filter": normalized_slug,
                        "tags_type": SOURCE_PROPOSAL_TYPE_TAGS,
                    },
                ).mappings().all()
                combined_rows.extend(dict(row) for row in tag_rows)
        except SQLAlchemyDatabaseError as exc:
            if _is_missing_relation_error(exc):
                _warn_missing_review_tables(
                    "fetch_source_change_proposals",
                    (
                        "app.unsorted_file_push_proposals",
                        "app.unsorted_file_tag_proposals",
                    ),
                )
                return []
            raise

    combined_rows.sort(
        key=lambda row: (
            _proposal_status_rank(row.get("status")),
            _proposal_created_sort_value(row.get("created_at")),
            int(row.get("id") or 0),
            _normalize_source_proposal_type(row.get("source_proposal_type")),
        )
    )
    result_rows = combined_rows[:max_limit]
    for index, row in enumerate(result_rows, start=1):
        row["dataset_entry"] = str(row.get("dataset_entry") or "").strip() or f"S-{index:03d}"
        row["proposal_source"] = PROPOSAL_SOURCE_SOURCE
        row["source_proposal_type"] = _normalize_source_proposal_type(row.get("source_proposal_type"))
    return result_rows


def _proposal_status_rank(status_value: object) -> int:
    normalized = str(status_value or "").strip().lower()
    if normalized == "pending":
        return 0
    if normalized == "reported":
        return 1
    return 2


def _proposal_created_sort_value(created_value: object) -> datetime:
    if isinstance(created_value, datetime):
        if created_value.tzinfo is not None:
            return created_value
        return created_value.replace(tzinfo=timezone.utc)
    raw_text = str(created_value or "").strip()
    if raw_text:
        try:
            parsed = datetime.fromisoformat(raw_text.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed
        except ValueError:
            pass
    return datetime.max.replace(tzinfo=timezone.utc)


def _fetch_change_proposals(
    limit: int = 120,
    slug_filter: str = "",
    *,
    skip_table_check: bool = False,
) -> List[Dict[str, object]]:
    max_limit = max(1, int(limit))
    people_rows = _fetch_people_change_proposals(
        limit=max_limit,
        slug_filter=slug_filter,
        skip_table_check=skip_table_check,
    )
    theory_rows = _fetch_theory_change_proposals(
        limit=max_limit,
        slug_filter=slug_filter,
        skip_table_check=skip_table_check,
    )
    source_rows = _fetch_source_change_proposals(
        limit=max_limit,
        slug_filter=slug_filter,
        skip_table_check=skip_table_check,
    )
    combined_rows = [*people_rows, *theory_rows, *source_rows]
    combined_rows.sort(
        key=lambda row: (
            _proposal_status_rank(row.get("status")),
            _proposal_created_sort_value(row.get("created_at")),
            int(row.get("id") or 0),
            _normalize_proposal_source(row.get("proposal_source")),
        )
    )
    return combined_rows[:max_limit]


def _fetch_people_proposal_by_id(proposal_id: int) -> Dict[str, object] | None:
    _ensure_local_db()
    with readonly_session_scope() as session:
        try:
            row = session.execute(
                text(
                    """
                    SELECT
                        p.id,
                        p.person_slug,
                        p.proposer_user_id,
                        COALESCE(NULLIF(proposer_user.name, ''), proposer_user.email, '') AS proposer_name,
                        COALESCE(proposer_user.email, '') AS proposer_email,
                        p.proposal_scope,
                        COALESCE(p.base_payload, '') AS base_payload,
                        COALESCE(p.proposed_payload, '') AS proposed_payload,
                        p.note,
                        p.status,
                        p.created_at,
                        p.reviewed_at,
                        p.reviewer_user_id,
                        COALESCE(NULLIF(reviewer_user.name, ''), reviewer_user.email, '') AS reviewer_name,
                        COALESCE(reviewer_user.email, '') AS reviewer_email,
                        p.review_note,
                        p.report_triggered,
                        COALESCE(p.person_id, c.person_id, 0) AS person_id,
                        c.slug AS current_person_slug,
                        COALESCE(c.person_id, 0) AS current_person_id,
                        COALESCE(person.name, '') AS current_name,
                        COALESCE(title.label, 'Sin asignar') AS current_title,
                        COALESCE(c.bucket, '') AS current_bucket,
                        COALESCE(c.image_url, '') AS current_image_url,
                        COALESCE(article.markdown, '') AS current_markdown,
                        COALESCE(
                            (
                                SELECT json_agg(tag_row.label ORDER BY tag_row.label)
                                FROM (
                                    SELECT DISTINCT tg.label
                                    FROM app.people_person_tags ppt
                                    JOIN app.people_tags tg
                                        ON tg.id = ppt.tag_id
                                    WHERE ppt.person_id = c.person_id
                                ) AS tag_row
                            ),
                            '[]'::json
                        )::text AS current_tags_json,
                        COALESCE(
                            (
                                SELECT json_agg(event_row ORDER BY (event_row->>'created_at') DESC, (event_row->>'id')::bigint DESC)
                                FROM (
                                    SELECT
                                        json_build_object(
                                            'id', e.id,
                                            'proposal_id', e.proposal_id,
                                            'event_type', e.event_type,
                                            'actor_user_id', e.actor_user_id,
                                            'actor_name', COALESCE(NULLIF(actor_user.name, ''), actor_user.email, ''),
                                            'actor_email', COALESCE(actor_user.email, ''),
                                            'notes', e.notes,
                                            'payload_json', e.payload_json,
                                            'created_at', e.created_at
                                        ) AS event_row
                                    FROM app.people_change_events e
                                    LEFT JOIN app."user" actor_user
                                        ON actor_user.id = e.actor_user_id
                                    WHERE e.proposal_id = p.id
                                    ORDER BY e.created_at DESC, e.id DESC
                                    LIMIT 30
                                ) AS event_rows
                            ),
                            '[]'::json
                        )::text AS events_json
                    FROM app.people_change_proposals p
                    LEFT JOIN app."user" proposer_user
                        ON proposer_user.id = p.proposer_user_id
                    LEFT JOIN app."user" reviewer_user
                        ON reviewer_user.id = p.reviewer_user_id
                    LEFT JOIN app.people_cards c
                        ON c.slug = p.person_slug
                    LEFT JOIN app.people person
                        ON person.id = c.person_id
                    LEFT JOIN app.people_titles title
                        ON title.id = c.title_id
                    LEFT JOIN app.people_articles article
                        ON article.person_slug = c.slug
                    WHERE p.id = :proposal_id
                    """
                ),
                {"proposal_id": int(proposal_id)},
            ).mappings().first()
            if row is None:
                return None
            proposal = dict(row)
            proposal_scope = _normalize_proposal_scope(proposal.get("proposal_scope"))
            base_payload = str(proposal.get("base_payload") or "")
            proposed_payload = str(proposal.get("proposed_payload") or "")
            if proposal_scope == PROPOSAL_SCOPE_CARD:
                base_snapshot = _deserialize_card_snapshot(base_payload)
                proposed_snapshot = _deserialize_card_snapshot(proposed_payload, fallback=base_snapshot)
                proposal["base_markdown"] = _serialize_card_snapshot(base_snapshot)
                proposal["proposed_markdown"] = _serialize_card_snapshot(proposed_snapshot)
                proposal["base_image_url"] = str(base_snapshot.get("image_url") or "").strip()
                proposal["proposed_image_url"] = str(proposed_snapshot.get("image_url") or "").strip()
            else:
                proposal["base_markdown"] = base_payload
                proposal["proposed_markdown"] = proposed_payload
                proposal["base_image_url"] = ""
                proposal["proposed_image_url"] = ""
            proposal["person_id"] = int(proposal.get("person_id") or 0)
            proposal["current_person_id"] = int(proposal.get("current_person_id") or 0)
            proposal["proposal_source"] = PROPOSAL_SOURCE_PEOPLE
        except SQLAlchemyDatabaseError as exc:
            if _is_missing_column_error(exc, "person_id"):
                logger.warning(
                    "review_display.fetch_proposal_by_id.missing_person_id_column fallback=legacy_query"
                )
                row = session.execute(
                    text(
                        """
                        SELECT
                            p.id,
                            p.person_slug,
                            p.proposer_user_id,
                            COALESCE(NULLIF(proposer_user.name, ''), proposer_user.email, '') AS proposer_name,
                            COALESCE(proposer_user.email, '') AS proposer_email,
                            p.proposal_scope,
                            COALESCE(p.base_payload, p.base_markdown, '') AS base_payload,
                            COALESCE(p.proposed_payload, p.proposed_markdown, '') AS proposed_payload,
                            p.note,
                            p.status,
                            p.created_at,
                            p.reviewed_at,
                            p.reviewer_user_id,
                            COALESCE(NULLIF(reviewer_user.name, ''), reviewer_user.email, '') AS reviewer_name,
                            COALESCE(reviewer_user.email, '') AS reviewer_email,
                            p.review_note,
                            p.report_triggered,
                            COALESCE(c.person_id, 0) AS person_id,
                            c.slug AS current_person_slug,
                            COALESCE(c.person_id, 0) AS current_person_id,
                            COALESCE(person.name, '') AS current_name,
                            COALESCE(title.label, 'Sin asignar') AS current_title,
                            COALESCE(c.bucket, '') AS current_bucket,
                            COALESCE(c.image_url, '') AS current_image_url,
                            COALESCE(article.markdown, '') AS current_markdown,
                            COALESCE(
                                (
                                    SELECT json_agg(tag_row.label ORDER BY tag_row.label)
                                    FROM (
                                        SELECT DISTINCT tg.label
                                        FROM app.people_person_tags ppt
                                        JOIN app.people_tags tg
                                            ON tg.id = ppt.tag_id
                                        WHERE ppt.person_id = c.person_id
                                    ) AS tag_row
                                ),
                                '[]'::json
                            )::text AS current_tags_json,
                            COALESCE(
                                (
                                    SELECT json_agg(event_row ORDER BY (event_row->>'created_at') DESC, (event_row->>'id')::bigint DESC)
                                    FROM (
                                        SELECT
                                            json_build_object(
                                                'id', e.id,
                                                'proposal_id', e.proposal_id,
                                                'event_type', e.event_type,
                                                'actor_user_id', e.actor_user_id,
                                                'actor_name', COALESCE(NULLIF(actor_user.name, ''), actor_user.email, ''),
                                                'actor_email', COALESCE(actor_user.email, ''),
                                                'notes', e.notes,
                                                'payload_json', e.payload_json,
                                                'created_at', e.created_at
                                            ) AS event_row
                                        FROM app.people_change_events e
                                        LEFT JOIN app."user" actor_user
                                            ON actor_user.id = e.actor_user_id
                                        WHERE e.proposal_id = p.id
                                        ORDER BY e.created_at DESC, e.id DESC
                                        LIMIT 30
                                    ) AS event_rows
                                ),
                                '[]'::json
                            )::text AS events_json
                        FROM app.people_change_proposals p
                        LEFT JOIN app."user" proposer_user
                            ON proposer_user.id = p.proposer_user_id
                        LEFT JOIN app."user" reviewer_user
                            ON reviewer_user.id = p.reviewer_user_id
                        LEFT JOIN app.people_cards c
                            ON c.slug = p.person_slug
                        LEFT JOIN app.people person
                            ON person.id = c.person_id
                        LEFT JOIN app.people_titles title
                            ON title.id = c.title_id
                        LEFT JOIN app.people_articles article
                            ON article.person_slug = c.slug
                        WHERE p.id = :proposal_id
                        """
                    ),
                    {"proposal_id": int(proposal_id)},
                ).mappings().first()
                if row is None:
                    return None
                proposal = dict(row)
                proposal_scope = _normalize_proposal_scope(proposal.get("proposal_scope"))
                base_payload = str(proposal.get("base_payload") or "")
                proposed_payload = str(proposal.get("proposed_payload") or "")
                if proposal_scope == PROPOSAL_SCOPE_CARD:
                    base_snapshot = _deserialize_card_snapshot(base_payload)
                    proposed_snapshot = _deserialize_card_snapshot(proposed_payload, fallback=base_snapshot)
                    proposal["base_markdown"] = _serialize_card_snapshot(base_snapshot)
                    proposal["proposed_markdown"] = _serialize_card_snapshot(proposed_snapshot)
                    proposal["base_image_url"] = str(base_snapshot.get("image_url") or "").strip()
                    proposal["proposed_image_url"] = str(proposed_snapshot.get("image_url") or "").strip()
                else:
                    proposal["base_markdown"] = base_payload
                    proposal["proposed_markdown"] = proposed_payload
                    proposal["base_image_url"] = ""
                    proposal["proposed_image_url"] = ""
                proposal["person_id"] = int(proposal.get("person_id") or 0)
                proposal["current_person_id"] = int(proposal.get("current_person_id") or 0)
                proposal["proposal_source"] = PROPOSAL_SOURCE_PEOPLE
                return proposal
            if _is_missing_relation_error(exc):
                _warn_missing_review_tables(
                    "fetch_proposal_by_id",
                    (
                        "app.people_change_proposals",
                    ),
                )
                return None
            raise
    return proposal


def _fetch_theory_proposal_by_id(proposal_id: int) -> Dict[str, object] | None:
    _ensure_local_db()
    with readonly_session_scope() as session:
        try:
            row = session.execute(
                text(
                    """
                    SELECT
                        p.id,
                        p.person_slug,
                        p.proposer_user_id,
                        COALESCE(NULLIF(proposer_user.name, ''), proposer_user.email, '') AS proposer_name,
                        COALESCE(proposer_user.email, '') AS proposer_email,
                        p.proposal_scope,
                        COALESCE(p.base_payload, '') AS base_payload,
                        COALESCE(p.proposed_payload, '') AS proposed_payload,
                        p.note,
                        p.status,
                        p.created_at,
                        p.reviewed_at,
                        p.reviewer_user_id,
                        COALESCE(NULLIF(reviewer_user.name, ''), reviewer_user.email, '') AS reviewer_name,
                        COALESCE(reviewer_user.email, '') AS reviewer_email,
                        p.review_note,
                        p.report_triggered,
                        COALESCE(p.person_id, c.person_id, 0) AS person_id,
                        c.slug AS current_person_slug,
                        COALESCE(c.person_id, 0) AS current_person_id,
                        COALESCE(person.name, '') AS current_name,
                        COALESCE(title.label, 'Sin asignar') AS current_title,
                        COALESCE(c.bucket, '') AS current_bucket,
                        COALESCE(c.image_url, '') AS current_image_url,
                        COALESCE(article.markdown, '') AS current_markdown,
                        COALESCE(
                            (
                                SELECT json_agg(tag_row.label ORDER BY tag_row.label)
                                FROM (
                                    SELECT DISTINCT tg.label
                                    FROM app.theory_person_tags ppt
                                    JOIN app.theory_tags tg
                                        ON tg.id = ppt.tag_id
                                    WHERE ppt.person_id = c.person_id
                                ) AS tag_row
                            ),
                            '[]'::json
                        )::text AS current_tags_json,
                        COALESCE(
                            (
                                SELECT json_agg(event_row ORDER BY (event_row->>'created_at') DESC, (event_row->>'id')::bigint DESC)
                                FROM (
                                    SELECT
                                        json_build_object(
                                            'id', e.id,
                                            'proposal_id', e.proposal_id,
                                            'event_type', e.event_type,
                                            'actor_user_id', e.actor_user_id,
                                            'actor_name', COALESCE(NULLIF(actor_user.name, ''), actor_user.email, ''),
                                            'actor_email', COALESCE(actor_user.email, ''),
                                            'notes', e.notes,
                                            'payload_json', e.payload_json,
                                            'created_at', e.created_at
                                        ) AS event_row
                                    FROM app.theory_change_events e
                                    LEFT JOIN app."user" actor_user
                                        ON actor_user.id = e.actor_user_id
                                    WHERE e.proposal_id = p.id
                                    ORDER BY e.created_at DESC, e.id DESC
                                    LIMIT 30
                                ) AS event_rows
                            ),
                            '[]'::json
                        )::text AS events_json
                    FROM app.theory_change_proposals p
                    LEFT JOIN app."user" proposer_user
                        ON proposer_user.id = p.proposer_user_id
                    LEFT JOIN app."user" reviewer_user
                        ON reviewer_user.id = p.reviewer_user_id
                    LEFT JOIN app.theory_cards c
                        ON c.slug = p.person_slug
                    LEFT JOIN app.theories person
                        ON person.id = c.person_id
                    LEFT JOIN app.theory_titles title
                        ON title.id = c.title_id
                    LEFT JOIN app.theory_articles article
                        ON article.person_slug = c.slug
                    WHERE p.id = :proposal_id
                    """
                ),
                {"proposal_id": int(proposal_id)},
            ).mappings().first()
            if row is None:
                return None
            proposal = dict(row)
            proposal_scope = _normalize_proposal_scope(proposal.get("proposal_scope"))
            base_payload = str(proposal.get("base_payload") or "")
            proposed_payload = str(proposal.get("proposed_payload") or "")
            if proposal_scope == PROPOSAL_SCOPE_CARD:
                base_snapshot = _deserialize_card_snapshot(base_payload)
                proposed_snapshot = _deserialize_card_snapshot(proposed_payload, fallback=base_snapshot)
                proposal["base_markdown"] = _serialize_card_snapshot(base_snapshot)
                proposal["proposed_markdown"] = _serialize_card_snapshot(proposed_snapshot)
                proposal["base_image_url"] = str(base_snapshot.get("image_url") or "").strip()
                proposal["proposed_image_url"] = str(proposed_snapshot.get("image_url") or "").strip()
            else:
                proposal["base_markdown"] = base_payload
                proposal["proposed_markdown"] = proposed_payload
                proposal["base_image_url"] = ""
                proposal["proposed_image_url"] = ""
            proposal["person_id"] = int(proposal.get("person_id") or 0)
            proposal["current_person_id"] = int(proposal.get("current_person_id") or 0)
            proposal["proposal_source"] = PROPOSAL_SOURCE_THEORY
        except SQLAlchemyDatabaseError as exc:
            if _is_missing_column_error(exc, "person_id"):
                logger.warning(
                    "review_display.fetch_theory_proposal_by_id.missing_person_id_column fallback=legacy_query"
                )
                row = session.execute(
                    text(
                        """
                        SELECT
                            p.id,
                            p.person_slug,
                            p.proposer_user_id,
                            COALESCE(NULLIF(proposer_user.name, ''), proposer_user.email, '') AS proposer_name,
                            COALESCE(proposer_user.email, '') AS proposer_email,
                            p.proposal_scope,
                            COALESCE(p.base_payload, p.base_markdown, '') AS base_payload,
                            COALESCE(p.proposed_payload, p.proposed_markdown, '') AS proposed_payload,
                            p.note,
                            p.status,
                            p.created_at,
                            p.reviewed_at,
                            p.reviewer_user_id,
                            COALESCE(NULLIF(reviewer_user.name, ''), reviewer_user.email, '') AS reviewer_name,
                            COALESCE(reviewer_user.email, '') AS reviewer_email,
                            p.review_note,
                            p.report_triggered,
                            COALESCE(c.person_id, 0) AS person_id,
                            c.slug AS current_person_slug,
                            COALESCE(c.person_id, 0) AS current_person_id,
                            COALESCE(person.name, '') AS current_name,
                            COALESCE(title.label, 'Sin asignar') AS current_title,
                            COALESCE(c.bucket, '') AS current_bucket,
                            COALESCE(c.image_url, '') AS current_image_url,
                            COALESCE(article.markdown, '') AS current_markdown,
                            COALESCE(
                                (
                                    SELECT json_agg(tag_row.label ORDER BY tag_row.label)
                                    FROM (
                                        SELECT DISTINCT tg.label
                                        FROM app.theory_person_tags ppt
                                        JOIN app.theory_tags tg
                                            ON tg.id = ppt.tag_id
                                        WHERE ppt.person_id = c.person_id
                                    ) AS tag_row
                                ),
                                '[]'::json
                            )::text AS current_tags_json,
                            COALESCE(
                                (
                                    SELECT json_agg(event_row ORDER BY (event_row->>'created_at') DESC, (event_row->>'id')::bigint DESC)
                                    FROM (
                                        SELECT
                                            json_build_object(
                                                'id', e.id,
                                                'proposal_id', e.proposal_id,
                                                'event_type', e.event_type,
                                                'actor_user_id', e.actor_user_id,
                                                'actor_name', COALESCE(NULLIF(actor_user.name, ''), actor_user.email, ''),
                                                'actor_email', COALESCE(actor_user.email, ''),
                                                'notes', e.notes,
                                                'payload_json', e.payload_json,
                                                'created_at', e.created_at
                                            ) AS event_row
                                        FROM app.theory_change_events e
                                        LEFT JOIN app."user" actor_user
                                            ON actor_user.id = e.actor_user_id
                                        WHERE e.proposal_id = p.id
                                        ORDER BY e.created_at DESC, e.id DESC
                                        LIMIT 30
                                    ) AS event_rows
                                ),
                                '[]'::json
                            )::text AS events_json
                        FROM app.theory_change_proposals p
                        LEFT JOIN app."user" proposer_user
                            ON proposer_user.id = p.proposer_user_id
                        LEFT JOIN app."user" reviewer_user
                            ON reviewer_user.id = p.reviewer_user_id
                        LEFT JOIN app.theory_cards c
                            ON c.slug = p.person_slug
                        LEFT JOIN app.theories person
                            ON person.id = c.person_id
                        LEFT JOIN app.theory_titles title
                            ON title.id = c.title_id
                        LEFT JOIN app.theory_articles article
                            ON article.person_slug = c.slug
                        WHERE p.id = :proposal_id
                        """
                    ),
                    {"proposal_id": int(proposal_id)},
                ).mappings().first()
                if row is None:
                    return None
                proposal = dict(row)
                proposal_scope = _normalize_proposal_scope(proposal.get("proposal_scope"))
                base_payload = str(proposal.get("base_payload") or "")
                proposed_payload = str(proposal.get("proposed_payload") or "")
                if proposal_scope == PROPOSAL_SCOPE_CARD:
                    base_snapshot = _deserialize_card_snapshot(base_payload)
                    proposed_snapshot = _deserialize_card_snapshot(proposed_payload, fallback=base_snapshot)
                    proposal["base_markdown"] = _serialize_card_snapshot(base_snapshot)
                    proposal["proposed_markdown"] = _serialize_card_snapshot(proposed_snapshot)
                    proposal["base_image_url"] = str(base_snapshot.get("image_url") or "").strip()
                    proposal["proposed_image_url"] = str(proposed_snapshot.get("image_url") or "").strip()
                else:
                    proposal["base_markdown"] = base_payload
                    proposal["proposed_markdown"] = proposed_payload
                    proposal["base_image_url"] = ""
                    proposal["proposed_image_url"] = ""
                proposal["person_id"] = int(proposal.get("person_id") or 0)
                proposal["current_person_id"] = int(proposal.get("current_person_id") or 0)
                proposal["proposal_source"] = PROPOSAL_SOURCE_THEORY
                return proposal
            if _is_missing_relation_error(exc):
                _warn_missing_review_tables(
                    "fetch_theory_proposal_by_id",
                    (
                        "app.theory_change_proposals",
                    ),
                )
                return None
            raise
    return proposal


def _fetch_source_proposal_by_id(
    proposal_id: int,
    *,
    source_proposal_type: str = "",
) -> Dict[str, object] | None:
    _ensure_local_db()
    with readonly_session_scope() as session:
        has_push_proposals = _table_exists_in_search_path(session, "unsorted_file_push_proposals")
        has_tag_proposals = _table_exists_in_search_path(session, "unsorted_file_tag_proposals")
        has_tag_rows = has_tag_proposals and _table_exists_in_search_path(session, "unsorted_file_tag_proposal_tags")
        normalized_requested_type = (
            _normalize_source_proposal_type(source_proposal_type)
            if str(source_proposal_type or "").strip()
            else ""
        )
        if not has_push_proposals and not has_tag_proposals:
            _warn_missing_review_tables(
                "fetch_source_proposal_by_id",
                (
                    "app.unsorted_file_push_proposals",
                    "app.unsorted_file_tag_proposals",
                ),
            )
            return None
        try:
            row = None
            if normalized_requested_type in {"", SOURCE_PROPOSAL_TYPE_PUSH} and has_push_proposals:
                row = session.execute(
                    text(
                        """
                        SELECT
                            p.id,
                            COALESCE(p.source_slug, '') AS person_slug,
                            p.proposer_user_id,
                            COALESCE(NULLIF(proposer_user.name, ''), proposer_user.email, '') AS proposer_name,
                            COALESCE(proposer_user.email, '') AS proposer_email,
                            'source' AS proposal_scope,
                            COALESCE(p.note, '') AS note,
                            COALESCE(p.status, '') AS status,
                            p.created_at,
                            p.reviewed_at,
                            0::bigint AS reviewer_user_id,
                            ''::text AS reviewer_name,
                            ''::text AS reviewer_email,
                            ''::text AS review_note,
                            0::integer AS report_triggered,
                            p.unsorted_file_id,
                            COALESCE(uf.file_name, '') AS unsorted_file_name,
                            COALESCE(uf.origin_text, '') AS unsorted_origin_text,
                            COALESCE(uf.mime_type, '') AS unsorted_mime_type,
                            COALESCE(uf.size_bytes, 0)::bigint AS unsorted_size_bytes,
                            COALESCE(uf.bucket, '') AS unsorted_bucket,
                            COALESCE(uf.blob_path, '') AS unsorted_blob_path,
                            COALESCE(s.slug, p.source_slug, '') AS source_slug,
                            COALESCE(s.name, '') AS source_name,
                            p.source_id,
                            '[]'::text AS current_tags_json,
                            '[]'::text AS proposed_tags_json,
                            :push_type AS source_proposal_type
                        FROM app.unsorted_file_push_proposals p
                        LEFT JOIN app.unsorted_files uf
                            ON uf.id = p.unsorted_file_id
                        LEFT JOIN app.sources_cards s
                            ON s.id = p.source_id
                        LEFT JOIN app."user" proposer_user
                            ON proposer_user.id = p.proposer_user_id
                        WHERE p.id = :proposal_id
                        """
                    ),
                    {
                        "proposal_id": int(proposal_id),
                        "push_type": SOURCE_PROPOSAL_TYPE_PUSH,
                    },
                ).mappings().one_or_none()

            if row is None and normalized_requested_type in {"", SOURCE_PROPOSAL_TYPE_TAGS} and has_tag_proposals:
                proposed_tags_select = (
                    """
                            COALESCE(
                                (
                                    SELECT json_agg(utpt.tag_label ORDER BY lower(utpt.tag_label), utpt.tag_label)
                                    FROM app.unsorted_file_tag_proposal_tags utpt
                                    WHERE utpt.proposal_id = p.id
                                ),
                                '[]'::json
                            )::text AS proposed_tags_json,
                    """
                    if has_tag_rows
                    else "COALESCE(p.tags_json, '[]') AS proposed_tags_json,"
                )
                row = session.execute(
                    text(
                        """
                        SELECT
                            p.id,
                            ('unsorted-file-' || p.unsorted_file_id::text) AS person_slug,
                            p.proposer_user_id,
                            COALESCE(NULLIF(proposer_user.name, ''), proposer_user.email, '') AS proposer_name,
                            COALESCE(proposer_user.email, '') AS proposer_email,
                            'source' AS proposal_scope,
                            COALESCE(p.note, '') AS note,
                            COALESCE(p.status, '') AS status,
                            p.created_at,
                            p.reviewed_at,
                            COALESCE(p.reviewer_user_id, 0)::bigint AS reviewer_user_id,
                            COALESCE(NULLIF(reviewer_user.name, ''), reviewer_user.email, '') AS reviewer_name,
                            COALESCE(reviewer_user.email, '') AS reviewer_email,
                            COALESCE(p.review_note, '') AS review_note,
                            0::integer AS report_triggered,
                            p.unsorted_file_id,
                            COALESCE(uf.file_name, '') AS unsorted_file_name,
                            COALESCE(uf.origin_text, '') AS unsorted_origin_text,
                            COALESCE(uf.mime_type, '') AS unsorted_mime_type,
                            COALESCE(uf.size_bytes, 0)::bigint AS unsorted_size_bytes,
                            COALESCE(uf.bucket, '') AS unsorted_bucket,
                            COALESCE(uf.blob_path, '') AS unsorted_blob_path,
                            ('unsorted-file-' || p.unsorted_file_id::text) AS source_slug,
                            ''::text AS source_name,
                            0::bigint AS source_id,
                            COALESCE(
                                (
                                    SELECT COALESCE(prev.tags_json, '[]')
                                    FROM app.unsorted_file_tag_proposals prev
                                    WHERE prev.unsorted_file_id = p.unsorted_file_id
                                      AND COALESCE(lower(prev.status), '') = 'accepted'
                                    ORDER BY prev.reviewed_at DESC NULLS LAST, prev.created_at DESC, prev.id DESC
                                    LIMIT 1
                                ),
                                '[]'
                            ) AS current_tags_json,
                            """
                        + proposed_tags_select
                        + """
                            :tags_type AS source_proposal_type
                        FROM app.unsorted_file_tag_proposals p
                        LEFT JOIN app.unsorted_files uf
                            ON uf.id = p.unsorted_file_id
                        LEFT JOIN app."user" proposer_user
                            ON proposer_user.id = p.proposer_user_id
                        LEFT JOIN app."user" reviewer_user
                            ON reviewer_user.id = p.reviewer_user_id
                        WHERE p.id = :proposal_id
                        """
                    ),
                    {
                        "proposal_id": int(proposal_id),
                        "tags_type": SOURCE_PROPOSAL_TYPE_TAGS,
                    },
                ).mappings().one_or_none()
        except SQLAlchemyDatabaseError as exc:
            if _is_missing_relation_error(exc):
                _warn_missing_review_tables(
                    "fetch_source_proposal_by_id",
                    (
                        "app.unsorted_file_push_proposals",
                        "app.unsorted_file_tag_proposals",
                    ),
                )
                return None
            raise

    if row is None:
        return None

    proposal = dict(row)
    source_proposal_type_value = _normalize_source_proposal_type(proposal.get("source_proposal_type"))
    file_id = int(proposal.get("unsorted_file_id") or 0)
    file_name = str(proposal.get("unsorted_file_name") or "").strip() or "file"
    origin_text = str(proposal.get("unsorted_origin_text") or "").strip()
    mime_type = str(proposal.get("unsorted_mime_type") or "").strip()
    size_bytes = int(proposal.get("unsorted_size_bytes") or 0)
    note_value = str(proposal.get("note") or "").strip()
    blob_path = str(proposal.get("unsorted_blob_path") or "").strip()
    media_url = media_path(blob_path) if blob_path else ""
    preview_image_url = media_url if mime_type.lower().startswith("image/") else ""
    if source_proposal_type_value == SOURCE_PROPOSAL_TYPE_TAGS:
        source_slug = (
            str(proposal.get("source_slug") or proposal.get("person_slug") or "").strip().lower()
            or f"unsorted-file-{file_id}"
        )
        source_name = "Unsorted file tags"
        current_tags = _decode_tags(proposal.get("current_tags_json"))
        proposed_tags = _decode_tags(proposal.get("proposed_tags_json"))
        base_markdown, proposed_markdown = _build_source_tags_review_markdown(
            unsorted_file_id=file_id,
            file_name=file_name,
            origin_text=origin_text,
            mime_type=mime_type,
            size_bytes=size_bytes,
            current_tags=current_tags,
            proposed_tags=proposed_tags,
            note=note_value,
            media_url=media_url,
        )
    else:
        source_slug = str(proposal.get("source_slug") or proposal.get("person_slug") or "").strip().lower()
        source_name = str(proposal.get("source_name") or "").strip() or source_slug
        base_markdown, proposed_markdown = _build_source_push_review_markdown(
            unsorted_file_id=file_id,
            file_name=file_name,
            source_slug=source_slug,
            source_name=source_name,
            origin_text=origin_text,
            mime_type=mime_type,
            size_bytes=size_bytes,
            note=note_value,
            media_url=media_url,
        )
    proposal["person_slug"] = source_slug
    proposal["base_payload"] = base_markdown
    proposal["proposed_payload"] = proposed_markdown
    proposal["base_markdown"] = base_markdown
    proposal["proposed_markdown"] = proposed_markdown
    proposal["base_image_url"] = ""
    proposal["proposed_image_url"] = preview_image_url
    proposal["person_id"] = 0
    proposal["current_person_slug"] = ""
    proposal["current_person_id"] = 0
    proposal["current_name"] = source_name
    proposal["current_title"] = "Fuente"
    proposal["current_bucket"] = ""
    proposal["current_image_url"] = preview_image_url
    proposal["current_markdown"] = base_markdown
    proposal["current_tags_json"] = "[]"
    proposal["events_json"] = "[]"
    proposal["source_proposal_type"] = source_proposal_type_value
    proposal["proposal_source"] = PROPOSAL_SOURCE_SOURCE
    return proposal


def _accept_source_push_proposal(
    session,
    *,
    proposal: Dict[str, object],
    reviewer_user_id: int,
) -> str:
    proposal_id = int(proposal.get("id") or 0)
    unsorted_file_id = int(proposal.get("unsorted_file_id") or 0)
    source_id = int(proposal.get("source_id") or 0)
    source_slug = str(proposal.get("source_slug") or proposal.get("person_slug") or "").strip().lower()
    if proposal_id <= 0:
        raise ValueError("Proposal id is missing.")
    if unsorted_file_id <= 0:
        raise ValueError("Falta el ID del archivo sin clasificar en la propuesta.")
    if source_id <= 0 and not source_slug:
        raise ValueError("Target source is missing from proposal.")

    source_row = None
    if source_id > 0:
        source_row = session.execute(
            text(
                """
                SELECT
                    id,
                    slug,
                    bucket,
                    folder_prefix,
                    max_bytes
                FROM app.sources_cards
                WHERE id = :source_id
                LIMIT 1
                """
            ),
            {"source_id": source_id},
        ).mappings().one_or_none()
    if source_row is None and source_slug:
        source_row = session.execute(
            text(
                """
                SELECT
                    id,
                    slug,
                    bucket,
                    folder_prefix,
                    max_bytes
                FROM app.sources_cards
                WHERE lower(slug) = :slug
                LIMIT 1
                """
            ),
            {"slug": source_slug},
        ).mappings().one_or_none()
    if source_row is None:
        raise ValueError("Target source was not found.")

    resolved_source_id = int(source_row.get("id") or 0)
    resolved_source_slug = str(source_row.get("slug") or source_slug).strip().lower()
    source_bucket = str(source_row.get("bucket") or "").strip()
    source_folder_prefix = str(source_row.get("folder_prefix") or "").strip().strip("/")
    source_max_bytes = max(1, int(source_row.get("max_bytes") or 0))
    if resolved_source_id <= 0 or not resolved_source_slug:
        raise ValueError("Target source metadata is invalid.")
    if not source_bucket:
        raise ValueError("Target source bucket is missing.")

    unsorted_row = session.execute(
        text(
            """
            SELECT
                id,
                bucket,
                blob_path,
                file_name,
                mime_type,
                size_bytes,
                COALESCE(origin_text, '') AS origin_text
            FROM app.unsorted_files
            WHERE id = :unsorted_file_id
            LIMIT 1
            """
        ),
        {"unsorted_file_id": unsorted_file_id},
    ).mappings().one_or_none()
    if unsorted_row is None:
        raise ValueError("Selected unsorted file was not found.")
    if not str(unsorted_row.get("blob_path") or "").strip():
        raise ValueError("Selected unsorted file is missing its storage path.")

    origin_value = (
        str(unsorted_row.get("origin_text") or "").strip()
        or str(proposal.get("unsorted_origin_text") or "").strip()
        or f"unsorted-file://{unsorted_file_id}"
    )

    uploaded_blob_refs: List[Tuple[str, str]] = []
    try:
        _store_unsorted_files_for_source(
            session,
            source_id=resolved_source_id,
            source_slug=resolved_source_slug,
            bucket_value=source_bucket,
            folder_prefix=source_folder_prefix,
            max_bytes=source_max_bytes,
            unsorted_files=[dict(unsorted_row)],
            origin_urls=[origin_value],
            actor_user_id=int(reviewer_user_id),
            uploaded_blob_refs=uploaded_blob_refs,
        )
        session.execute(
            text(
                """
                UPDATE app.unsorted_file_push_proposals
                SET status = 'accepted',
                    reviewed_at = CURRENT_TIMESTAMP
                WHERE id = :proposal_id
                """
            ),
            {"proposal_id": proposal_id},
        )
    except Exception:
        for bucket_name, blob_name in uploaded_blob_refs:
            try:
                get_bucket(bucket_name).blob(blob_name).delete()
            except Exception:
                continue
        raise

    return resolved_source_slug


def _accept_source_tags_proposal(
    session,
    *,
    proposal: Dict[str, object],
    reviewer_user_id: int,
) -> str:
    proposal_id = int(proposal.get("id") or 0)
    file_id = int(proposal.get("unsorted_file_id") or 0)
    slug_hint = str(proposal.get("person_slug") or proposal.get("source_slug") or "").strip().lower()
    if proposal_id <= 0:
        raise ValueError("Proposal id is missing.")
    if file_id <= 0:
        raise ValueError("Falta el ID del archivo sin clasificar en la propuesta.")

    session.execute(
        text(
            """
            UPDATE app.unsorted_file_tag_proposals
            SET status = 'accepted',
                reviewed_at = CURRENT_TIMESTAMP,
                reviewer_user_id = :reviewer_user_id,
                review_note = :review_note
            WHERE id = :proposal_id
            """
        ),
        {
            "proposal_id": proposal_id,
            "reviewer_user_id": int(reviewer_user_id),
            "review_note": "Aceptado por revisor desde el panel de revisión",
        },
    )
    return slug_hint or f"unsorted-file-{file_id}"


def _fetch_proposal_by_id(
    proposal_id: int,
    proposal_source: str = PROPOSAL_SOURCE_PEOPLE,
    *,
    source_proposal_type: str = "",
) -> Dict[str, object] | None:
    source = _normalize_proposal_source(proposal_source)
    if source == PROPOSAL_SOURCE_SOURCE:
        return _fetch_source_proposal_by_id(
            proposal_id,
            source_proposal_type=source_proposal_type,
        )
    if source == PROPOSAL_SOURCE_THEORY:
        return _fetch_theory_proposal_by_id(proposal_id)
    return _fetch_people_proposal_by_id(proposal_id)


def _fetch_proposal_events(
    proposal_id: int,
    limit: int = 30,
    *,
    proposal_source: str = PROPOSAL_SOURCE_PEOPLE,
) -> List[Dict[str, object]]:
    _ensure_local_db()
    source = _normalize_proposal_source(proposal_source)
    if source == PROPOSAL_SOURCE_SOURCE:
        return []
    events_table = "app.theory_change_events" if source == PROPOSAL_SOURCE_THEORY else "app.people_change_events"
    with readonly_session_scope() as session:
        try:
            rows = session.execute(
                text(
                    f"""
                    SELECT
                        e.id,
                        e.proposal_id,
                        e.event_type,
                        e.actor_user_id,
                        COALESCE(NULLIF(actor_user.name, ''), actor_user.email, '') AS actor_name,
                        COALESCE(actor_user.email, '') AS actor_email,
                        e.notes,
                        e.payload_json,
                        e.created_at
                    FROM {events_table} e
                    LEFT JOIN app."user" actor_user
                        ON actor_user.id = e.actor_user_id
                    WHERE e.proposal_id = :proposal_id
                    ORDER BY e.created_at DESC, e.id DESC
                    LIMIT :limit
                    """
                ),
                {"proposal_id": int(proposal_id), "limit": max(1, int(limit))},
            ).mappings().all()
        except SQLAlchemyDatabaseError as exc:
            if _is_missing_relation_error(exc):
                _warn_missing_review_tables("fetch_proposal_events", (events_table,))
                return []
            raise
    return [dict(row) for row in rows]


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
        raise ValueError("No se pudo leer la imagen subida.")

    extension = source.suffix.lower()
    if extension not in ALLOWED_IMAGE_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_IMAGE_EXTENSIONS))
        raise ValueError(f"Formato de imagen no compatible. Permitidos: {allowed}")
    image_bytes = source.read_bytes()
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise ValueError(f"La imagen supera el límite de {MAX_IMAGE_BYTES // (1024 * 1024)} MB.")
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


def _role_flags_from_request(
    request: gr.Request | None,
    *,
    refresh_if_not_admin: bool = False,
) -> tuple[Dict[str, object], bool, bool]:
    # Review callbacks are high-frequency; prefer cached privileges but hydrate if missing.
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
    has_reviewer_access = _is_truthy(privileges.get("reviewer"))
    if refresh_if_not_admin and user and not has_reviewer_access:
        refreshed_user = get_user(
            request,
            refresh_privileges=True,
            force_privileges_refresh=True,
        ) or {}
        if refreshed_user:
            user = refreshed_user
            refreshed_privileges = refreshed_user.get("privileges")
            privileges = refreshed_privileges if isinstance(refreshed_privileges, dict) else {}
            has_reviewer_access = _is_truthy(privileges.get("reviewer"))
    can_submit = _is_truthy(privileges.get("base_user"))
    return user, has_reviewer_access, can_submit


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
            user["user_id"] = resolved_id
            user["employee_id"] = resolved_id
            return resolved_id
    return 0


def _resolve_user_email_by_id(user_id: int) -> str:
    if int(user_id or 0) <= 0:
        return ""
    with readonly_session_scope() as session:
        row = session.execute(
            text(
                """
                SELECT email
                FROM app."user"
                WHERE id = :user_id
                """
            ),
            {"user_id": int(user_id)},
        ).mappings().first()
    if not row:
        return ""
    return str(row.get("email") or "").strip().lower()


def _render_tag_chips(tags: Sequence[str]) -> str:
    if not tags:
        return '<span class="person-tag person-tag--muted">sin-etiquetas</span>'
    parts = []
    for tag in tags:
        safe_tag = html.escape(tag)
        parts.append(f'<span class="person-tag">{safe_tag}</span>')
    return "".join(parts)


def _render_cards(people: Sequence[Dict[str, object]]) -> str:
    if not people:
        return '<div class="people-empty">No hay perfiles disponibles.</div>'

    cards: List[str] = []
    for row in people:
        name = html.escape(str(row.get("name") or "Desconocido"))
        slug = str(row.get("slug") or "")
        title = html.escape(str(row.get("title") or row.get("bucket") or "Sin asignar"))
        image_url = html.escape(str(row.get("image_url") or "/images/Logo.png"), quote=True)
        href = f"/receta/?slug={quote(slug, safe='-')}"
        tag_values = [_normalize_tag(str(tag)) for tag in row.get("tags", []) if str(tag).strip()]
        tags_markup = _render_tag_chips(row.get("tags", []))
        tags_json_attr = html.escape(json.dumps(tag_values, ensure_ascii=True), quote=True)
        cards.append(
            f"""
            <a class="person-card" href="{href}" data-tags-json="{tags_json_attr}">
              <div class="person-card__image-wrap">
                <img class="person-card__image" src="{image_url}" alt="{name}" loading="lazy" />
              </div>
              <div class="person-card__content">
                <h3 class="person-card__title">{name}</h3>
                <div class="person-card__bucket">{title}</div>
                <div class="person-card__tags">{tags_markup}</div>
              </div>
            </a>
            """.strip()
        )

    return f'<div class="people-grid">{"".join(cards)}</div>'


def _render_person_hero(person: Dict[str, object], *, include_back_link: bool = True) -> str:
    name = html.escape(str(person.get("name") or "Desconocido"))
    title = html.escape(str(person.get("title") or person.get("bucket") or "Sin asignar"))
    image_url = html.escape(str(person.get("image_url") or "/images/Logo.png"), quote=True)
    tags_markup = _render_tag_chips(person.get("tags", []))
    back_link_markup = (
        '<div class="person-detail-card__top-row">'
        '<a class="person-detail-card__back-link" href="/recetas/">Volver a todas las tarjetas</a>'
        "</div>"
        if include_back_link
        else ""
    )
    return f"""
    <section class="person-detail-card" id="person-detail-card">
      <div class="person-detail-card__media" id="person-detail-card-media">
        <img src="{image_url}" alt="{name}" loading="lazy" />
      </div>
      <div class="person-detail-card__body">
        {back_link_markup}
        <div class="person-detail-card__title-row">
          <h2 class="person-detail-card__title" id="person-detail-card-title">{name}</h2>
          <div class="person-detail-card__title-actions-slot" id="person-detail-card-title-actions-slot"></div>
        </div>
        <p class="person-detail-card__bucket" id="person-detail-card-bucket">{title}</p>
        <div class="person-detail-card__tags" id="person-detail-card-tags">{tags_markup}</div>
        <div class="person-detail-card__inline-actions-slot" id="person-detail-card-inline-actions-slot"></div>
      </div>
    </section>
    """


def _render_card_article_preview(title: str, card_snapshot: Dict[str, object], article_markdown: str) -> str:
    """Render card+article with the same structure users see on the published people page."""
    tags_source = card_snapshot.get("tags", [])
    if not isinstance(tags_source, (list, tuple, set)):
        tags_source = []
    tags = [str(tag).strip() for tag in tags_source if str(tag).strip()]
    hero_html = _render_person_hero(
        {
            "name": str(card_snapshot.get("name") or "").strip() or "Desconocido",
            "title": str(card_snapshot.get("title") or card_snapshot.get("bucket") or "").strip() or "Sin asignar",
            "bucket": str(card_snapshot.get("title") or card_snapshot.get("bucket") or "").strip() or "Sin asignar",
            "image_url": str(card_snapshot.get("image_url") or "").strip() or "/images/Logo.png",
            "tags": tags,
        },
        include_back_link=False,
    )
    hero_html = "\n".join(
        line.strip()
        for line in textwrap.dedent(hero_html).splitlines()
        if line.strip()
    )
    article_section = _render_citation_compiled_markdown(str(article_markdown or ""))
    if not str(article_section or "").strip():
        article_section = "_Sin contenido de artículo._"

    # Keep article markdown outside HTML containers so Gradio compiles it normally.
    return (
        f"### {html.escape(title)}\n\n"
        f"{hero_html}\n\n"
        f"{article_section}"
    )


def _sanitize_markdown_href(raw_value: object) -> str:
    value = str(raw_value or "").strip()
    if not value:
        return ""
    lowered = value.lower()
    if lowered.startswith(("http://", "https://", "/", "./", "../", "#", "mailto:")):
        return html.escape(value, quote=True)
    return ""


def _sanitize_markdown_image_src(raw_value: object) -> str:
    value = str(raw_value or "").strip()
    if not value:
        return ""
    lowered = value.lower()
    if lowered.startswith(("http://", "https://", "/", "./", "../", "data:image/")):
        return html.escape(value, quote=True)
    return ""


def _render_inline_markdown_html(raw_text: str) -> str:
    text_value = str(raw_text or "")
    token_map: Dict[str, str] = {}

    def _reserve(fragment: str) -> str:
        token = f"@@MDTOKEN{len(token_map)}@@"
        token_map[token] = fragment
        return token

    def _replace_code(match: re.Match[str]) -> str:
        return _reserve(f"<code>{html.escape(match.group(1) or '', quote=False)}</code>")

    def _replace_link(match: re.Match[str]) -> str:
        label = html.escape(str(match.group(1) or "").strip() or str(match.group(2) or "").strip(), quote=False)
        href = _sanitize_markdown_href(match.group(2))
        if not href:
            return _reserve(label)
        return _reserve(f"<a href='{href}' target='_blank' rel='noopener noreferrer'>{label}</a>")

    with_tokens = _INLINE_CODE_RE.sub(_replace_code, text_value)
    with_tokens = _INLINE_LINK_RE.sub(_replace_link, with_tokens)
    escaped = html.escape(with_tokens, quote=False)
    escaped = _INLINE_BOLD_RE.sub(lambda m: f"<strong>{m.group(1)}</strong>", escaped)
    escaped = _INLINE_ITALIC_RE.sub(lambda m: f"<em>{m.group(1)}</em>", escaped)

    for token, fragment in token_map.items():
        escaped = escaped.replace(token, fragment)

    return escaped


def _render_article_markdown_html(markdown_text: str) -> str:
    normalized_lines = str(markdown_text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if not any(str(line or "").strip() for line in normalized_lines):
        return "<p class='proposal-card-article__empty'><em>Sin contenido de artículo.</em></p>"

    rendered: List[str] = []
    paragraph_lines: List[str] = []
    list_type = ""
    in_code_block = False
    code_lines: List[str] = []

    def _flush_paragraph() -> None:
        if not paragraph_lines:
            return
        paragraph_text = " ".join(line.strip() for line in paragraph_lines if line.strip())
        paragraph_lines.clear()
        if paragraph_text:
            rendered.append(f"<p>{_render_inline_markdown_html(paragraph_text)}</p>")

    def _flush_list() -> None:
        nonlocal list_type
        if not list_type:
            return
        rendered.append(f"</{list_type}>")
        list_type = ""

    def _flush_code_block() -> None:
        nonlocal in_code_block
        if not in_code_block:
            return
        rendered.append(f"<pre><code>{html.escape(chr(10).join(code_lines), quote=False)}</code></pre>")
        code_lines.clear()
        in_code_block = False

    for raw_line in normalized_lines:
        line = str(raw_line or "").rstrip()
        stripped = line.strip()

        if in_code_block:
            if stripped.startswith("```"):
                _flush_code_block()
                continue
            code_lines.append(str(raw_line or ""))
            continue

        if stripped.startswith("```"):
            _flush_paragraph()
            _flush_list()
            in_code_block = True
            code_lines.clear()
            continue

        if not stripped:
            _flush_paragraph()
            _flush_list()
            continue

        heading_match = re.match(r"^\s{0,3}(#{1,6})\s+(.*)$", line)
        if heading_match:
            _flush_paragraph()
            _flush_list()
            level = len(heading_match.group(1))
            heading_text = _render_inline_markdown_html(heading_match.group(2).strip())
            rendered.append(f"<h{level}>{heading_text}</h{level}>")
            continue

        if re.match(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$", line):
            _flush_paragraph()
            _flush_list()
            rendered.append("<hr />")
            continue

        image_match = re.match(r"^\s*!\[([^\]]*)\]\(([^)]+)\)\s*$", line)
        if image_match:
            _flush_paragraph()
            _flush_list()
            alt = html.escape(str(image_match.group(1) or "").strip() or "Imagen del artículo", quote=True)
            src = _sanitize_markdown_image_src(image_match.group(2))
            if src:
                rendered.append(f"<p><img src='{src}' alt='{alt}' loading='lazy' /></p>")
            continue

        unordered_match = re.match(r"^\s{0,3}[-*+]\s+(.*)$", line)
        if unordered_match:
            _flush_paragraph()
            if list_type != "ul":
                _flush_list()
                rendered.append("<ul>")
                list_type = "ul"
            rendered.append(f"<li>{_render_inline_markdown_html(unordered_match.group(1).strip())}</li>")
            continue

        ordered_match = re.match(r"^\s{0,3}\d+\.\s+(.*)$", line)
        if ordered_match:
            _flush_paragraph()
            if list_type != "ol":
                _flush_list()
                rendered.append("<ol>")
                list_type = "ol"
            rendered.append(f"<li>{_render_inline_markdown_html(ordered_match.group(1).strip())}</li>")
            continue

        quote_match = re.match(r"^\s{0,3}>\s?(.*)$", line)
        if quote_match:
            _flush_paragraph()
            _flush_list()
            quote_body = _render_inline_markdown_html(quote_match.group(1).strip())
            rendered.append(f"<blockquote><p>{quote_body}</p></blockquote>")
            continue

        paragraph_lines.append(line)

    _flush_paragraph()
    _flush_list()
    _flush_code_block()

    if not rendered:
        return "<p class='proposal-card-article__empty'><em>Sin contenido de artículo.</em></p>"
    return "\n".join(rendered)


def _render_card_article_snapshot_html(title: str, card_snapshot: Dict[str, object], article_markdown: str) -> str:
    tags_source = card_snapshot.get("tags", [])
    if not isinstance(tags_source, (list, tuple, set)):
        tags_source = []
    tags = [str(tag).strip() for tag in tags_source if str(tag).strip()]
    hero_html = _render_person_hero(
        {
            "name": str(card_snapshot.get("name") or "").strip() or "Desconocido",
            "title": str(card_snapshot.get("title") or card_snapshot.get("bucket") or "").strip() or "Sin asignar",
            "bucket": str(card_snapshot.get("title") or card_snapshot.get("bucket") or "").strip() or "Sin asignar",
            "image_url": str(card_snapshot.get("image_url") or "").strip() or "/images/Logo.png",
            "tags": tags,
        },
        include_back_link=False,
    )
    compact_hero_html = "".join(
        line.strip()
        for line in textwrap.dedent(hero_html).splitlines()
        if line.strip()
    )
    article_html = _render_article_markdown_html(article_markdown)
    return (
        "<div class='proposal-diff proposal-diff--card-article-snapshot'>"
        f"<h4 class='proposal-diff-title'>{html.escape(title)}</h4>"
        f"{compact_hero_html}"
        "<section class='proposal-card-article-article'>"
        f"{article_html}"
        "</section>"
        "</div>"
    )


def _render_missing_person(slug: str) -> str:
    safe_slug = html.escape(slug or "desconocido")
    return (
        "<section class='person-detail-card person-detail-card--missing'>"
        "<div class='person-detail-card__body'>"
        "<a class='person-detail-card__back-link' href='/recetas/'>Volver a todas las tarjetas</a>"
        "<h2>Perfil no encontrado</h2>"
        f"<p>No se encontró ningún perfil con slug <code>{safe_slug}</code>.</p>"
        "</div></section>"
    )


def _render_empty_diff(message: str) -> str:
    return f"<div class='proposal-diff proposal-diff--empty'>{html.escape(message)}</div>"


def _scope_dataset_title(scope: str) -> str:
    normalized_scope = _normalize_proposal_scope(scope)
    if normalized_scope == PROPOSAL_SCOPE_SOURCE:
        return "Fuente"
    if normalized_scope == PROPOSAL_SCOPE_CARD:
        return "Tarjeta"
    if normalized_scope == PROPOSAL_SCOPE_CARD_ARTICLE:
        return "Tarjeta + Artículo"
    return "Artículo"


def _scope_dataset_prefix(scope: str) -> str:
    normalized_scope = _normalize_proposal_scope(scope)
    if normalized_scope == PROPOSAL_SCOPE_SOURCE:
        return "S"
    if normalized_scope == PROPOSAL_SCOPE_CARD:
        return "C"
    if normalized_scope == PROPOSAL_SCOPE_CARD_ARTICLE:
        return "CA"
    return "A"


def _format_scope_dataset_entry(scope: str, index: int) -> str:
    safe_index = max(1, int(index))
    return f"{_scope_dataset_prefix(scope)}-{safe_index:03d}"


def _build_scope_dataset_entries(proposals: Sequence[Dict[str, object]]) -> Dict[str, str]:
    proposal_ids = {str(proposal.get("id") or "").strip() for proposal in proposals}
    proposal_ids.discard("")
    if not proposal_ids:
        return {}

    _ensure_local_db()
    with readonly_session_scope() as session:
        missing_tables = _missing_tables_in_search_path(session, ("people_change_proposals",))
        if missing_tables:
            _warn_missing_review_tables("build_scope_dataset_entries", missing_tables)
            return {}

        rows = session.execute(
            text(
                """
                SELECT id, proposal_scope
                FROM app.people_change_proposals
                ORDER BY id ASC
                """
            )
        ).mappings().all()

    counters = {
        PROPOSAL_SCOPE_ARTICLE: 0,
        PROPOSAL_SCOPE_CARD: 0,
        PROPOSAL_SCOPE_CARD_ARTICLE: 0,
    }
    all_entries: Dict[str, str] = {}
    for row in rows:
        proposal_id = str(row["id"])
        scope = _normalize_proposal_scope(row["proposal_scope"])
        counters[scope] = int(counters.get(scope, 0)) + 1
        all_entries[proposal_id] = _format_scope_dataset_entry(scope, counters[scope])

    return {proposal_id: all_entries[proposal_id] for proposal_id in proposal_ids if proposal_id in all_entries}


def _pluralize_unit(value: int, unit: str) -> str:
    return f"{value} {unit}" + ("" if value == 1 else "s")


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        next_month = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        next_month = datetime(year, month + 1, 1, tzinfo=timezone.utc)
    current_month = datetime(year, month, 1, tzinfo=timezone.utc)
    return (next_month - current_month).days


def _add_months(value: datetime, months: int) -> datetime:
    month_offset = value.month - 1 + months
    target_year = value.year + month_offset // 12
    target_month = month_offset % 12 + 1
    target_day = min(value.day, _days_in_month(target_year, target_month))
    return value.replace(year=target_year, month=target_month, day=target_day)


def _parse_timestamp(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        raw_value = str(value or "").strip()
        if not raw_value:
            return None
        normalized_value = raw_value.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(normalized_value)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _format_elapsed_ago(value: object) -> str:
    parsed_time = _parse_timestamp(value)
    if parsed_time is None:
        return ""

    now_utc = datetime.now(timezone.utc)
    if parsed_time > now_utc:
        return "justo ahora"

    elapsed_seconds = int((now_utc - parsed_time).total_seconds())
    if elapsed_seconds < 60:
        return "justo ahora"
    if elapsed_seconds < 3600:
        minutes = max(1, elapsed_seconds // 60)
        return f"hace {_pluralize_unit(minutes, 'minuto')}"
    if elapsed_seconds < 86400:
        hours = max(1, elapsed_seconds // 3600)
        return f"hace {_pluralize_unit(hours, 'hora')}"

    total_months = max(0, (now_utc.year - parsed_time.year) * 12 + (now_utc.month - parsed_time.month))
    shifted_time = _add_months(parsed_time, total_months)
    if shifted_time > now_utc and total_months > 0:
        total_months -= 1
        shifted_time = _add_months(parsed_time, total_months)

    remaining_days = max(0, (now_utc - shifted_time).days)
    years, months = divmod(total_months, 12)
    parts: List[str] = []
    if years > 0:
        parts.append(_pluralize_unit(years, "año"))
    if months > 0:
        parts.append(_pluralize_unit(months, "mes"))
    if remaining_days > 0 and len(parts) < 2:
        parts.append(_pluralize_unit(remaining_days, "día"))
    if not parts:
        day_count = max(1, elapsed_seconds // 86400)
        parts.append(_pluralize_unit(day_count, "día"))
    if len(parts) == 1:
        return f"hace {parts[0]}"
    return f"hace {parts[0]} y {parts[1]}"


def _format_username_with_email(name_value: object, email_value: object, user_id: int) -> str:
    email = str(email_value or "").strip()
    name = str(name_value or "").strip()
    if email and "@" in email:
        username = email.split("@", 1)[0].strip()
    elif name and "@" in name:
        username = name.split("@", 1)[0].strip()
    elif name:
        username = name
    elif user_id > 0:
        username = f"usuario#{user_id}"
    else:
        username = "desconocido"
    if email:
        return f"{username} ({email})"
    return username


def _render_proposal_meta(
    proposal: Dict[str, object],
    dataset_entry: str = "",
) -> str:
    _ = dataset_entry
    scope = _normalize_proposal_scope(proposal.get("proposal_scope"))
    scope_title = _scope_dataset_title(scope)
    proposal_source = _normalize_proposal_source(proposal.get("proposal_source"))
    source_proposal_type = (
        _normalize_source_proposal_type(proposal.get("source_proposal_type"))
        if proposal_source == PROPOSAL_SOURCE_SOURCE
        else ""
    )
    source_label = _proposal_source_label(str(proposal.get("proposal_source") or PROPOSAL_SOURCE_PEOPLE))
    proposer_user_id = int(proposal.get("proposer_user_id") or 0)
    proposer_identity = _format_username_with_email(
        proposal.get("proposer_name"),
        proposal.get("proposer_email"),
        proposer_user_id,
    )
    heading = f"### Propuesta de {scope_title}"
    if proposal_source == PROPOSAL_SOURCE_SOURCE:
        if source_proposal_type == SOURCE_PROPOSAL_TYPE_TAGS:
            heading = "### Propuesta de etiquetas de fuente"
        else:
            heading = "### Propuesta de envío a fuente"
    created_raw = str(proposal.get("created_at") or "").strip() or "n/d"
    created_ago = _format_elapsed_ago(proposal.get("created_at"))
    created_line = f"`{created_raw}`" if created_raw else "`n/d`"
    if created_ago:
        created_line = f"{created_line} ({created_ago})"
    slug_value = str(proposal.get("person_slug") or "").strip()
    slug_label = "Perfil"
    if proposal_source == PROPOSAL_SOURCE_SOURCE:
        slug_label = "Fuente de destino"
    lines = [
        heading,
        f"- **Origen:** `{source_label}`",
        f"- **{slug_label}:** `{slug_value}`",
        f"- **Proponente:** `{proposer_identity}`",
        f"- **Creada:** {created_line}",
    ]
    if proposal_source == PROPOSAL_SOURCE_SOURCE:
        file_id = int(proposal.get("unsorted_file_id") or 0)
        file_name = str(proposal.get("unsorted_file_name") or "").strip() or "archivo"
        source_name = str(proposal.get("source_name") or "").strip()
        if file_id > 0:
            lines.append(f"- **ID de archivo sin clasificar:** `{file_id}`")
        lines.append(f"- **Nombre de archivo sin clasificar:** `{file_name}`")
        if source_proposal_type == SOURCE_PROPOSAL_TYPE_TAGS:
            current_tags = _format_tags_markdown(_decode_tags(proposal.get("current_tags_json")))
            proposed_tags = _format_tags_markdown(_decode_tags(proposal.get("proposed_tags_json")))
            lines.append(f"- **Etiquetas aceptadas actuales:** {current_tags}")
            lines.append(f"- **Etiquetas propuestas:** {proposed_tags}")
        else:
            if source_name:
                lines.append(f"- **Nombre de fuente de destino:** `{source_name}`")
        origin_text = str(proposal.get("unsorted_origin_text") or "").strip()
        if origin_text:
            lines.append(f"- **Origen/descripción:** {origin_text}")

    note = (proposal.get("note") or "").strip()
    if note:
        lines.append(f"- **Nota del usuario:** {note}")

    reviewer_user_id = int(proposal.get("reviewer_user_id") or 0)
    reviewer_email = str(proposal.get("reviewer_email") or "").strip()
    reviewer_name = str(proposal.get("reviewer_name") or "").strip()
    reviewer_identity = reviewer_email or reviewer_name or (f"usuario#{reviewer_user_id}" if reviewer_user_id > 0 else "")
    reviewed_at = str(proposal.get("reviewed_at") or "").strip()
    review_note = str(proposal.get("review_note") or "").strip()
    if reviewer_user_id > 0 or reviewer_identity or reviewed_at or review_note:
        lines.append(f"- **Revisada por ID de usuario:** `{reviewer_user_id if reviewer_user_id > 0 else 'desconocido'}`")
        lines.append(f"- **Revisada por:** `{reviewer_identity or 'desconocido'}`")
        lines.append(f"- **Revisada en:** `{reviewed_at or 'n/d'}`")
        if review_note:
            lines.append(f"- **Nota de revisión:** {review_note}")

    return "\n".join(lines)


def _sanitize_image_url(raw_value: object) -> str:
    value = str(raw_value or "").strip()
    if not value:
        value = "/images/Logo.png"
    return html.escape(value, quote=True)


def _render_proposal_images(base_image_url: str, current_image_url: str, proposed_image_url: str) -> str:
    review_image = _sanitize_image_url(proposed_image_url or current_image_url or base_image_url)
    return (
        "<div class='the-list-review-image-root' "
        "style='width:100%;max-width:210px;margin:0;line-height:0;'>"
        "<div class='the-list-review-image-frame' "
        "style='width:100%;aspect-ratio:4 / 3;border:1px solid #d9e4f4;border-radius:12px;"
        "background:linear-gradient(140deg,#edf3ff 0%,#ecfeff 100%);overflow:hidden;'>"
        f"<img src='{review_image}' alt='Imagen en revisión' loading='lazy' "
        "style='display:block;width:100%;height:100%;margin:0;padding:0;border:0;object-fit:cover;' />"
        "</div>"
        "</div>"
    )


def _render_card_snapshot_markdown(snapshot: Dict[str, object]) -> str:
    name = html.escape(str(snapshot.get("name") or "Desconocido"))
    title = html.escape(str(snapshot.get("title") or snapshot.get("bucket") or "Sin asignar"))
    tags = [str(tag).strip() for tag in snapshot.get("tags", []) if str(tag).strip()]
    tags_md = ", ".join(f"`{html.escape(tag)}`" for tag in tags) if tags else "_Sin etiquetas_"
    return (
        "## Instantánea de la tarjeta\n"
        f"- **Nombre:** {name}\n"
        f"- **Título:** {title}\n"
        f"- **Etiquetas:** {tags_md}\n"
    )


def _render_card_review_state_panel(
    panel_title: str,
    snapshot: Dict[str, object],
    image_url: str,
    *,
    side: str = "base",
    compare_snapshot: Dict[str, object] | None = None,
    compare_image_url: str = "",
    context_snapshot: Dict[str, object] | None = None,
) -> str:
    normalized_side = str(side or "base").strip().lower()

    def _value_line(value: object, highlight_variant: str = "") -> str:
        text = str(value or "").strip()
        if not text:
            rendered = "<span class='card-review-empty'>(vacío)</span>"
        else:
            rendered = html.escape(text)
        if highlight_variant:
            return (
                f"<span class='card-review-highlight card-review-highlight--{html.escape(highlight_variant)}'>"
                f"{rendered}"
                "</span>"
            )
        return rendered

    tags = [
        _normalize_tag(str(tag))
        for tag in snapshot.get("tags", [])
        if _normalize_tag(str(tag))
    ]
    name_value = str(snapshot.get("name") or "").strip()
    title_value = str(snapshot.get("title") or snapshot.get("bucket") or "").strip()
    image_value = str(image_url or "").strip() or "/images/Logo.png"

    compare_name = ""
    compare_title = ""
    compare_tags: List[str] = []
    compare_image = str(compare_image_url or "").strip() or image_value
    if compare_snapshot is not None:
        compare_name = str(compare_snapshot.get("name") or "").strip()
        compare_title = str(compare_snapshot.get("title") or compare_snapshot.get("bucket") or "").strip()
        compare_tags = [
            _normalize_tag(str(tag))
            for tag in compare_snapshot.get("tags", [])
            if _normalize_tag(str(tag))
        ]
        compare_image = str(compare_image_url or "").strip() or image_value

    name_changed = compare_snapshot is not None and name_value != compare_name
    title_changed = compare_snapshot is not None and title_value != compare_title
    image_changed = compare_snapshot is not None and image_value != compare_image
    context_title = ""
    if context_snapshot is not None:
        context_title = str(context_snapshot.get("title") or context_snapshot.get("bucket") or "").strip()
    title_context_changed = context_snapshot is not None and title_value != context_title
    name_variant = normalized_side if name_changed and normalized_side in {"current", "proposed"} else ""
    title_variant = ""
    if title_changed and normalized_side in {"current", "proposed"}:
        title_variant = normalized_side
    elif title_context_changed and normalized_side in {"base", "current"}:
        title_variant = "context"

    tags_lines: List[str] = []
    added_tags: set[str] = set()
    removed_tags: List[str] = []
    if compare_snapshot is not None:
        compare_tag_set = set(compare_tags)
        tag_set = set(tags)
        added_tags = tag_set - compare_tag_set
        removed_tags = [tag for tag in compare_tags if tag not in tag_set]

    for tag in tags:
        tag_html = f"<code class='card-review-tag'>{html.escape(tag)}</code>"
        if tag in added_tags and normalized_side in {"current", "proposed"}:
            tag_html = (
                f"<span class='card-review-highlight card-review-highlight--{normalized_side}'>"
                f"{tag_html}"
                "</span>"
            )
        tags_lines.append(f"- {tag_html}")

    if removed_tags and normalized_side in {"current", "proposed"}:
        for tag in removed_tags:
            tag_html = f"<code class='card-review-tag'>{html.escape(tag)}</code>"
            tags_lines.append(f"- <span class='card-review-removed'>{tag_html}</span>")

    if not tags_lines:
        empty_tags_variant = ""
        if compare_snapshot is not None and normalized_side in {"current", "proposed"} and bool(compare_tags):
            empty_tags_variant = normalized_side
        tags_lines.append(f"- {_value_line('', highlight_variant=empty_tags_variant)}")
    tags_list = "\n".join(tags_lines)

    safe_image_url = _sanitize_image_url(image_value)
    image_wrap_classes = ["card-review-image-wrap"]
    if image_changed and normalized_side in {"current", "proposed"}:
        image_wrap_classes.append(f"card-review-image-wrap--{normalized_side}")

    card_markdown = (
        f"**Nombre:** {_value_line(name_value, highlight_variant=name_variant)}\n\n"
        f"<div class='{' '.join(image_wrap_classes)}'>"
        f"<img class='card-review-image' src='{safe_image_url}' alt='Imagen de la tarjeta' loading='lazy' />"
        "</div>\n\n"
        f"**Título:** {_value_line(title_value, highlight_variant=title_variant)}\n\n"
        "**Etiquetas:**\n"
        f"{tags_list}"
    )
    return _render_review_markdown_panel(panel_title, card_markdown)


def _render_review_markdown_panel(title: str, markdown_text: str) -> str:
    cleaned_markdown = (markdown_text or "").strip()
    if not cleaned_markdown:
        cleaned_markdown = "_Sin contenido markdown._"
    return f"### {title}\n\n{cleaned_markdown}"


def _normalize_review_view_mode(view_mode: str) -> str:
    normalized_mode = str(view_mode or DEFAULT_REVIEW_VIEW).strip().lower()
    if normalized_mode in {REVIEW_VIEW_RAW, MARKDOWN_VIEW_RAW, "markdown en bruto"}:
        return REVIEW_VIEW_RAW
    return REVIEW_VIEW_COMPILED


def _review_raw_panel_title(title: str) -> str:
    return re.sub(r"\(compiled\)", "(markdown en bruto)", str(title or ""), flags=re.IGNORECASE)


def _render_review_article_raw_panel(title: str, markdown_text: str) -> str:
    raw_lines = str(markdown_text or "").splitlines()
    if raw_lines:
        body_lines = [f"<span class='review-raw-line'>{html.escape(line)}</span>" for line in raw_lines]
    else:
        body_lines = ["<span class='review-raw-line review-raw-line--empty'>(vacío)</span>"]
    body_markup = (
        "<div class='review-raw-markdown'>"
        "<pre class='review-raw-markdown__pre'>"
        f"{chr(10).join(body_lines)}"
        "</pre>"
        "</div>"
    )
    return _render_review_markdown_panel(_review_raw_panel_title(title), body_markup)


def _render_review_article_raw_diff_panel(
    title: str,
    base_markdown: str,
    target_markdown: str,
    side: str,
) -> str:
    base_lines = str(base_markdown or "").splitlines()
    target_lines = str(target_markdown or "").splitlines()
    diff_lines: List[str] = []
    matcher = difflib.SequenceMatcher(a=base_lines, b=target_lines, autojunk=False)
    changed_class = (
        "review-raw-line--changed-proposed"
        if str(side or "").strip().lower() == "proposed"
        else "review-raw-line--changed-current"
    )

    for opcode, i1, i2, j1, j2 in matcher.get_opcodes():
        if opcode == "equal":
            diff_lines.extend(
                f"<span class='review-raw-line'>{html.escape(line)}</span>"
                for line in target_lines[j1:j2]
            )
            continue
        if opcode in {"replace", "insert"}:
            diff_lines.extend(
                f"<span class='review-raw-line {changed_class}'>{html.escape(line)}</span>"
                for line in target_lines[j1:j2]
            )
            continue
        # For deletes there is no line in the target view; keep panel text exactly as target.

    if not diff_lines:
        diff_lines.append("<span class='review-raw-line review-raw-line--empty'>(vacío)</span>")

    body_markup = (
        "<div class='review-raw-markdown'>"
        "<pre class='review-raw-markdown__pre'>"
        f"{chr(10).join(diff_lines)}"
        "</pre>"
        "</div>"
    )
    return _render_review_markdown_panel(_review_raw_panel_title(title), body_markup)


def _format_card_article_raw_payload_for_display(raw_payload: str) -> str:
    candidate = str(raw_payload or "").strip()
    if not candidate:
        return ""
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return str(raw_payload or "")
    if not isinstance(parsed, dict):
        return str(raw_payload or "")
    return json.dumps(parsed, ensure_ascii=True, indent=2, sort_keys=True)


def _is_review_citation_reference_line(line: str) -> bool:
    stripped = str(line or "").strip()
    if not stripped:
        return False
    if REFERENCE_HEADING_LINE_RE.match(stripped):
        return True
    if REFERENCE_DEFINITION_LINE_RE.match(stripped):
        return True
    lowered = stripped.lower()
    return "\\bib{" in lowered


def _sanitize_review_markdown_for_citation_compile(markdown_text: str) -> str:
    normalized = str(markdown_text or "").replace("\r\n", "\n")
    if "review-change" not in normalized:
        return normalized

    cleaned_lines: List[str] = []
    for line in normalized.splitlines():
        if "review-change" not in line:
            cleaned_lines.append(line)
            continue
        visible_line = re.sub(r"<[^>]+>", "", line)
        if _is_review_citation_reference_line(visible_line):
            if "review-change--placeholder" in line:
                continue
            cleaned_lines.append(visible_line)
            continue
        cleaned_lines.append(line)
    return "\n".join(cleaned_lines)


def _render_review_article_compiled_panel(title: str, markdown_text: str) -> str:
    compiled_markdown = _render_citation_compiled_markdown(
        _sanitize_review_markdown_for_citation_compile(markdown_text or "")
    )
    return _render_review_markdown_panel(title, compiled_markdown)


def _render_compiled_review_single_panel(scope: str, title: str, raw_markdown: str) -> str:
    normalized_scope = _normalize_proposal_scope(scope)
    if normalized_scope == PROPOSAL_SCOPE_CARD:
        snapshot = _deserialize_card_snapshot(raw_markdown)
        return _render_review_markdown_panel(title, _render_card_snapshot_markdown(snapshot))
    if normalized_scope == PROPOSAL_SCOPE_CARD_ARTICLE:
        combined = _deserialize_card_article_snapshot(raw_markdown)
        card_snapshot = combined.get("card", {})
        article_markdown = str(combined.get("article") or "")
        return _render_card_article_preview(title, card_snapshot, article_markdown)
    return _render_review_article_compiled_panel(title, raw_markdown)


def _merge_three_way_text(base_value: str, current_value: str, proposed_value: str) -> tuple[str, bool]:
    base = str(base_value or "")
    current = str(current_value or "")
    proposed = str(proposed_value or "")
    if current == proposed:
        return current, False
    if current == base:
        return proposed, False
    if proposed == base:
        return current, False
    return proposed, True


def _merge_three_way_scalar(base_value: str, current_value: str, proposed_value: str) -> tuple[str, bool]:
    base = str(base_value or "").strip()
    current = str(current_value or "").strip()
    proposed = str(proposed_value or "").strip()
    if current == proposed:
        return current, False
    if current == base:
        return proposed, False
    if proposed == base:
        return current, False
    return proposed, True


def _merge_three_way_card_snapshots(
    base_snapshot: Dict[str, object],
    current_snapshot: Dict[str, object],
    proposed_snapshot: Dict[str, object],
) -> tuple[Dict[str, object], List[str]]:
    merged: Dict[str, object] = {}
    conflicts: List[str] = []

    for field in ("name", "title"):
        merged_value, has_conflict = _merge_three_way_scalar(
            str(base_snapshot.get(field) or ""),
            str(current_snapshot.get(field) or ""),
            str(proposed_snapshot.get(field) or ""),
        )
        merged[field] = merged_value
        if has_conflict:
            conflicts.append(field)

    base_tags = [_normalize_tag(str(tag)) for tag in base_snapshot.get("tags", []) if _normalize_tag(str(tag))]
    current_tags = [
        _normalize_tag(str(tag)) for tag in current_snapshot.get("tags", []) if _normalize_tag(str(tag))
    ]
    proposed_tags = [
        _normalize_tag(str(tag)) for tag in proposed_snapshot.get("tags", []) if _normalize_tag(str(tag))
    ]
    if current_tags == proposed_tags:
        merged_tags = current_tags
    elif current_tags == base_tags:
        merged_tags = proposed_tags
    elif proposed_tags == base_tags:
        merged_tags = current_tags
    else:
        merged_tags = proposed_tags
        conflicts.append("tags")
    merged["tags"] = merged_tags
    return merged, conflicts


def _wrap_grouped_change_markup(line_markup: str, group_id: int, side: str) -> str:
    if _table_separator_cells(line_markup) is not None or _is_markdown_table_row(line_markup):
        return line_markup
    if not str(line_markup or "").strip():
        return ""
    prefix, content = _split_markdown_line_prefix(line_markup)
    marker_attrs = f"class='review-change review-change--{side}' data-review-change-id='{int(group_id)}'"
    if prefix and content:
        return f"{prefix}<span {marker_attrs}>{content}</span>"
    if prefix and not content:
        return line_markup
    return f"<span {marker_attrs}>{line_markup}</span>"


def _render_group_placeholder_line(reference_line: str, group_id: int, side: str, kind: str) -> str:
    reference_value = str(reference_line or "")
    visible_reference = _placeholder_visible_text(reference_value)
    class_names = [
        "review-change",
        f"review-change--{side}",
        "review-change--placeholder",
        f"review-change--{kind}",
    ]
    if not visible_reference:
        class_names.append("review-change--blank")
    marker_attrs = f"class='{' '.join(class_names)}' data-review-change-id='{int(group_id)}'"
    # Preserve markdown block boundaries after removed placeholder lines.
    # Using literal newlines here keeps following headings/lists rendering normally.
    removed_line_break = "\n" if kind == "removed" else ""
    if _is_review_citation_reference_line(reference_value):
        return removed_line_break
    if _table_separator_cells(reference_line) is not None:
        return reference_line
    if _is_markdown_table_row(reference_line):
        raw_cells = [cell.strip() for cell in str(reference_line).strip().strip("|").split("|")]
        if not raw_cells:
            return f"<span {marker_attrs}>&nbsp;</span>"
        rendered_cells: List[str] = []
        for cell in raw_cells:
            visible_cell = _placeholder_visible_text(cell)
            rendered_cells.append(f"<span {marker_attrs}>{html.escape(visible_cell) or '&nbsp;'}</span>")
        return f"| {' | '.join(rendered_cells)} |"
    # Keep spacing for removed blank lines, but do not render tiny placeholder bars.
    if not visible_reference:
        return removed_line_break

    prefix, content = _split_markdown_line_prefix(reference_line)
    if prefix and content:
        visible_content = _placeholder_visible_text(content)
        if visible_content:
            content_span = f"<span {marker_attrs}>{html.escape(visible_content)}</span>"
            # Preserve markdown syntax structure (headings/lists/quotes) so removed
            # placeholders consume the same layout space as normal rendered lines.
            return f"{prefix}{content_span}{removed_line_break}"
        return ""
    if prefix:
        return ""
    if visible_reference:
        return f"<span {marker_attrs}>{html.escape(visible_reference)}</span>{removed_line_break}"
    return f"<span {marker_attrs}>&nbsp;</span>"


def _placeholder_visible_text(markdown_text: str) -> str:
    value = str(markdown_text or "")
    if not value:
        return ""
    # Approximate rendered inline markdown text so placeholder wrapping matches compiled output.
    value = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", value)
    value = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", value)
    value = re.sub(r"`([^`]*)`", r"\1", value)
    value = re.sub(r"\*\*([^*]+)\*\*", r"\1", value)
    value = re.sub(r"__([^_]+)__", r"\1", value)
    value = re.sub(r"(?<!\w)\*([^*\n]+)\*(?!\w)", r"\1", value)
    value = re.sub(r"(?<!\w)_([^_\n]+)_(?!\w)", r"\1", value)
    value = re.sub(r"~~([^~]+)~~", r"\1", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def _map_proposed_index_to_base(base_lines: Sequence[str], proposed_lines: Sequence[str], proposed_index: int) -> int:
    matcher = _line_matcher(base_lines, proposed_lines)
    index = max(0, min(int(proposed_index), len(proposed_lines)))
    for opcode, b1, b2, p1, p2 in matcher.get_opcodes():
        if p1 <= index <= p2:
            if opcode == "equal":
                return min(b1 + (index - p1), b2)
            return b1
    return len(base_lines)


def _map_proposed_range_to_base(
    base_lines: Sequence[str],
    proposed_lines: Sequence[str],
    proposed_start: int,
    proposed_end: int,
) -> tuple[int, int]:
    start = max(0, int(proposed_start))
    end = max(start, int(proposed_end))
    if start == end:
        insertion_point = _map_proposed_index_to_base(base_lines, proposed_lines, start)
        return insertion_point, insertion_point

    matcher = _line_matcher(base_lines, proposed_lines)
    mapped_ranges: List[Tuple[int, int]] = []
    for opcode, b1, b2, p1, p2 in matcher.get_opcodes():
        overlap_start = max(start, p1)
        overlap_end = min(end, p2)
        if overlap_start >= overlap_end:
            continue
        if opcode == "equal":
            delta_start = overlap_start - p1
            mapped_ranges.append((b1 + delta_start, b1 + delta_start + (overlap_end - overlap_start)))
            continue
        if opcode in {"replace", "delete"}:
            mapped_ranges.append((b1, b2))
            continue
        mapped_ranges.append((b1, b1))

    if not mapped_ranges:
        fallback_start = _map_proposed_index_to_base(base_lines, proposed_lines, start)
        fallback_end = _map_proposed_index_to_base(base_lines, proposed_lines, end)
        return min(fallback_start, fallback_end), max(fallback_start, fallback_end)

    return min(item[0] for item in mapped_ranges), max(item[1] for item in mapped_ranges)


def _map_base_index_to_version(base_lines: Sequence[str], target_lines: Sequence[str], base_index: int) -> int:
    matcher = _line_matcher(base_lines, target_lines)
    index = max(0, min(int(base_index), len(base_lines)))
    for opcode, b1, b2, t1, t2 in matcher.get_opcodes():
        if b1 <= index <= b2:
            if opcode == "equal":
                return min(t1 + (index - b1), t2)
            return t1
    return len(target_lines)


def _map_base_range_to_version(
    base_lines: Sequence[str],
    target_lines: Sequence[str],
    base_start: int,
    base_end: int,
) -> tuple[int, int]:
    start = max(0, int(base_start))
    end = max(start, int(base_end))
    if start == end:
        insertion_point = _map_base_index_to_version(base_lines, target_lines, start)
        return insertion_point, insertion_point

    matcher = _line_matcher(base_lines, target_lines)
    mapped_ranges: List[Tuple[int, int]] = []
    for opcode, b1, b2, t1, t2 in matcher.get_opcodes():
        overlap_start = max(start, b1)
        overlap_end = min(end, b2)
        if overlap_start >= overlap_end:
            continue
        if opcode == "equal":
            delta_start = overlap_start - b1
            mapped_ranges.append((t1 + delta_start, t1 + delta_start + (overlap_end - overlap_start)))
            continue
        if opcode in {"replace", "delete"}:
            mapped_ranges.append((t1, t2))
            continue
        mapped_ranges.append((t1, t1))

    if not mapped_ranges:
        fallback_start = _map_base_index_to_version(base_lines, target_lines, start)
        fallback_end = _map_base_index_to_version(base_lines, target_lines, end)
        return min(fallback_start, fallback_end), max(fallback_start, fallback_end)

    return min(item[0] for item in mapped_ranges), max(item[1] for item in mapped_ranges)


def _render_grouped_current_proposed_panels(
    scope: str,
    base_raw: str,
    current_raw: str,
    proposed_raw: str,
    *,
    base_image_url: str = "",
    current_image_url: str = "",
    proposed_image_url: str = "",
    proposal: Dict[str, object] | None = None,
) -> tuple[str, str, str, List[Dict[str, int]], bool]:
    normalized_scope = _normalize_proposal_scope(scope)
    collapse_base_current = str(base_raw or "") == str(current_raw or "")
    base_title = "Estado base (=estado actual) (compilado)" if collapse_base_current else "Estado base (compilado)"
    if normalized_scope == PROPOSAL_SCOPE_CARD:
        base_snapshot = _deserialize_card_snapshot(base_raw)
        current_snapshot = _deserialize_card_snapshot(current_raw, fallback=base_snapshot)
        proposed_snapshot = _deserialize_card_snapshot(proposed_raw, fallback=current_snapshot)
        base_image = base_image_url or current_image_url or proposed_image_url or "/images/Logo.png"
        current_image = current_image_url or base_image
        proposed_image = proposed_image_url or current_image
        return (
            _render_card_review_state_panel(
                "Tarjeta base",
                base_snapshot,
                base_image,
                side="base",
                context_snapshot=proposed_snapshot,
            ),
            _render_card_review_state_panel(
                "Tarjeta actual",
                current_snapshot,
                current_image,
                side="current",
                compare_snapshot=base_snapshot,
                compare_image_url=base_image,
                context_snapshot=proposed_snapshot,
            ),
            _render_card_review_state_panel(
                "Tarjeta propuesta",
                proposed_snapshot,
                proposed_image,
                side="proposed",
                compare_snapshot=base_snapshot,
                compare_image_url=base_image,
            ),
            [],
            False,
        )

    if normalized_scope == PROPOSAL_SCOPE_CARD_ARTICLE:
        # Render combined card+article panels
        base_combined = _deserialize_card_article_snapshot(base_raw)
        current_combined = _deserialize_card_article_snapshot(current_raw)
        proposed_combined = _deserialize_card_article_snapshot(proposed_raw)
        
        base_card = base_combined.get("card", {})
        current_card = current_combined.get("card", {})
        proposed_card = proposed_combined.get("card", {})
        
        base_article = str(base_combined.get("article") or "")
        current_article = str(current_combined.get("article") or "")
        proposed_article = str(proposed_combined.get("article") or "")
        
        base_image = base_image_url or str(base_card.get("image_url") or "").strip() or "/images/Logo.png"
        current_image = current_image_url or str(current_card.get("image_url") or "").strip() or base_image
        proposed_image = proposed_image_url or str(proposed_card.get("image_url") or "").strip() or current_image

        hide_base_current = _should_hide_card_article_base_current_panels(
            proposal=proposal,
            base_raw=base_raw,
            current_raw=current_raw,
        )
        return (
            "" if hide_base_current else _render_card_article_preview("Base (tarjeta + artículo)", base_card, base_article),
            "" if hide_base_current else _render_card_article_preview("Actual (tarjeta + artículo)", current_card, current_article),
            _render_card_article_preview("Propuesto (tarjeta + artículo)", proposed_card, proposed_article),
            [],
            collapse_base_current,
        )

    base_lines = str(base_raw or "").splitlines()
    current_lines = str(current_raw or "").splitlines()
    proposed_lines = str(proposed_raw or "").splitlines()
    _base_for_current, highlighted_current = _highlight_markdown_pair(
        "\n".join(base_lines),
        "\n".join(current_lines),
    )
    _base_for_proposed, highlighted_proposed = _highlight_markdown_pair(
        "\n".join(base_lines),
        "\n".join(proposed_lines),
    )
    highlighted_current_lines = highlighted_current.splitlines()
    highlighted_proposed_lines = highlighted_proposed.splitlines()

    if len(highlighted_current_lines) != len(current_lines):
        highlighted_current_lines = current_lines
    if len(highlighted_proposed_lines) != len(proposed_lines):
        highlighted_proposed_lines = proposed_lines

    def _range_has_substantive_lines(lines: Sequence[str], start: int, end: int) -> bool:
        lower = max(0, int(start))
        upper = min(int(end), len(lines))
        if lower >= upper:
            return False
        return any(str(lines[index] or "").strip() for index in range(lower, upper))

    def _contains_reference_block(target_lines: Sequence[str], reference_lines: Sequence[str]) -> bool:
        reference_keys = [
            key
            for reference_line in reference_lines
            if (key := _canonicalize_compiled_diff_line(reference_line))
        ]
        if not reference_keys:
            return False
        target_keys = [_canonicalize_compiled_diff_line(target_line) for target_line in target_lines]
        if len(reference_keys) > len(target_keys):
            return False
        for start in range(0, len(target_keys) - len(reference_keys) + 1):
            if target_keys[start : start + len(reference_keys)] == reference_keys:
                return True
        return False

    group_candidates: List[Dict[str, int]] = []
    # Grouping is anchored to base->current and base->proposed deltas so unchanged base
    # content does not get highlighted as "new" in proposed/current panels.
    base_current_matcher = _line_matcher(base_lines, current_lines)
    for opcode, b1, b2, c1, c2 in base_current_matcher.get_opcodes():
        if opcode == "equal":
            continue
        if not _range_has_substantive_lines(base_lines, b1, b2) and not _range_has_substantive_lines(
            current_lines,
            c1,
            c2,
        ):
            continue
        proposed_start, _proposed_end = _map_base_range_to_version(base_lines, proposed_lines, b1, b2)
        group_candidates.append(
            {
                "base_start": b1,
                "base_end": b2,
                "current_start": c1,
                "current_end": c2,
                "proposed_start": proposed_start,
                "proposed_end": proposed_start,
                "current_changed": True,
                "proposed_changed": False,
            }
        )
    base_proposed_matcher = _line_matcher(base_lines, proposed_lines)
    for opcode, b1, b2, p1, p2 in base_proposed_matcher.get_opcodes():
        if opcode == "equal":
            continue
        if not _range_has_substantive_lines(base_lines, b1, b2) and not _range_has_substantive_lines(
            proposed_lines,
            p1,
            p2,
        ):
            continue
        current_start, _current_end = _map_base_range_to_version(base_lines, current_lines, b1, b2)
        group_candidates.append(
            {
                "base_start": b1,
                "base_end": b2,
                "current_start": current_start,
                "current_end": current_start,
                "proposed_start": p1,
                "proposed_end": p2,
                "current_changed": False,
                "proposed_changed": True,
            }
        )

    unique_candidates: List[Dict[str, int]] = []
    seen_ranges: set[tuple[int, int, int, int, int, int, bool, bool]] = set()
    for candidate in group_candidates:
        key = (
            int(candidate.get("base_start") or 0),
            int(candidate.get("base_end") or 0),
            int(candidate.get("current_start") or 0),
            int(candidate.get("current_end") or 0),
            int(candidate.get("proposed_start") or 0),
            int(candidate.get("proposed_end") or 0),
            bool(candidate.get("current_changed")),
            bool(candidate.get("proposed_changed")),
        )
        if key in seen_ranges:
            continue
        seen_ranges.add(key)
        unique_candidates.append(
            {
                "base_start": key[0],
                "base_end": key[1],
                "current_start": key[2],
                "current_end": key[3],
                "proposed_start": key[4],
                "proposed_end": key[5],
                "current_changed": key[6],
                "proposed_changed": key[7],
            }
        )

    unique_candidates.sort(
        key=lambda item: (
            min(
                int(item.get("base_start") or 0),
                int(item.get("current_start") or 0),
                int(item.get("proposed_start") or 0),
            ),
            int(item.get("base_start") or 0),
            int(item.get("current_start") or 0),
            int(item.get("proposed_start") or 0),
        )
    )

    def _side_ranges_overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
        if a_start == a_end and b_start == b_end:
            return a_start == b_start
        return a_start < b_end and b_start < a_end

    def _groups_overlap(a: Dict[str, int], b: Dict[str, int]) -> bool:
        shares_current_side = bool(a.get("current_changed")) and bool(b.get("current_changed"))
        shares_proposed_side = bool(a.get("proposed_changed")) and bool(b.get("proposed_changed"))
        # Do not merge groups that only overlap in base range while affecting
        # different sides; that causes placeholder bleed/duplication.
        if not shares_current_side and not shares_proposed_side:
            return False
        current_overlap = False
        if shares_current_side:
            current_overlap = _side_ranges_overlap(
                int(a.get("current_start") or 0),
                int(a.get("current_end") or 0),
                int(b.get("current_start") or 0),
                int(b.get("current_end") or 0),
            )
        proposed_overlap = False
        if shares_proposed_side:
            proposed_overlap = _side_ranges_overlap(
                int(a.get("proposed_start") or 0),
                int(a.get("proposed_end") or 0),
                int(b.get("proposed_start") or 0),
                int(b.get("proposed_end") or 0),
            )
        return (
            _side_ranges_overlap(
                int(a.get("base_start") or 0),
                int(a.get("base_end") or 0),
                int(b.get("base_start") or 0),
                int(b.get("base_end") or 0),
            )
            or current_overlap
            or proposed_overlap
        )

    merged_candidates: List[Dict[str, int]] = []
    for candidate in unique_candidates:
        if not merged_candidates:
            merged_candidates.append(dict(candidate))
            continue
        last = merged_candidates[-1]
        if not _groups_overlap(last, candidate):
            merged_candidates.append(dict(candidate))
            continue
        for side in ("base", "current", "proposed"):
            start_key = f"{side}_start"
            end_key = f"{side}_end"
            if side == "base":
                last[start_key] = min(int(last.get(start_key) or 0), int(candidate.get(start_key) or 0))
                last[end_key] = max(int(last.get(end_key) or 0), int(candidate.get(end_key) or 0))
                continue
            changed_key = f"{side}_changed"
            last_changed = bool(last.get(changed_key))
            candidate_changed = bool(candidate.get(changed_key))
            if last_changed and candidate_changed:
                last[start_key] = min(int(last.get(start_key) or 0), int(candidate.get(start_key) or 0))
                last[end_key] = max(int(last.get(end_key) or 0), int(candidate.get(end_key) or 0))
                continue
            if not last_changed and candidate_changed:
                last[start_key] = int(candidate.get(start_key) or 0)
                last[end_key] = int(candidate.get(end_key) or 0)
            last[changed_key] = last_changed or candidate_changed

    change_groups: List[Dict[str, int]] = []
    for group_id, candidate in enumerate(merged_candidates, start=1):
        change_groups.append(
            {
                "id": group_id,
                "base_start": int(candidate.get("base_start") or 0),
                "base_end": int(candidate.get("base_end") or 0),
                "current_start": int(candidate.get("current_start") or 0),
                "current_end": int(candidate.get("current_end") or 0),
                "proposed_start": int(candidate.get("proposed_start") or 0),
                "proposed_end": int(candidate.get("proposed_end") or 0),
                "current_changed": bool(candidate.get("current_changed")),
                "proposed_changed": bool(candidate.get("proposed_changed")),
            }
        )

    def _render_side_output(
        side: str,
        raw_lines: Sequence[str],
        decorated_lines: Sequence[str],
    ) -> List[str]:
        total = len(raw_lines)
        line_group_ids: List[int] = [0] * total
        placeholder_lines: Dict[int, List[str]] = {}

        for group in change_groups:
            group_id = int(group.get("id") or 0)
            side_changed_key = f"{side}_changed"
            if side in {"current", "proposed"} and not bool(group.get(side_changed_key)):
                continue
            start_key = f"{side}_start"
            end_key = f"{side}_end"
            start = max(0, min(int(group.get(start_key) or 0), total))
            end = max(start, min(int(group.get(end_key) or start), total))
            if start >= end:
                if side in {"current", "proposed"}:
                    base_start = max(0, min(int(group.get("base_start") or 0), len(base_lines)))
                    base_end = max(base_start, min(int(group.get("base_end") or base_start), len(base_lines)))
                    reference_lines = list(base_lines[base_start:base_end])
                    # Keep compatibility for ranges that originate from current/proposed-only edits.
                    if not reference_lines and side == "proposed":
                        current_start = max(0, min(int(group.get("current_start") or 0), len(current_lines)))
                        current_end = max(
                            current_start,
                            min(int(group.get("current_end") or current_start), len(current_lines)),
                        )
                        reference_lines = list(current_lines[current_start:current_end])
                    if reference_lines:
                        if _contains_reference_block(raw_lines, reference_lines):
                            continue
                        target = max(0, min(start, total))
                        placeholder_lines.setdefault(target, []).extend(
                            placeholder_markup
                            for reference_line in reference_lines
                            if (
                                placeholder_markup := _render_group_placeholder_line(
                                    reference_line,
                                    group_id,
                                    side,
                                    "removed",
                                )
                            )
                        )
                continue
            for line_index in range(start, end):
                if 0 <= line_index < total and line_group_ids[line_index] == 0:
                    line_group_ids[line_index] = group_id

        output: List[str] = []
        for line_index in range(total + 1):
            output.extend(placeholder_lines.get(line_index, []))
            if line_index >= total:
                break
            line_value = decorated_lines[line_index] if line_index < len(decorated_lines) else raw_lines[line_index]
            group_id = line_group_ids[line_index]
            if group_id > 0:
                output.append(_wrap_grouped_change_markup(line_value, group_id, side))
            else:
                output.append(line_value)
        return output

    base_output = _render_side_output("base", base_lines, base_lines)
    current_output = _render_side_output("current", current_lines, highlighted_current_lines)
    proposed_output = _render_side_output("proposed", proposed_lines, highlighted_proposed_lines)
    return (
        _render_review_article_compiled_panel(base_title, "\n".join(base_output)),
        _render_review_article_compiled_panel("Estado actual (compilado)", "\n".join(current_output)),
        _render_review_article_compiled_panel("Estado propuesto (compilado)", "\n".join(proposed_output)),
        change_groups,
        collapse_base_current,
    )


def _build_article_compiled_panel_updates(
    compiled_base: str,
    compiled_current: str,
    compiled_proposed: str,
    *,
    collapse_base_current: bool = False,
) -> tuple[gr.update, gr.update, gr.update]:
    return (
        gr.update(value=compiled_base, visible=True),
        gr.update(value=compiled_current, visible=not collapse_base_current),
        gr.update(value=compiled_proposed, visible=True),
    )


def _build_card_compiled_panel_updates(
    compiled_base: str,
    compiled_current: str,
    compiled_proposed: str,
    *,
    collapse_base_current: bool = False,
) -> tuple[gr.update, gr.update, gr.update]:
    return (
        gr.update(value=compiled_base, visible=True),
        gr.update(value=compiled_current, visible=not collapse_base_current),
        gr.update(value=compiled_proposed, visible=True),
    )


def _build_card_article_compiled_panel_updates(
    compiled_base: str,
    compiled_current: str,
    compiled_proposed: str,
    *,
    collapse_base_current: bool = False,
) -> tuple[gr.update, gr.update, gr.update]:
    base_visible = bool(str(compiled_base or "").strip())
    current_visible = bool(str(compiled_current or "").strip()) and not collapse_base_current
    proposed_visible = bool(str(compiled_proposed or "").strip())
    return (
        gr.update(value=compiled_base, visible=base_visible),
        gr.update(value=compiled_current, visible=current_visible),
        gr.update(value=compiled_proposed, visible=proposed_visible),
    )


def _build_admin_compiled_panel_updates(
    compiled_base: str,
    compiled_current: str,
    compiled_proposed: str,
    collapse_base_current: bool,
    scope: str = PROPOSAL_SCOPE_ARTICLE,
) -> tuple[gr.update, gr.update, gr.update]:
    normalized_scope = _normalize_proposal_scope(scope)
    if normalized_scope == PROPOSAL_SCOPE_CARD_ARTICLE:
        return _build_card_article_compiled_panel_updates(
            compiled_base,
            compiled_current,
            compiled_proposed,
            collapse_base_current=collapse_base_current,
        )
    if normalized_scope == PROPOSAL_SCOPE_CARD:
        return _build_card_compiled_panel_updates(
            compiled_base,
            compiled_current,
            compiled_proposed,
            collapse_base_current=collapse_base_current,
        )
    return _build_article_compiled_panel_updates(
        compiled_base,
        compiled_current,
        compiled_proposed,
        collapse_base_current=collapse_base_current,
    )


def _build_admin_display_panel_updates(
    view_mode: str,
    scope: str,
    compiled_base: str,
    compiled_current: str,
    compiled_proposed: str,
    raw_base: str,
    raw_current: str,
    raw_proposed: str,
    *,
    collapse_base_current: bool,
) -> tuple[gr.update, gr.update, gr.update]:
    normalized_scope = _normalize_proposal_scope(scope)
    normalized_view_mode = _normalize_review_view_mode(view_mode)
    if normalized_view_mode != REVIEW_VIEW_RAW:
        return _build_admin_compiled_panel_updates(
            compiled_base,
            compiled_current,
            compiled_proposed,
            collapse_base_current=collapse_base_current,
            scope=normalized_scope,
        )

    if normalized_scope == PROPOSAL_SCOPE_CARD_ARTICLE:
        base_title = "Estado base (=estado actual) (compilado)" if collapse_base_current else "Estado base (compilado)"
        hide_base_current = _should_hide_card_article_base_current_panels(
            proposal=None,
            base_raw=raw_base,
            current_raw=raw_current,
        )
        return _build_card_article_compiled_panel_updates(
            ""
            if hide_base_current
            else _render_review_article_raw_panel(base_title, _format_card_article_raw_payload_for_display(raw_base)),
            ""
            if hide_base_current
            else _render_review_article_raw_panel(
                "Estado actual (compilado)",
                _format_card_article_raw_payload_for_display(raw_current),
            ),
            _render_review_article_raw_panel(
                "Estado propuesto (compilado)",
                _format_card_article_raw_payload_for_display(raw_proposed),
            ),
            collapse_base_current=collapse_base_current,
        )

    if normalized_scope != PROPOSAL_SCOPE_ARTICLE:
        return _build_admin_compiled_panel_updates(
            compiled_base,
            compiled_current,
            compiled_proposed,
            collapse_base_current=collapse_base_current,
            scope=normalized_scope,
        )

    base_title = "Estado base (=estado actual) (compilado)" if collapse_base_current else "Estado base (compilado)"
    return _build_article_compiled_panel_updates(
        _render_review_article_raw_panel(base_title, raw_base),
        _render_review_article_raw_diff_panel("Estado actual (compilado)", raw_base, raw_current, "current"),
        _render_review_article_raw_diff_panel("Estado propuesto (compilado)", raw_base, raw_proposed, "proposed"),
        collapse_base_current=collapse_base_current,
    )


def _build_article_raw_panel_updates(
    raw_base: str,
    raw_current: str,
    raw_proposed: str,
    _diff_html: str,
) -> tuple[gr.update, gr.update, gr.update, gr.update]:
    return (
        gr.update(value=raw_base, visible=False, interactive=False),
        gr.update(value=raw_current, visible=False, interactive=False),
        gr.update(value=raw_proposed, visible=False, interactive=False),
        gr.update(value="", visible=False),
    )


def _build_card_raw_panel_updates(
    raw_base: str,
    raw_current: str,
    raw_proposed: str,
    _diff_html: str,
) -> tuple[gr.update, gr.update, gr.update, gr.update]:
    return (
        gr.update(value=raw_base, visible=False, interactive=False),
        gr.update(value=raw_current, visible=False, interactive=False),
        gr.update(value=raw_proposed, visible=False, interactive=False),
        gr.update(value="", visible=False),
    )


def _build_card_article_raw_panel_updates(
    raw_base: str,
    raw_current: str,
    raw_proposed: str,
    _diff_html: str,
) -> tuple[gr.update, gr.update, gr.update, gr.update]:
    return (
        gr.update(value=raw_base, visible=False, interactive=False),
        gr.update(value=raw_current, visible=False, interactive=False),
        gr.update(value=raw_proposed, visible=False, interactive=False),
        gr.update(value="", visible=False),
    )


def _build_admin_raw_panel_updates(
    raw_base: str,
    raw_current: str,
    raw_proposed: str,
    diff_html: str,
    scope: str = PROPOSAL_SCOPE_ARTICLE,
) -> tuple[gr.update, gr.update, gr.update, gr.update]:
    normalized_scope = _normalize_proposal_scope(scope)
    if normalized_scope == PROPOSAL_SCOPE_CARD_ARTICLE:
        return _build_card_article_raw_panel_updates(raw_base, raw_current, raw_proposed, diff_html)
    if normalized_scope == PROPOSAL_SCOPE_CARD:
        return _build_card_raw_panel_updates(raw_base, raw_current, raw_proposed, diff_html)
    return _build_article_raw_panel_updates(raw_base, raw_current, raw_proposed, diff_html)


def _build_selected_proposal_panel_content(
    proposal: Dict[str, object] | None,
    selected_dataset_entry: str = "",
    review_view_mode: str = DEFAULT_REVIEW_VIEW,
) -> tuple[str, str, gr.update, gr.update, gr.update, gr.update, gr.update, gr.update, gr.update, str, str]:
    if proposal is None:
        empty_compiled_base = _render_review_markdown_panel("Estado base (compilado)", "")
        empty_compiled_current = _render_review_markdown_panel("Estado actual (compilado)", "")
        empty_compiled_proposed = _render_review_markdown_panel("Estado propuesto (compilado)", "")
        (
            compiled_base_update,
            compiled_current_update,
            compiled_proposed_update,
        ) = _build_admin_display_panel_updates(
            review_view_mode,
            PROPOSAL_SCOPE_ARTICLE,
            empty_compiled_base,
            empty_compiled_current,
            empty_compiled_proposed,
            "",
            "",
            "",
            collapse_base_current=False,
        )
        (
            raw_base_update,
            raw_current_update,
            raw_proposed_update,
            diff_update,
        ) = _build_admin_raw_panel_updates(
            "",
            "",
            "",
            _render_empty_diff("No se pudo cargar la propuesta seleccionada."),
            scope=PROPOSAL_SCOPE_ARTICLE,
        )
        return (
            "No se pudo cargar la propuesta seleccionada.",
            _render_empty_diff("No se pudo cargar la propuesta seleccionada."),
            compiled_base_update,
            compiled_current_update,
            compiled_proposed_update,
            raw_base_update,
            raw_current_update,
            raw_proposed_update,
            diff_update,
            "",
            "[]",
        )

    selected_scope = _normalize_proposal_scope(proposal.get("proposal_scope"))
    (
        compiled_base,
        compiled_current,
        compiled_proposed,
        raw_base,
        raw_current,
        raw_proposed,
        images_html,
        diff_html,
        _conflict_fields,
        change_groups_json,
        collapse_base_current,
    ) = _build_review_states(proposal)
    (
        compiled_base_update,
        compiled_current_update,
        compiled_proposed_update,
    ) = _build_admin_display_panel_updates(
        review_view_mode,
        selected_scope,
        compiled_base,
        compiled_current,
        compiled_proposed,
        raw_base,
        raw_current,
        raw_proposed,
        collapse_base_current=collapse_base_current,
    )
    (
        raw_base_update,
        raw_current_update,
        raw_proposed_update,
        diff_update,
    ) = _build_admin_raw_panel_updates(
        raw_base,
        raw_current,
        raw_proposed,
        diff_html,
        scope=selected_scope,
    )
    return (
        _render_proposal_meta(proposal, selected_dataset_entry),
        images_html,
        compiled_base_update,
        compiled_current_update,
        compiled_proposed_update,
        raw_base_update,
        raw_current_update,
        raw_proposed_update,
        diff_update,
        selected_scope,
        change_groups_json,
    )


def _build_review_states(
    proposal: Dict[str, object],
) -> tuple[str, str, str, str, str, str, str, str, List[str], str, bool]:
    scope = _normalize_proposal_scope(proposal.get("proposal_scope"))
    proposal_source = _normalize_proposal_source(proposal.get("proposal_source"))
    person_slug = str(proposal.get("person_slug") or "").strip().lower()
    person: Dict[str, object] | None = None
    current_slug = str(proposal.get("current_person_slug") or person_slug).strip().lower()
    current_person_id = int(proposal.get("current_person_id") or proposal.get("person_id") or 0)
    current_name = str(proposal.get("current_name") or "").strip()
    current_title = str(proposal.get("current_title") or "").strip()
    current_bucket = str(proposal.get("current_bucket") or "").strip()
    current_image_url_value = str(proposal.get("current_image_url") or "").strip()
    current_markdown_value = str(proposal.get("current_markdown") or "")
    current_tags = _decode_tags(proposal.get("current_tags_json"))
    if (
        current_slug
        and (
            current_person_id > 0
            or current_name
            or current_markdown_value
            or current_image_url_value
            or current_tags
        )
    ):
        person = {
            "slug": current_slug,
            "person_id": current_person_id,
            "name": current_name or _display_name_from_slug(current_slug),
            "title": current_title or current_bucket or "Sin asignar",
            "bucket": current_bucket or current_title or "Sin asignar",
            "image_url": current_image_url_value,
            "tags": current_tags,
            "markdown": current_markdown_value,
        }
    if person is None:
        person = _fetch_profile_for_source(person_slug, proposal_source)

    base_raw = str(proposal.get("base_markdown") or "")
    submitted_proposed_raw = str(proposal.get("proposed_markdown") or "")
    current_raw = base_raw if person is None else str(person.get("markdown") or "")
    proposed_raw = submitted_proposed_raw
    conflict_fields: List[str] = []

    if scope == PROPOSAL_SCOPE_CARD:
        base_snapshot = _deserialize_card_snapshot(base_raw)
        current_snapshot = _card_snapshot_from_person(person) if person else base_snapshot
        submitted_snapshot = _deserialize_card_snapshot(submitted_proposed_raw, fallback=base_snapshot)
        proposed_snapshot, card_conflicts = _merge_three_way_card_snapshots(
            base_snapshot,
            current_snapshot,
            submitted_snapshot,
        )
        base_raw = json.dumps(base_snapshot, ensure_ascii=True, indent=2, sort_keys=True)
        current_raw = json.dumps(current_snapshot, ensure_ascii=True, indent=2, sort_keys=True)
        proposed_raw = json.dumps(proposed_snapshot, ensure_ascii=True, indent=2, sort_keys=True)
        conflict_fields.extend(f"card.{field}" for field in card_conflicts)
    elif scope == PROPOSAL_SCOPE_CARD_ARTICLE:
        # Parse combined payloads - keep raw as-is for the diff/preview functions
        # They will deserialize internally
        base_raw = str(proposal.get("base_markdown") or "")
        proposed_raw = submitted_proposed_raw
        # For current, we need to build the combined payload from current person state
        if person is not None:
            current_card_snapshot = _card_snapshot_from_person(person)
            current_article_markdown = str(person.get("markdown") or "")
            current_raw = json.dumps({
                "card": current_card_snapshot,
                "article": current_article_markdown,
            }, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        else:
            current_raw = base_raw
    else:
        proposed_raw, text_conflict = _merge_three_way_text(base_raw, current_raw, submitted_proposed_raw)
        if text_conflict:
            conflict_fields.append("markdown")

    base_image_url = ""
    current_image_url = ""
    proposed_image_url = ""
    if scope == PROPOSAL_SCOPE_CARD:
        base_image_url = str(proposal.get("base_image_url") or "").strip()
        submitted_proposed_image_url = str(proposal.get("proposed_image_url") or "").strip() or base_image_url
        current_image_url = str((person or {}).get("image_url") or "").strip() or base_image_url
        proposed_image_url, image_conflict = _merge_three_way_scalar(
            base_image_url,
            current_image_url,
            submitted_proposed_image_url,
        )
        if image_conflict:
            conflict_fields.append("image")
    elif scope == PROPOSAL_SCOPE_CARD_ARTICLE:
        # Extract image URLs from combined payloads
        base_combined = _deserialize_card_article_snapshot(base_raw)
        proposed_combined = _deserialize_card_article_snapshot(proposed_raw)
        base_card = base_combined.get("card", {})
        proposed_card = proposed_combined.get("card", {})
        base_image_url = str(proposal.get("base_image_url") or base_card.get("image_url") or "").strip()
        submitted_proposed_image_url = str(proposal.get("proposed_image_url") or proposed_card.get("image_url") or "").strip() or base_image_url
        current_image_url = str((person or {}).get("image_url") or "").strip() or base_image_url
        proposed_image_url = submitted_proposed_image_url or current_image_url or "/images/Logo.png"
    else:
        current_image_url = (
            str((person or {}).get("image_url") or "").strip()
            or str(proposal.get("current_image_url") or "").strip()
        )

    (
        compiled_base,
        compiled_current,
        compiled_proposed,
        change_groups,
        collapse_base_current,
    ) = _render_grouped_current_proposed_panels(
        scope,
        base_raw,
        current_raw,
        proposed_raw,
        base_image_url=base_image_url,
        current_image_url=current_image_url,
        proposed_image_url=proposed_image_url,
        proposal=proposal,
    )
    images_html = _render_proposal_images(base_image_url, current_image_url, proposed_image_url)
    render_as_snapshot = _should_render_creation_snapshot(proposal, scope, base_raw, current_raw)
    diff_html = _render_proposal_diff_table(
        current_raw,
        proposed_raw,
        scope,
        current_image_url=current_image_url,
        proposed_image_url=proposed_image_url,
        render_as_snapshot=render_as_snapshot,
    )
    return (
        compiled_base,
        compiled_current,
        compiled_proposed,
        base_raw,
        current_raw,
        proposed_raw,
        images_html,
        diff_html,
        conflict_fields,
        json.dumps(change_groups, ensure_ascii=True),
        collapse_base_current,
    )


def _split_markdown_line_prefix(line: str) -> tuple[str, str]:
    match = _MARKDOWN_LINE_PREFIX_RE.match(line)
    if not match:
        return "", line
    return match.group(1), match.group(2)


def _tokenize_for_inline_diff(value: str) -> List[str]:
    return _DIFF_TOKEN_RE.findall(value)


def _normalize_inline_spacing(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _table_separator_cells(line: str) -> List[str] | None:
    stripped = (line or "").strip()
    if "|" not in stripped:
        return None
    cells = [cell.strip() for cell in stripped.strip("|").split("|")]
    if not cells or any(not cell for cell in cells):
        return None
    if all(_TABLE_SEPARATOR_CELL_RE.fullmatch(cell) for cell in cells):
        return cells
    return None


def _canonicalize_compiled_diff_line(line: str) -> str:
    stripped = (line or "").strip()
    if not stripped:
        return ""
    separator_cells = _table_separator_cells(stripped)
    if separator_cells is not None:
        return f"__table_separator__:{len(separator_cells)}"
    return stripped


def _is_markdown_table_row(line: str) -> bool:
    stripped = (line or "").strip()
    if not stripped.startswith("|"):
        return False
    return stripped.count("|") >= 2


def _line_matcher(base_lines: Sequence[str], proposed_lines: Sequence[str]) -> difflib.SequenceMatcher:
    base_keys = [_canonicalize_compiled_diff_line(line) for line in base_lines]
    proposed_keys = [_canonicalize_compiled_diff_line(line) for line in proposed_lines]
    return difflib.SequenceMatcher(lambda token: token == "", base_keys, proposed_keys, autojunk=False)


def _wrap_change(text: str, tag: str) -> str:
    escaped = html.escape(text)
    if not escaped:
        return ""
    return f"<{tag}>{escaped}</{tag}>"


def _diff_inline_segments(current_text: str, proposed_text: str) -> tuple[str, str]:
    if _normalize_inline_spacing(current_text) == _normalize_inline_spacing(proposed_text):
        return current_text, proposed_text
    current_tokens = _tokenize_for_inline_diff(current_text)
    proposed_tokens = _tokenize_for_inline_diff(proposed_text)
    matcher = difflib.SequenceMatcher(a=current_tokens, b=proposed_tokens, autojunk=False)
    current_parts: List[str] = []
    proposed_parts: List[str] = []

    for opcode, i1, i2, j1, j2 in matcher.get_opcodes():
        current_chunk = "".join(current_tokens[i1:i2])
        proposed_chunk = "".join(proposed_tokens[j1:j2])
        if opcode == "equal":
            current_parts.append(current_chunk)
            proposed_parts.append(proposed_chunk)
            continue
        if opcode == "delete":
            current_parts.append(_wrap_change(current_chunk, "del"))
            continue
        if opcode == "insert":
            proposed_parts.append(_wrap_change(proposed_chunk, "ins"))
            continue
        current_parts.append(_wrap_change(current_chunk, "del"))
        proposed_parts.append(_wrap_change(proposed_chunk, "ins"))

    return "".join(current_parts), "".join(proposed_parts)


def _wrap_line_change(line: str, tag: str) -> str:
    if not line:
        return ""
    if _table_separator_cells(line) is not None or _is_markdown_table_row(line):
        return line
    prefix, content = _split_markdown_line_prefix(line)
    wrapped_body = _wrap_change(content if (prefix and content) else line, tag)
    if prefix and content:
        return f"{prefix}<mark>{wrapped_body}</mark>"
    return f"<mark>{wrapped_body}</mark>"


def _wrap_changed_line_context(line_markup: str) -> str:
    if not line_markup:
        return ""
    if _table_separator_cells(line_markup) is not None or _is_markdown_table_row(line_markup):
        return line_markup
    prefix, content = _split_markdown_line_prefix(line_markup)
    if prefix and content:
        return f"{prefix}<mark>{content}</mark>"
    return f"<mark>{line_markup}</mark>"


def _append_inline_line_diff(
    current_line: str,
    proposed_line: str,
    current_output: List[str],
    proposed_output: List[str],
) -> None:
    if _canonicalize_compiled_diff_line(current_line) == _canonicalize_compiled_diff_line(proposed_line):
        current_output.append(current_line)
        proposed_output.append(proposed_line)
        return

    current_prefix, current_body = _split_markdown_line_prefix(current_line)
    proposed_prefix, proposed_body = _split_markdown_line_prefix(proposed_line)
    if current_prefix == proposed_prefix and current_body and proposed_body:
        current_diff, proposed_diff = _diff_inline_segments(current_body, proposed_body)
        if current_diff == current_body and proposed_diff == proposed_body:
            current_output.append(current_line)
            proposed_output.append(proposed_line)
            return
        current_output.append(_wrap_changed_line_context(f"{current_prefix}{current_diff}"))
        proposed_output.append(_wrap_changed_line_context(f"{proposed_prefix}{proposed_diff}"))
        return

    current_diff, proposed_diff = _diff_inline_segments(current_line, proposed_line)
    if current_diff == current_line and proposed_diff == proposed_line:
        current_output.append(current_line)
        proposed_output.append(proposed_line)
        return
    current_output.append(_wrap_changed_line_context(current_diff))
    proposed_output.append(_wrap_changed_line_context(proposed_diff))


def _line_similarity(current_line: str, proposed_line: str) -> float:
    return difflib.SequenceMatcher(
        a=_canonicalize_compiled_diff_line(current_line),
        b=_canonicalize_compiled_diff_line(proposed_line),
        autojunk=False,
    ).ratio()


def _append_replace_block_diff(
    current_lines: Sequence[str],
    proposed_lines: Sequence[str],
    current_output: List[str],
    proposed_output: List[str],
) -> None:
    i = 0
    j = 0
    while i < len(current_lines) or j < len(proposed_lines):
        has_current = i < len(current_lines)
        has_proposed = j < len(proposed_lines)
        current_line = current_lines[i] if has_current else ""
        proposed_line = proposed_lines[j] if has_proposed else ""
        if has_current and has_proposed:
            if _canonicalize_compiled_diff_line(current_line) == _canonicalize_compiled_diff_line(proposed_line):
                current_output.append(current_line)
                proposed_output.append(proposed_line)
                i += 1
                j += 1
                continue

            current_blank = not current_line.strip()
            proposed_blank = not proposed_line.strip()
            if current_blank and not proposed_blank and i + 1 < len(current_lines):
                if _line_similarity(current_lines[i + 1], proposed_line) >= 0.6:
                    current_output.append(current_line)
                    i += 1
                    continue
            if proposed_blank and not current_blank and j + 1 < len(proposed_lines):
                if _line_similarity(current_line, proposed_lines[j + 1]) >= 0.6:
                    proposed_output.append(proposed_line)
                    j += 1
                    continue

            _append_inline_line_diff(current_line, proposed_line, current_output, proposed_output)
            i += 1
            j += 1
            continue
        if has_current:
            current_output.append(_wrap_line_change(current_line, "del"))
            i += 1
            continue
        if has_proposed:
            proposed_output.append(_wrap_line_change(proposed_line, "ins"))
            j += 1


def _highlight_markdown_pair(base_markdown: str, proposed_markdown: str) -> tuple[str, str]:
    base_lines = base_markdown.splitlines()
    proposed_lines = proposed_markdown.splitlines()
    current_output: List[str] = []
    proposed_output: List[str] = []
    matcher = _line_matcher(base_lines, proposed_lines)

    for opcode, i1, i2, j1, j2 in matcher.get_opcodes():
        if opcode == "equal":
            current_output.extend(base_lines[i1:i2])
            proposed_output.extend(proposed_lines[j1:j2])
            continue
        if opcode == "delete":
            current_output.extend(_wrap_line_change(line, "del") for line in base_lines[i1:i2])
            continue
        if opcode == "insert":
            proposed_output.extend(_wrap_line_change(line, "ins") for line in proposed_lines[j1:j2])
            continue

        current_chunk = base_lines[i1:i2]
        proposed_chunk = proposed_lines[j1:j2]
        nested_matcher = _line_matcher(current_chunk, proposed_chunk)
        for nested_opcode, ni1, ni2, nj1, nj2 in nested_matcher.get_opcodes():
            if nested_opcode == "equal":
                current_output.extend(current_chunk[ni1:ni2])
                proposed_output.extend(proposed_chunk[nj1:nj2])
                continue
            if nested_opcode == "delete":
                current_output.extend(_wrap_line_change(line, "del") for line in current_chunk[ni1:ni2])
                continue
            if nested_opcode == "insert":
                proposed_output.extend(_wrap_line_change(line, "ins") for line in proposed_chunk[nj1:nj2])
                continue

            _append_replace_block_diff(
                current_chunk[ni1:ni2],
                proposed_chunk[nj1:nj2],
                current_output,
                proposed_output,
            )

    return "\n".join(current_output), "\n".join(proposed_output)


def _render_compiled_review_panels(
    base_markdown: str,
    proposed_markdown: str,
    scope: str = PROPOSAL_SCOPE_ARTICLE,
) -> tuple[str, str]:
    normalized_scope = _normalize_proposal_scope(scope)
    if normalized_scope == PROPOSAL_SCOPE_CARD:
        base_snapshot = _deserialize_card_snapshot(base_markdown)
        proposed_snapshot = _deserialize_card_snapshot(proposed_markdown, fallback=base_snapshot)
        base_render = _render_card_snapshot_markdown(base_snapshot)
        proposed_render = _render_card_snapshot_markdown(proposed_snapshot)
        highlighted_current, highlighted_proposed = _highlight_markdown_pair(base_render, proposed_render)
        return (
            _render_review_markdown_panel("Instantánea actual de la tarjeta (compilado)", highlighted_current),
            _render_review_markdown_panel("Instantánea propuesta de la tarjeta (compilado)", highlighted_proposed),
        )

    if normalized_scope == PROPOSAL_SCOPE_CARD_ARTICLE:
        # For card_article, render combined preview panels
        base_combined = _deserialize_card_article_snapshot(base_markdown)
        proposed_combined = _deserialize_card_article_snapshot(proposed_markdown)
        base_card = base_combined.get("card", {})
        proposed_card = proposed_combined.get("card", {})
        base_article = str(base_combined.get("article") or "")
        proposed_article = str(proposed_combined.get("article") or "")
        return (
            _render_card_article_preview("Actual (tarjeta + artículo)", base_card, base_article),
            _render_card_article_preview("Propuesto (tarjeta + artículo)", proposed_card, proposed_article),
        )

    highlighted_current, highlighted_proposed = _highlight_markdown_pair(base_markdown, proposed_markdown)
    return (
        _render_review_article_compiled_panel("Artículo actual (compilado)", highlighted_current),
        _render_review_article_compiled_panel("Artículo propuesto (compilado)", highlighted_proposed),
    )


def _render_plain_snapshot_panel(title: str, lines: Sequence[str]) -> str:
    body = "\n".join(lines).strip()
    if not body:
        body = "(vacío)"
    return (
        "<div class='proposal-diff'>"
        f"<h4 class='proposal-diff-title'>{html.escape(title)}</h4>"
        "<div class='proposal-diff-table'>"
        f"<pre class='proposal-diff-plain'>{html.escape(body)}</pre>"
        "</div>"
        "</div>"
    )


def _decode_event_payload(raw_payload: object) -> Dict[str, object]:
    if isinstance(raw_payload, dict):
        return dict(raw_payload)
    try:
        parsed = json.loads(str(raw_payload or ""))
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return parsed


def _proposal_has_new_profile_event(proposal: Dict[str, object] | None) -> bool:
    if not isinstance(proposal, dict):
        return False
    for event in _decode_events(proposal.get("events_json")):
        payload = _decode_event_payload(event.get("payload_json"))
        if bool(payload.get("is_new_profile")):
            return True
    return False


def _is_empty_card_snapshot(snapshot: Dict[str, object]) -> bool:
    tags = [_normalize_tag(str(tag)) for tag in snapshot.get("tags", []) if _normalize_tag(str(tag))]
    return (
        not str(snapshot.get("name") or "").strip()
        and not str(snapshot.get("title") or snapshot.get("bucket") or "").strip()
        and not tags
        and not str(snapshot.get("image_url") or "").strip()
    )


def _is_seed_only_card_snapshot(snapshot: Dict[str, object]) -> bool:
    tags = [_normalize_tag(str(tag)) for tag in snapshot.get("tags", []) if _normalize_tag(str(tag))]
    title = str(snapshot.get("title") or snapshot.get("bucket") or "").strip().lower()
    return (
        bool(str(snapshot.get("name") or "").strip())
        and title in {"", "unassigned"}
        and not tags
        and not str(snapshot.get("image_url") or "").strip()
    )


def _should_hide_card_article_base_current_panels(
    *,
    proposal: Dict[str, object] | None,
    base_raw: str,
    current_raw: str,
) -> bool:
    current_slug = str((proposal or {}).get("current_person_slug") or "").strip()
    current_person_id = int((proposal or {}).get("current_person_id") or 0)
    if (current_person_id <= 0 and not current_slug) and _proposal_has_new_profile_event(proposal):
        return True

    base_combined = _deserialize_card_article_snapshot(base_raw)
    current_combined = _deserialize_card_article_snapshot(current_raw)
    base_card = base_combined.get("card", {})
    current_card = current_combined.get("card", {})
    base_article = str(base_combined.get("article") or "").strip()
    current_article = str(current_combined.get("article") or "").strip()

    if base_article or current_article:
        return False
    if not _is_empty_card_snapshot(base_card):
        return False
    return _is_empty_card_snapshot(current_card) or _is_seed_only_card_snapshot(current_card)


def _should_render_creation_snapshot(
    proposal: Dict[str, object] | None,
    scope: str,
    base_raw: str,
    current_raw: str,
) -> bool:
    if _proposal_has_new_profile_event(proposal):
        return True

    normalized_scope = _normalize_proposal_scope(scope)
    current_slug = str((proposal or {}).get("current_person_slug") or "").strip()
    current_person_id = int((proposal or {}).get("current_person_id") or 0)

    if normalized_scope == PROPOSAL_SCOPE_CARD:
        base_snapshot = _deserialize_card_snapshot(base_raw)
        current_snapshot = _deserialize_card_snapshot(current_raw, fallback=base_snapshot)
        has_current_profile = bool(current_slug) or current_person_id > 0
        return (not has_current_profile) and _is_empty_card_snapshot(base_snapshot) and _is_empty_card_snapshot(current_snapshot)

    article_has_content = bool(str(base_raw or "").strip()) or bool(str(current_raw or "").strip())
    if article_has_content:
        return False
    if proposal is None:
        return True
    return not current_slug and current_person_id <= 0


def _render_proposal_diff_table(
    current_markdown: str,
    proposed_markdown: str,
    scope: str,
    *,
    current_image_url: str = "",
    proposed_image_url: str = "",
    render_as_snapshot: bool = False,
) -> str:
    normalized_scope = _normalize_proposal_scope(scope)
    if normalized_scope == PROPOSAL_SCOPE_CARD:
        current_snapshot = _deserialize_card_snapshot(current_markdown)
        proposed_snapshot = _deserialize_card_snapshot(proposed_markdown, fallback=current_snapshot)
        current_tags = [str(tag).strip() for tag in current_snapshot.get("tags", []) if str(tag).strip()]
        proposed_tags = [str(tag).strip() for tag in proposed_snapshot.get("tags", []) if str(tag).strip()]
        current_image = str(current_image_url or "").strip() or "(ninguno)"
        proposed_image = str(proposed_image_url or "").strip() or current_image
        current_lines = [
            f"Nombre: {str(current_snapshot.get('name') or '').strip() or '(vacío)'}",
            f"Título: {str(current_snapshot.get('title') or current_snapshot.get('bucket') or '').strip() or '(vacío)'}",
            f"Etiquetas: {', '.join(current_tags) if current_tags else '(ninguna)'}",
            f"URL de imagen: {current_image}",
        ]
        proposed_lines = [
            f"Nombre: {str(proposed_snapshot.get('name') or '').strip() or '(vacío)'}",
            f"Título: {str(proposed_snapshot.get('title') or proposed_snapshot.get('bucket') or '').strip() or '(vacío)'}",
            f"Etiquetas: {', '.join(proposed_tags) if proposed_tags else '(ninguna)'}",
            f"URL de imagen: {proposed_image}",
        ]
        if render_as_snapshot:
            return _render_plain_snapshot_panel("Instantánea de propuesta de tarjeta", proposed_lines)
        diff_table = difflib.HtmlDiff(tabsize=2, wrapcolumn=92).make_table(
            current_lines,
            proposed_lines,
            fromdesc="Instantánea actual de la tarjeta",
            todesc="Instantánea propuesta de la tarjeta",
            context=False,
            numlines=1,
        )
        return (
            "<div class='proposal-diff'>"
            "<h4 class='proposal-diff-title'>Diff de propuesta de tarjeta</h4>"
            "<div class='proposal-diff-table'>"
            f"{diff_table}"
            "</div>"
            "</div>"
        )

    if normalized_scope == PROPOSAL_SCOPE_CARD_ARTICLE:
        # Handle combined card+article payload
        current_combined = _deserialize_card_article_snapshot(current_markdown)
        proposed_combined = _deserialize_card_article_snapshot(proposed_markdown)
        current_card = current_combined.get("card", {})
        proposed_card = proposed_combined.get("card", {})
        current_article = str(current_combined.get("article") or "")
        proposed_article = str(proposed_combined.get("article") or "")
        
        current_tags = [str(tag).strip() for tag in current_card.get("tags", []) if str(tag).strip()]
        proposed_tags = [str(tag).strip() for tag in proposed_card.get("tags", []) if str(tag).strip()]
        current_image = str(current_image_url or "").strip() or str(current_card.get("image_url") or "").strip() or "(ninguno)"
        proposed_image = str(proposed_image_url or "").strip() or str(proposed_card.get("image_url") or "").strip() or current_image
        
        if render_as_snapshot:
            # Render as dedicated card+article boxes so both parts match published layout.
            return _render_card_article_snapshot_html("Propuesta de tarjeta + artículo", proposed_card, proposed_article)
        
        # Build card lines for diff
        current_card_lines = [
            "=== TARJETA ===",
            f"Nombre: {str(current_card.get('name') or '').strip() or '(vacío)'}",
            f"Título: {str(current_card.get('title') or '').strip() or '(vacío)'}",
            f"Etiquetas: {', '.join(current_tags) if current_tags else '(ninguna)'}",
            f"URL de imagen: {current_image}",
            "",
            "=== ARTÍCULO ===",
        ] + current_article.splitlines()
        
        proposed_card_lines = [
            "=== TARJETA ===",
            f"Nombre: {str(proposed_card.get('name') or '').strip() or '(vacío)'}",
            f"Título: {str(proposed_card.get('title') or '').strip() or '(vacío)'}",
            f"Etiquetas: {', '.join(proposed_tags) if proposed_tags else '(ninguna)'}",
            f"URL de imagen: {proposed_image}",
            "",
            "=== ARTÍCULO ===",
        ] + proposed_article.splitlines()
        
        diff_table = difflib.HtmlDiff(tabsize=2, wrapcolumn=92).make_table(
            current_card_lines,
            proposed_card_lines,
            fromdesc="Tarjeta + artículo actual",
            todesc="Tarjeta + artículo propuesto",
            context=True,
            numlines=2,
        )
        return (
            "<div class='proposal-diff'>"
            "<h4 class='proposal-diff-title'>Diff de propuesta de tarjeta + artículo</h4>"
            "<div class='proposal-diff-table'>"
            f"{diff_table}"
            "</div>"
            "</div>"
        )

    current_value = str(current_markdown or "")
    proposed_value = str(proposed_markdown or "")
    if render_as_snapshot:
        return _render_plain_snapshot_panel("Markdown en bruto del artículo", proposed_value.splitlines())
    diff_table = difflib.HtmlDiff(tabsize=2, wrapcolumn=92).make_table(
        current_value.splitlines(),
        proposed_value.splitlines(),
        fromdesc="Markdown actual del artículo",
        todesc="Markdown propuesto del artículo",
        context=True,
        numlines=2,
    )
    return (
        "<div class='proposal-diff'>"
        "<h4 class='proposal-diff-title'>Diff de markdown en bruto del artículo</h4>"
        "<div class='proposal-diff-table'>"
        f"{diff_table}"
        "</div>"
        "</div>"
    )


def _build_admin_panel(
    selected_proposal_id: str | None = None,
    slug_filter: str = "",
    prioritize_proposal_id: str | None = None,
    proposals: Sequence[Dict[str, object]] | None = None,
    review_view_mode: str = DEFAULT_REVIEW_VIEW,
) -> tuple[gr.update, str, str, gr.update, gr.update, gr.update, gr.update, gr.update, gr.update, gr.update, str, str]:
    normalized_slug_filter = (slug_filter or "").strip().lower()
    proposal_rows: List[Dict[str, object]]
    if proposals is None:
        proposal_rows = _fetch_change_proposals(slug_filter=normalized_slug_filter)
    else:
        proposal_rows = [dict(item) for item in proposals]
        if normalized_slug_filter:
            proposal_rows = [
                row for row in proposal_rows if str(row.get("person_slug") or "").strip().lower() == normalized_slug_filter
            ]

    if not proposal_rows:
        empty_compiled_base = _render_review_markdown_panel("Estado base (compilado)", "")
        empty_compiled_current = _render_review_markdown_panel("Estado actual (compilado)", "")
        empty_compiled_proposed = _render_review_markdown_panel("Estado propuesto (compilado)", "")
        (
            compiled_base_update,
            compiled_current_update,
            compiled_proposed_update,
        ) = _build_admin_display_panel_updates(
            review_view_mode,
            PROPOSAL_SCOPE_ARTICLE,
            empty_compiled_base,
            empty_compiled_current,
            empty_compiled_proposed,
            "",
            "",
            "",
            collapse_base_current=False,
        )
        (
            raw_base_update,
            raw_current_update,
            raw_proposed_update,
            diff_update,
        ) = _build_admin_raw_panel_updates(
            "",
            "",
            "",
            _render_empty_diff("No hay diff de propuesta para mostrar."),
            scope=PROPOSAL_SCOPE_ARTICLE,
        )
        return (
            gr.update(choices=[], value=None),
            "Aún no hay propuestas en seguimiento.",
            _render_empty_diff("No hay imágenes de propuesta para mostrar."),
            compiled_base_update,
            compiled_current_update,
            compiled_proposed_update,
            raw_base_update,
            raw_current_update,
            raw_proposed_update,
            diff_update,
            "",
            "[]",
        )

    by_id: Dict[str, Dict[str, object]] = {}
    choices: List[Tuple[str, str]] = []
    for proposal in proposal_rows:
        proposal_source = _normalize_proposal_source(proposal.get("proposal_source"))
        source_proposal_type = (
            _normalize_source_proposal_type(proposal.get("source_proposal_type"))
            if proposal_source == PROPOSAL_SOURCE_SOURCE
            else ""
        )
        pid = _proposal_choice_value(
            proposal_source,
            proposal.get("id"),
            source_proposal_type=source_proposal_type,
        )
        if not pid:
            continue
        status = (proposal.get("status") or "desconocido").strip().lower()
        scope = _normalize_proposal_scope(proposal.get("proposal_scope"))
        slug = str(proposal.get("person_slug") or "")
        proposer_user_id = int(proposal.get("proposer_user_id") or 0)
        proposer_identity = (
            str(proposal.get("proposer_email") or "").strip()
            or str(proposal.get("proposer_name") or "").strip()
            or (f"usuario#{proposer_user_id}" if proposer_user_id > 0 else "desconocido")
        )
        created = str(proposal.get("created_at") or "")
        dataset_entry = str(proposal.get("dataset_entry") or "").strip() or _format_scope_dataset_entry(scope, 1)
        source_label = _proposal_source_label(proposal_source)
        scope_label = (
            f"{scope}:{source_proposal_type}"
            if proposal_source == PROPOSAL_SOURCE_SOURCE
            else scope
        )
        label = f"{source_label} {dataset_entry} [{status}/{scope_label}] {slug} - {proposer_identity} ({created})"
        choices.append((label, pid))
        by_id[pid] = proposal

    if not choices:
        return _build_admin_panel(
            selected_proposal_id=None,
            slug_filter=normalized_slug_filter,
            proposals=[],
            review_view_mode=review_view_mode,
        )

    prioritized = str(prioritize_proposal_id or "").strip()
    prioritized_ref = _parse_proposal_choice_value(prioritized)
    if prioritized_ref is not None:
        prioritized = _proposal_choice_value(
            prioritized_ref[0],
            prioritized_ref[1],
            source_proposal_type=prioritized_ref[2],
        )
    if prioritized in by_id:
        pinned_choice = next((choice for choice in choices if choice[1] == prioritized), None)
        if pinned_choice is not None:
            choices = [pinned_choice, *(choice for choice in choices if choice[1] != prioritized)]

    selected = str(selected_proposal_id or "").strip()
    selected_ref = _parse_proposal_choice_value(selected)
    if selected_ref is not None:
        selected = _proposal_choice_value(
            selected_ref[0],
            selected_ref[1],
            source_proposal_type=selected_ref[2],
        )
    if selected not in by_id:
        selected = choices[0][1]
    selected_summary = by_id[selected]
    selected_ref = _parse_proposal_choice_value(selected)
    selected_proposal = (
        _fetch_proposal_by_id(
            selected_ref[1],
            proposal_source=selected_ref[0],
            source_proposal_type=selected_ref[2],
        )
        if selected_ref is not None
        else None
    )
    selected_dataset_entry = str(selected_summary.get("dataset_entry") or "").strip()
    (
        admin_meta,
        images_html,
        compiled_base_update,
        compiled_current_update,
        compiled_proposed_update,
        raw_base_update,
        raw_current_update,
        raw_proposed_update,
        diff_update,
        selected_scope,
        change_groups_json,
    ) = _build_selected_proposal_panel_content(
        selected_proposal,
        selected_dataset_entry,
        review_view_mode=review_view_mode,
    )
    return (
        gr.update(choices=choices, value=selected),
        admin_meta,
        images_html,
        compiled_base_update,
        compiled_current_update,
        compiled_proposed_update,
        raw_base_update,
        raw_current_update,
        raw_proposed_update,
        diff_update,
        selected_scope,
        change_groups_json,
    )


def _next_pending_proposal_for_slug(
    person_slug: str,
    slug_filter: str = "",
    exclude_proposal_id: int | None = None,
    proposal_source: str | None = None,
    source_proposal_type: str | None = None,
) -> str | None:
    normalized_slug = str(person_slug or "").strip().lower()
    if not normalized_slug:
        return None

    source_filter = (
        _normalize_proposal_source(proposal_source)
        if str(proposal_source or "").strip()
        else ""
    )
    source_type_filter = (
        _normalize_source_proposal_type(source_proposal_type)
        if source_filter == PROPOSAL_SOURCE_SOURCE and str(source_proposal_type or "").strip()
        else ""
    )
    excluded = str(int(exclude_proposal_id)) if exclude_proposal_id is not None else ""
    proposals = _fetch_change_proposals(slug_filter=slug_filter)
    for proposal in proposals:
        current_source = _normalize_proposal_source(proposal.get("proposal_source"))
        if source_filter and current_source != source_filter:
            continue
        current_type = (
            _normalize_source_proposal_type(proposal.get("source_proposal_type"))
            if current_source == PROPOSAL_SOURCE_SOURCE
            else ""
        )
        if source_type_filter and current_type != source_type_filter:
            continue
        proposal_slug = str(proposal.get("person_slug") or "").strip().lower()
        proposal_status = str(proposal.get("status") or "").strip().lower()
        proposal_id = str(proposal.get("id") or "").strip()
        if proposal_slug != normalized_slug or proposal_status != "pending":
            continue
        if excluded and proposal_id == excluded:
            continue
        if proposal_id.isdigit():
            return _proposal_choice_value(
                current_source,
                proposal_id,
                source_proposal_type=current_type,
            )
    return None


def _header_people(request: gr.Request):
    return render_header(path="/recetas", request=request)


def _header_people_review(request: gr.Request):
    return render_header(path="/review", request=request)


def _render_review_link_button(slug: str) -> str:
    href = f"/review/?slug={quote((slug or '').strip().lower(), safe='-')}"
    return (
        f"<a class='the-list-review-btn' href='{href}' title='Revisión administrativa' aria-label='Revisión administrativa'>"
        f"<img src='{REVIEW_BUTTON_ICON_SRC}' alt='' aria-hidden='true' loading='lazy'/>"
        "</a>"
    )


def _build_review_slug_filter_update(
    selected_slug: str = "",
    proposals: Sequence[Dict[str, object]] | None = None,
) -> tuple[gr.update, str]:
    normalized_slug = (selected_slug or "").strip().lower()
    choices: List[Tuple[str, str]] = [("Todos los slugs", "")]
    known_slugs = {""}
    source_rows = list(proposals) if proposals is not None else _fetch_change_proposals(limit=2000)
    for proposal in source_rows:
        slug = str(proposal.get("person_slug") or "").strip().lower()
        if not slug or slug in known_slugs:
            continue
        choices.append((slug, slug))
        known_slugs.add(slug)

    if normalized_slug and normalized_slug not in known_slugs:
        choices.append((f"{normalized_slug} (slug no encontrado)", normalized_slug))
        known_slugs.add(normalized_slug)

    selected_value = normalized_slug if normalized_slug in known_slugs else ""
    return gr.update(choices=choices, value=selected_value, interactive=True), selected_value


def _review_summary(slug_filter: str) -> str:
    normalized_slug = (slug_filter or "").strip().lower()
    if not normalized_slug:
        return "Revisando propuestas en seguimiento para todos los slugs."
    return f"Revisando propuestas en seguimiento para el slug `{normalized_slug}`."


def _build_proposal_help_messages(user_name: str, user_email: str, can_submit: bool) -> tuple[str, str]:
    if not can_submit:
        disabled_message = (
            "Tu privilegio `base_user` está desactivado. Contacta a un creador si se eliminó por error."
        )
        return disabled_message, disabled_message

    signed_in = (
        f"Sesión iniciada como **{html.escape(user_name)}** (`{html.escape(user_email or 'desconocido')}`). "
    )
    return (
        signed_in + "Envía una propuesta de artículo y los revisores evaluarán el diff en seguimiento.",
        signed_in + "Envía una propuesta de tarjeta y los revisores evaluarán el diff en seguimiento.",
    )


def _load_people_page(request: gr.Request):
    try:
        user, is_admin, can_submit = _role_flags_from_request(request)
        user_name = str(user.get("name") or user.get("email") or "Usuario")
        user_email = str(user.get("email") or "").strip().lower()
        markdown_help, card_help = _build_proposal_help_messages(user_name, user_email, can_submit)
        empty_filter_choices = [(TAG_FILTER_ALL_OPTION, TAG_FILTER_ALL_OPTION)]
        empty_filter_update = gr.update(choices=empty_filter_choices, value=[], interactive=True)

        slug = _query_param(request, "slug").lower()
        if slug:
            person = _fetch_person(slug)
            if person is not None:
                markdown_value = str(person.get("markdown") or "")
                name_value = str(person.get("name") or "")
                bucket_value = str(person.get("title") or person.get("bucket") or "")
                tags_value = _tags_to_text(person.get("tags", []))
                return (
                    f"<h2>{html.escape(name_value or 'Receta')}</h2>",
                    gr.update(visible=False),
                    empty_filter_update,
                    [],
                    gr.update(value="", visible=False),
                    gr.update(value=_render_person_hero(person), visible=True),
                    gr.update(value=markdown_value, visible=True),
                    slug,
                    markdown_value,
                    name_value,
                    bucket_value,
                    tags_value,
                    False,
                    gr.update(value=EDIT_TOGGLE_BUTTON_LABEL, visible=can_submit),
                    False,
                    gr.update(value=EDIT_TOGGLE_BUTTON_LABEL, visible=can_submit),
                    gr.update(value=_render_review_link_button(slug), visible=is_admin),
                    gr.update(visible=False),
                    markdown_help,
                    markdown_value,
                    "",
                    gr.update(visible=False),
                    card_help,
                    name_value,
                    bucket_value,
                    tags_value,
                    "",
                )
            return (
                "<h2>Recetas</h2>",
                gr.update(visible=False),
                empty_filter_update,
                [],
                gr.update(value="", visible=False),
                gr.update(value=_render_missing_person(slug), visible=True),
                gr.update(value="", visible=False),
                "",
                "",
                "",
                "",
                "",
                False,
                gr.update(value=EDIT_TOGGLE_BUTTON_LABEL, visible=False),
                False,
                gr.update(value=EDIT_TOGGLE_BUTTON_LABEL, visible=False),
                gr.update(value="", visible=False),
                gr.update(visible=False),
                "",
                "",
                "",
                gr.update(visible=False),
                "",
                "",
                "",
                "",
                "",
            )

        people = _fetch_all_people()
        selected_tags = _parse_tag_query_values(_query_param(request, "tag"))
        tag_filter_update, _tag_filter_choices, _tag_filter_selection = _build_tag_filter_update(
            people,
            selected_tags,
            default_to_all=True,
        )
        cards_html = _render_cards(_filter_people_for_tag_selection(people, _tag_filter_selection))
        return (
            "<h2>Recetas</h2>",
            gr.update(visible=True),
            tag_filter_update,
            _tag_filter_selection,
            gr.update(value=cards_html, visible=True),
            gr.update(value="", visible=False),
            gr.update(value="", visible=False),
            "",
            "",
            "",
            "",
            "",
            False,
            gr.update(value=EDIT_TOGGLE_BUTTON_LABEL, visible=False),
            False,
            gr.update(value=EDIT_TOGGLE_BUTTON_LABEL, visible=False),
            gr.update(value="", visible=False),
            gr.update(visible=False),
            "",
            "",
            "",
            gr.update(visible=False),
            "",
            "",
            "",
            "",
            "",
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to load people page: %s", exc)
        return (
            "<h2>Recetas</h2>",
            gr.update(visible=False),
            gr.update(choices=[(TAG_FILTER_ALL_OPTION, TAG_FILTER_ALL_OPTION)], value=[], interactive=True),
            [],
            gr.update(value="", visible=False),
            gr.update(value=_render_missing_person("load-error"), visible=True),
            gr.update(value="", visible=False),
            "",
            "",
            "",
            "",
            "",
            False,
            gr.update(value=EDIT_TOGGLE_BUTTON_LABEL, visible=False),
            False,
            gr.update(value=EDIT_TOGGLE_BUTTON_LABEL, visible=False),
            gr.update(value="", visible=False),
            gr.update(visible=False),
            "",
            "",
            "",
            gr.update(visible=False),
            "",
            "",
            "",
            "",
            "",
        )


def _load_people_review_page(request: gr.Request, review_view_mode: str = DEFAULT_REVIEW_VIEW):
    user, is_admin, _ = _role_flags_from_request(request, refresh_if_not_admin=True)
    slug_filter = (_query_param(request, "slug") or "").strip().lower()
    selected_proposal_id = (_query_param(request, "proposal_id") or "").strip()

    if not user or not is_admin:
        logger.warning(
            "review_display.load_admin_page.access_denied has_user=%s email=%s privileges=%s",
            bool(user),
            str(user.get("email") or ""),
            user.get("privileges") if isinstance(user, dict) else {},
        )
        (
            admin_meta,
            admin_images,
            admin_compiled_base,
            admin_compiled_current,
            admin_compiled_proposed,
            admin_raw_base,
            admin_raw_current,
            admin_raw_proposed,
            admin_diff,
            admin_scope,
            admin_change_groups,
        ) = _empty_admin_review_panels("Se requieren permisos de revisor.", review_view_mode=review_view_mode)
        return (
            "## Revisión de Recetas",
            "Se requieren permisos de revisor para abrir el panel de revisión.",
            gr.update(choices=[], value=None, interactive=False),
            slug_filter,
            gr.update(choices=[], value=None),
            admin_meta,
            admin_images,
            admin_compiled_base,
            admin_compiled_current,
            admin_compiled_proposed,
            admin_raw_base,
            admin_raw_current,
            admin_raw_proposed,
            admin_diff,
            admin_scope,
            admin_change_groups,
            "❌ Se requieren permisos de revisor.",
        )

    all_proposals = _fetch_change_proposals(limit=2000)
    slug_filter_update, normalized_slug_filter = _build_review_slug_filter_update(
        slug_filter,
        proposals=all_proposals,
    )
    panel_proposals = all_proposals
    if normalized_slug_filter:
        panel_proposals = [
            proposal
            for proposal in all_proposals
            if str(proposal.get("person_slug") or "").strip().lower() == normalized_slug_filter
        ]
    (
        selector_update,
        admin_meta,
        admin_images,
        admin_compiled_base,
        admin_compiled_current,
        admin_compiled_proposed,
        admin_raw_base,
        admin_raw_current,
        admin_raw_proposed,
        admin_diff,
        admin_scope,
        admin_change_groups,
    ) = _build_admin_panel(
        selected_proposal_id,
        normalized_slug_filter,
        proposals=panel_proposals,
        review_view_mode=review_view_mode,
    )
    return (
        "## Revisión de Recetas",
        _review_summary(normalized_slug_filter),
        slug_filter_update,
        normalized_slug_filter,
        selector_update,
        admin_meta,
        admin_images,
        admin_compiled_base,
        admin_compiled_current,
        admin_compiled_proposed,
        admin_raw_base,
        admin_raw_current,
        admin_raw_proposed,
        admin_diff,
        admin_scope,
        admin_change_groups,
        "",
    )


def _empty_admin_review_panels(
    reason: str,
    review_view_mode: str = DEFAULT_REVIEW_VIEW,
) -> tuple[str, str, gr.update, gr.update, gr.update, gr.update, gr.update, gr.update, gr.update, str, str]:
    message = reason or "No hay ninguna propuesta seleccionada."
    empty_compiled_base = _render_review_markdown_panel("Estado base (compilado)", "")
    empty_compiled_current = _render_review_markdown_panel("Estado actual (compilado)", "")
    empty_compiled_proposed = _render_review_markdown_panel("Estado propuesto (compilado)", "")
    (
        compiled_base_update,
        compiled_current_update,
        compiled_proposed_update,
    ) = _build_admin_display_panel_updates(
        review_view_mode,
        PROPOSAL_SCOPE_ARTICLE,
        empty_compiled_base,
        empty_compiled_current,
        empty_compiled_proposed,
        "",
        "",
        "",
        collapse_base_current=False,
    )
    (
        raw_base_update,
        raw_current_update,
        raw_proposed_update,
        diff_update,
    ) = _build_admin_raw_panel_updates(
        "",
        "",
        "",
        _render_empty_diff(message),
        scope=PROPOSAL_SCOPE_ARTICLE,
    )
    return (
        message,
        _render_empty_diff(message),
        compiled_base_update,
        compiled_current_update,
        compiled_proposed_update,
        raw_base_update,
        raw_current_update,
        raw_proposed_update,
        diff_update,
        "",
        "[]",
    )


def _toggle_admin_review_view_mode(
    review_view_mode: str,
    scope_value: str,
    raw_base_value: str,
    raw_current_value: str,
    raw_proposed_value: str,
):
    scope = _normalize_proposal_scope(scope_value)
    raw_base = str(raw_base_value or "")
    raw_current = str(raw_current_value or "")
    raw_proposed = str(raw_proposed_value or "")
    collapse_base_current = raw_base == raw_current

    compiled_base = _render_review_markdown_panel(
        "Estado base (=estado actual) (compilado)" if collapse_base_current else "Estado base (compilado)",
        "",
    )
    compiled_current = _render_review_markdown_panel("Estado actual (compilado)", "")
    compiled_proposed = _render_review_markdown_panel("Estado propuesto (compilado)", "")

    if raw_base.strip() or raw_current.strip() or raw_proposed.strip():
        (
            compiled_base,
            compiled_current,
            compiled_proposed,
            _change_groups,
            collapse_base_current,
        ) = _render_grouped_current_proposed_panels(
            scope,
            raw_base,
            raw_current,
            raw_proposed,
        )

    return _build_admin_display_panel_updates(
        review_view_mode,
        scope,
        compiled_base,
        compiled_current,
        compiled_proposed,
        raw_base,
        raw_current,
        raw_proposed,
        collapse_base_current=collapse_base_current,
    )


def _toggle_proposal_markdown_view(view_mode: str, proposal_markdown: str):
    normalized_mode = (view_mode or DEFAULT_MARKDOWN_VIEW).strip().lower()
    is_preview = normalized_mode in {
        str(MARKDOWN_VIEW_PREVIEW).strip().lower(),
        "compiled",
        "preview",
    }
    preview_value = _render_citation_compiled_markdown(proposal_markdown or "") if is_preview else (proposal_markdown or "")
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
            gr.update(value=_render_citation_compiled_markdown(current_markdown or ""), visible=True),
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
            gr.update(value=_render_citation_compiled_markdown(current_markdown or ""), visible=True),
            gr.update(value=DEFAULT_MARKDOWN_VIEW),
            gr.update(value=EDIT_TOGGLE_BUTTON_LABEL, visible=True),
            "",
        )

    return (
        False,
        gr.update(visible=True),
        gr.update(visible=False),
        gr.update(value=current_markdown or "", visible=False),
        gr.update(value=_render_citation_compiled_markdown(current_markdown or ""), visible=True),
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
        gr.update(value=_render_citation_compiled_markdown(current_markdown or ""), visible=True),
        gr.update(value=DEFAULT_MARKDOWN_VIEW),
        gr.update(value=EDIT_TOGGLE_BUTTON_LABEL, visible=True),
        "",
        "",
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
    )


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
            return _response("❌ Debes iniciar sesión para enviar una propuesta.", proposal_note)
        if not can_submit:
            return _response(
                "❌ Tu privilegio `base_user` está deshabilitado. Pide a un creador que restaure el acceso.",
                proposal_note,
            )

        slug = (current_slug or "").strip().lower()
        if not slug:
            return _response("❌ Abre un perfil antes de enviar una propuesta.", proposal_note)

        person = _fetch_person(slug)
        if person is None:
            return _response("❌ No se encontró el perfil.", proposal_note)
        person_id = int(person.get("person_id") or 0)
        if person_id <= 0:
            return _response("❌ No se pudo resolver el id del perfil.", proposal_note)

        proposed_markdown = (proposal_markdown or "").strip()
        if not proposed_markdown:
            return _response("❌ El markdown propuesto no puede estar vacío.", proposal_note)
        if len(proposed_markdown) > 60000:
            return _response("❌ El markdown propuesto es demasiado grande (máx. 60,000 caracteres).", proposal_note)

        actor_user_id = _resolve_request_user_id(user)
        if actor_user_id <= 0:
            return _response("❌ No se pudo resolver tu id de usuario.", proposal_note)

        note_value = (proposal_note or "").strip()
        base_markdown = str(person.get("markdown") or "")
        if proposed_markdown == base_markdown:
            return _response("❌ No se detectaron cambios en el artículo.", proposal_note)

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

        return _response(
            f"✅ Propuesta de artículo #{proposal_id} enviada. Ahora está registrada y pendiente de revisión.",
            "",
            close_editor=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to submit change proposal: %s", exc)
        return _response(f"❌ No se pudo enviar la propuesta: {exc}", proposal_note)


def _submit_card_proposal(
    current_slug: str,
    proposal_note: str,
    proposal_name: str,
    proposal_bucket: str,
    proposal_tags: str,
    proposal_image: object,
    current_name: str,
    current_bucket: str,
    current_tags: str,
    edit_mode: bool,
    request: gr.Request,
):
    def _response(message: str, next_note: str, image_update, close_editor: bool = False):
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
            ) = _cancel_card_editor(current_name, current_bucket, current_tags)
            return (
                message,
                next_note,
                image_update,
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
            return _response("❌ Debes iniciar sesión para enviar una propuesta.", proposal_note, gr.update(value=None))
        if not can_submit:
            return _response(
                "❌ Tu privilegio `base_user` está deshabilitado. Pide a un creador que restaure el acceso.",
                proposal_note,
                gr.update(value=None),
            )

        slug = (current_slug or "").strip().lower()
        if not slug:
            return _response(
                "❌ Abre un perfil antes de enviar una propuesta.",
                proposal_note,
                gr.update(value=None),
            )

        person = _fetch_person(slug)
        if person is None:
            return _response("❌ No se encontró el perfil.", proposal_note, gr.update(value=None))
        person_id = int(person.get("person_id") or 0)
        if person_id <= 0:
            return _response("❌ No se pudo resolver el id del perfil.", proposal_note, gr.update(value=None))

        actor_user_id = _resolve_request_user_id(user)
        if actor_user_id <= 0:
            return _response("❌ No se pudo resolver tu id de usuario.", proposal_note, gr.update(value=None))
        actor_email = (user.get("email") or "").strip().lower()
        actor_storage_identity = actor_email or f"user-{actor_user_id}"

        proposed_name = str(proposal_name or "").strip()
        proposed_title = str(proposal_bucket or "").strip()
        proposed_tags = _parse_tags_input(proposal_tags)
        if not proposed_name:
            return _response("❌ El nombre de la tarjeta no puede estar vacío.", proposal_note, gr.update(value=None))
        if not proposed_title:
            return _response("❌ El título de la tarjeta no puede estar vacío.", proposal_note, gr.update(value=None))
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
        base_image_url = str(person.get("image_url") or "")
        proposed_image_url = base_image_url
        if uploaded_path:
            proposed_image_url = _persist_uploaded_image(uploaded_path, slug, actor_storage_identity)
        proposed_snapshot["image_url"] = proposed_image_url

        if (
            _serialize_card_snapshot(base_snapshot) == _serialize_card_snapshot(proposed_snapshot)
            and base_image_url == proposed_image_url
        ):
            return _response("❌ No se detectaron cambios en la tarjeta.", proposal_note, gr.update(value=None))

        note_value = (proposal_note or "").strip()
        base_payload = _serialize_card_snapshot(base_snapshot)
        proposed_payload = _serialize_card_snapshot(proposed_snapshot)

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
            upsert_people_diff_payload(
                session,
                proposal_id=proposal_id,
                person_id=person_id,
                scope=PROPOSAL_SCOPE_CARD,
                base_payload=base_payload,
                proposed_payload=proposed_payload,
                base_image_url=base_image_url,
                proposed_image_url=proposed_image_url,
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

        return _response(
            f"✅ Propuesta de tarjeta #{proposal_id} enviada. Ahora está registrada y pendiente de revisión.",
            "",
            gr.update(value=None),
            close_editor=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to submit card proposal: %s", exc)
        return _response(f"❌ No se pudo enviar la propuesta: {exc}", proposal_note, gr.update(value=None))


def _change_admin_slug_filter(slug_filter: str, review_view_mode: str, request: gr.Request):
    user, is_admin, _ = _role_flags_from_request(request, refresh_if_not_admin=True)
    normalized_slug_filter = (slug_filter or "").strip().lower()
    if not user or not is_admin:
        (
            admin_meta,
            admin_images,
            admin_compiled_base,
            admin_compiled_current,
            admin_compiled_proposed,
            admin_raw_base,
            admin_raw_current,
            admin_raw_proposed,
            admin_diff,
            admin_scope,
            admin_change_groups,
        ) = _empty_admin_review_panels("Se requieren permisos de revisor.", review_view_mode=review_view_mode)
        return (
            "",
            "Se requieren permisos de revisor.",
            gr.update(choices=[], value=None),
            admin_meta,
            admin_images,
            admin_compiled_base,
            admin_compiled_current,
            admin_compiled_proposed,
            admin_raw_base,
            admin_raw_current,
            admin_raw_proposed,
            admin_diff,
            admin_scope,
            admin_change_groups,
            "❌ Se requieren permisos de revisor.",
        )
    (
        selector_update,
        admin_meta,
        admin_images,
        admin_compiled_base,
        admin_compiled_current,
        admin_compiled_proposed,
        admin_raw_base,
        admin_raw_current,
        admin_raw_proposed,
        admin_diff,
        admin_scope,
        admin_change_groups,
    ) = _build_admin_panel(slug_filter=normalized_slug_filter, review_view_mode=review_view_mode)
    return (
        normalized_slug_filter,
        _review_summary(normalized_slug_filter),
        selector_update,
        admin_meta,
        admin_images,
        admin_compiled_base,
        admin_compiled_current,
        admin_compiled_proposed,
        admin_raw_base,
        admin_raw_current,
        admin_raw_proposed,
        admin_diff,
        admin_scope,
        admin_change_groups,
        "",
    )


def _refresh_admin_panel(slug_filter: str, review_view_mode: str, request: gr.Request):
    user, is_admin, _ = _role_flags_from_request(request, refresh_if_not_admin=True)
    if not user or not is_admin:
        (
            admin_meta,
            admin_images,
            admin_compiled_base,
            admin_compiled_current,
            admin_compiled_proposed,
            admin_raw_base,
            admin_raw_current,
            admin_raw_proposed,
            admin_diff,
            admin_scope,
            admin_change_groups,
        ) = _empty_admin_review_panels("Se requieren permisos de revisor.", review_view_mode=review_view_mode)
        return (
            gr.update(choices=[], value=None),
            admin_meta,
            admin_images,
            admin_compiled_base,
            admin_compiled_current,
            admin_compiled_proposed,
            admin_raw_base,
            admin_raw_current,
            admin_raw_proposed,
            admin_diff,
            admin_scope,
            admin_change_groups,
            "❌ Se requieren permisos de revisor.",
        )
    (
        selector_update,
        admin_meta,
        admin_images,
        admin_compiled_base,
        admin_compiled_current,
        admin_compiled_proposed,
        admin_raw_base,
        admin_raw_current,
        admin_raw_proposed,
        admin_diff,
        admin_scope,
        admin_change_groups,
    ) = _build_admin_panel(slug_filter=slug_filter, review_view_mode=review_view_mode)
    return (
        selector_update,
        admin_meta,
        admin_images,
        admin_compiled_base,
        admin_compiled_current,
        admin_compiled_proposed,
        admin_raw_base,
        admin_raw_current,
        admin_raw_proposed,
        admin_diff,
        admin_scope,
        admin_change_groups,
        "",
    )


def _select_admin_proposal(proposal_id: str, slug_filter: str, review_view_mode: str, request: gr.Request):
    user, is_admin, _ = _role_flags_from_request(request, refresh_if_not_admin=True)
    if not user or not is_admin:
        (
            admin_meta,
            admin_images,
            admin_compiled_base,
            admin_compiled_current,
            admin_compiled_proposed,
            admin_raw_base,
            admin_raw_current,
            admin_raw_proposed,
            admin_diff,
            admin_scope,
            admin_change_groups,
        ) = _empty_admin_review_panels("Se requieren permisos de revisor.", review_view_mode=review_view_mode)
        return (
            admin_meta,
            admin_images,
            admin_compiled_base,
            admin_compiled_current,
            admin_compiled_proposed,
            admin_raw_base,
            admin_raw_current,
            admin_raw_proposed,
            admin_diff,
            admin_scope,
            admin_change_groups,
            "❌ Se requieren permisos de revisor.",
        )

    selected = str(proposal_id or "").strip()
    selected_ref = _parse_proposal_choice_value(selected)
    selected_proposal = (
        _fetch_proposal_by_id(
            selected_ref[1],
            proposal_source=selected_ref[0],
            source_proposal_type=selected_ref[2],
        )
        if selected_ref is not None
        else None
    )
    (
        admin_meta,
        admin_images,
        admin_compiled_base,
        admin_compiled_current,
        admin_compiled_proposed,
        admin_raw_base,
        admin_raw_current,
        admin_raw_proposed,
        admin_diff,
        admin_scope,
        admin_change_groups,
    ) = _build_selected_proposal_panel_content(
        selected_proposal,
        "",
        review_view_mode=review_view_mode,
    )
    return (
        admin_meta,
        admin_images,
        admin_compiled_base,
        admin_compiled_current,
        admin_compiled_proposed,
        admin_raw_base,
        admin_raw_current,
        admin_raw_proposed,
        admin_diff,
        admin_scope,
        admin_change_groups,
        "",
    )


def _preview_admin_proposed_edit(
    proposal_id: str,
    scope_value: str,
    base_raw_value: str,
    current_raw_value: str,
    proposed_raw_value: str,
    review_view_mode: str,
    request: gr.Request,
):
    user, is_admin, _ = _role_flags_from_request(request, refresh_if_not_admin=True)
    if not user or not is_admin:
        return gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), "❌ Se requieren permisos de revisor."

    selected = str(proposal_id or "").strip()
    selected_ref = _parse_proposal_choice_value(selected)
    scope = _normalize_proposal_scope(scope_value)
    base_raw = str(base_raw_value or "")
    current_raw = str(current_raw_value or "")
    proposed_raw = str(proposed_raw_value or "")
    base_image_url = ""
    current_image_url = ""
    proposed_image_url = ""
    proposal: Dict[str, object] | None = (
        _fetch_proposal_by_id(
            selected_ref[1],
            proposal_source=selected_ref[0],
            source_proposal_type=selected_ref[2],
        )
        if selected_ref is not None
        else None
    )
    if scope == PROPOSAL_SCOPE_CARD:
        if selected_ref is None:
            return gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), "❌ Selecciona primero una propuesta válida."
        if proposal is None:
            return gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), "❌ Propuesta no encontrada."
        candidate = proposed_raw.strip()
        if not candidate:
            return gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), "❌ El JSON de tarjeta propuesto no puede estar vacío."
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError as exc:
            return (
                gr.update(),
                gr.update(),
                gr.update(),
                gr.update(),
                gr.update(),
                f"❌ El JSON de tarjeta propuesto no es válido: {exc.msg}.",
            )
        if not isinstance(parsed, dict):
            return gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), "❌ El JSON de tarjeta propuesto debe ser un objeto."
        proposed_raw = json.dumps(
            _deserialize_card_snapshot(json.dumps(parsed, ensure_ascii=True)),
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
        )
        base_image_url = str(proposal.get("base_image_url") or "").strip()
        submitted_proposed_image_url = str(proposal.get("proposed_image_url") or "").strip() or base_image_url
        current_image_url = str(proposal.get("current_image_url") or "").strip() or base_image_url
        proposed_image_url, _ = _merge_three_way_scalar(
            base_image_url,
            current_image_url,
            submitted_proposed_image_url,
        )

    elif scope == PROPOSAL_SCOPE_CARD_ARTICLE:
        if selected_ref is None:
            return gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), "❌ Selecciona primero una propuesta válida."
        if proposal is None:
            return gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), "❌ Propuesta no encontrada."
        candidate = proposed_raw.strip()
        if not candidate:
            return gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), "❌ El JSON propuesto de tarjeta+artículo no puede estar vacío."
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError as exc:
            return (
                gr.update(),
                gr.update(),
                gr.update(),
                gr.update(),
                gr.update(),
                f"❌ El JSON propuesto de tarjeta+artículo no es válido: {exc.msg}.",
            )
        if not isinstance(parsed, dict):
            return gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), "❌ El JSON propuesto de tarjeta+artículo debe ser un objeto."
        proposed_raw = json.dumps(
            _deserialize_card_article_snapshot(json.dumps(parsed, ensure_ascii=True)),
            ensure_ascii=True,
        )
        base_image_url = str(proposal.get("base_image_url") or "").strip()
        submitted_proposed_image_url = str(proposal.get("proposed_image_url") or "").strip() or base_image_url
        current_image_url = str(proposal.get("current_image_url") or "").strip() or base_image_url
        proposed_image_url, _ = _merge_three_way_scalar(
            base_image_url,
            current_image_url,
            submitted_proposed_image_url,
        )

    (
        compiled_base,
        compiled_current,
        compiled_proposed,
        change_groups,
        collapse_base_current,
    ) = _render_grouped_current_proposed_panels(
        scope,
        base_raw,
        current_raw,
        proposed_raw,
        base_image_url=base_image_url,
        current_image_url=current_image_url,
        proposed_image_url=proposed_image_url,
        proposal=proposal,
    )
    (
        compiled_base_update,
        compiled_current_update,
        compiled_proposed_update,
    ) = _build_admin_display_panel_updates(
        review_view_mode,
        scope,
        compiled_base,
        compiled_current,
        compiled_proposed,
        base_raw,
        current_raw,
        proposed_raw,
        collapse_base_current=collapse_base_current,
    )
    render_as_snapshot = _should_render_creation_snapshot(proposal, scope, base_raw, current_raw)
    diff_update = _build_admin_raw_panel_updates(
        "",
        "",
        proposed_raw,
        _render_proposal_diff_table(
            current_raw,
            proposed_raw,
            scope,
            current_image_url=current_image_url,
            proposed_image_url=proposed_image_url,
            render_as_snapshot=render_as_snapshot,
        ),
        scope=scope,
    )[3]
    return (
        compiled_base_update,
        compiled_current_update,
        compiled_proposed_update,
        diff_update,
        json.dumps(change_groups, ensure_ascii=True),
        "",
    )


def _apply_review_change_choice(
    action_json: str,
    scope_value: str,
    change_groups_json: str,
    base_raw_value: str,
    current_raw_value: str,
    proposed_raw_value: str,
    review_view_mode: str,
    request: gr.Request,
):
    user, is_admin, _ = _role_flags_from_request(request, refresh_if_not_admin=True)
    if not user or not is_admin:
        return (
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            "❌ Se requieren permisos de revisor.",
        )

    scope = _normalize_proposal_scope(scope_value)
    if scope != PROPOSAL_SCOPE_ARTICLE:
        return (
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            "⚠️ El selector por grupos solo está disponible para propuestas de artículo.",
        )

    try:
        action = json.loads(str(action_json or "{}"))
    except json.JSONDecodeError:
        return (
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            "❌ Carga de acción de cambio no válida.",
        )
    source = str((action or {}).get("source") or "").strip().lower()
    group_id = int((action or {}).get("change_id") or 0)
    if source not in {"base", "current", "proposed"} or group_id <= 0:
        return (
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            "",
        )

    try:
        groups = json.loads(str(change_groups_json or "[]"))
    except json.JSONDecodeError:
        groups = []
    if not isinstance(groups, list):
        groups = []
    selected_group = next(
        (
            item
            for item in groups
            if isinstance(item, dict) and int(item.get("id") or 0) == group_id
        ),
        None,
    )
    if selected_group is None:
        return (
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            f"⚠️ El grupo de cambios #{group_id} ya no está disponible.",
        )

    base_lines = str(base_raw_value or "").splitlines()
    current_lines = str(current_raw_value or "").splitlines()
    proposed_lines = str(proposed_raw_value or "").splitlines()

    def _safe_range(start_key: str, end_key: str, total: int) -> tuple[int, int]:
        start = max(0, min(int(selected_group.get(start_key) or 0), total))
        end = max(start, min(int(selected_group.get(end_key) or start), total))
        return start, end

    proposed_start, proposed_end = _safe_range("proposed_start", "proposed_end", len(proposed_lines))
    current_start, current_end = _safe_range("current_start", "current_end", len(current_lines))
    base_start, base_end = _safe_range("base_start", "base_end", len(base_lines))

    if source == "base":
        replacement = base_lines[base_start:base_end]
    elif source == "current":
        replacement = current_lines[current_start:current_end]
    else:
        replacement = proposed_lines[proposed_start:proposed_end]

    next_proposed_lines = [*proposed_lines[:proposed_start], *replacement, *proposed_lines[proposed_end:]]
    next_proposed_raw = "\n".join(next_proposed_lines)

    (
        compiled_base,
        compiled_current,
        compiled_proposed,
        next_groups,
        collapse_base_current,
    ) = _render_grouped_current_proposed_panels(
        scope,
        str(base_raw_value or ""),
        str(current_raw_value or ""),
        next_proposed_raw,
    )
    (
        compiled_base_update,
        compiled_current_update,
        compiled_proposed_update,
    ) = _build_admin_display_panel_updates(
        review_view_mode,
        scope,
        compiled_base,
        compiled_current,
        compiled_proposed,
        str(base_raw_value or ""),
        str(current_raw_value or ""),
        next_proposed_raw,
        collapse_base_current=collapse_base_current,
    )
    render_as_snapshot = _should_render_creation_snapshot(
        None,
        scope,
        str(base_raw_value or ""),
        str(current_raw_value or ""),
    )
    diff_update = _build_admin_raw_panel_updates(
        "",
        "",
        next_proposed_raw,
        _render_proposal_diff_table(
            str(current_raw_value or ""),
            next_proposed_raw,
            scope,
            render_as_snapshot=render_as_snapshot,
        ),
        scope=scope,
    )[3]
    status = f"✅ Se aplicó el grupo de cambios #{group_id} desde `{source}`."
    return (
        next_proposed_raw,
        compiled_base_update,
        compiled_current_update,
        compiled_proposed_update,
        diff_update,
        json.dumps(next_groups, ensure_ascii=True),
        status,
    )


def _accept_admin_proposal(
    proposal_id: str,
    slug_filter: str,
    reviewed_proposed_raw: str,
    review_view_mode: str,
    request: gr.Request,
):
    user, is_admin, _ = _role_flags_from_request(request, refresh_if_not_admin=True)
    if not user or not is_admin:
        panel = _build_admin_panel(slug_filter=slug_filter, review_view_mode=review_view_mode)
        return ("❌ Se requieren permisos de revisor.", *panel)

    selected = str(proposal_id or "").strip()
    selected_ref = _parse_proposal_choice_value(selected)
    if selected_ref is None:
        panel = _build_admin_panel(slug_filter=slug_filter, review_view_mode=review_view_mode)
        return ("❌ Selecciona primero una propuesta válida.", *panel)

    proposal_source, proposal_id_int, source_proposal_type = selected_ref
    proposal = _fetch_proposal_by_id(
        proposal_id_int,
        proposal_source=proposal_source,
        source_proposal_type=source_proposal_type,
    )
    if proposal is None:
        panel = _build_admin_panel(slug_filter=slug_filter, review_view_mode=review_view_mode)
        return ("❌ Propuesta no encontrada.", *panel)

    status = str(proposal.get("status") or "").strip().lower()
    if status != "pending":
        panel = _build_admin_panel(selected, slug_filter=slug_filter, review_view_mode=review_view_mode)
        return (f"⚠️ Propuesta #{proposal_id_int} ya está `{status or 'desconocido'}`.", *panel)

    if proposal_source == PROPOSAL_SOURCE_SOURCE:
        normalized_source_type = _normalize_source_proposal_type(
            proposal.get("source_proposal_type") or source_proposal_type
        )
        admin_user_id = _resolve_request_user_id(user)
        if admin_user_id <= 0:
            panel = _build_admin_panel(selected, slug_filter=slug_filter, review_view_mode=review_view_mode)
            return ("❌ No se pudo resolver el id del usuario revisor.", *panel)

        _ensure_local_db()
        try:
            with session_scope() as session:
                if normalized_source_type == SOURCE_PROPOSAL_TYPE_TAGS:
                    source_slug = _accept_source_tags_proposal(
                        session,
                        proposal=proposal,
                        reviewer_user_id=admin_user_id,
                    )
                else:
                    source_slug = _accept_source_push_proposal(
                        session,
                        proposal=proposal,
                        reviewer_user_id=admin_user_id,
                    )
        except Exception as exc:  # noqa: BLE001
            panel = _build_admin_panel(selected, slug_filter=slug_filter, review_view_mode=review_view_mode)
            return (f"❌ No se puede aceptar la propuesta de fuente #{proposal_id_int}: {exc}", *panel)

        next_pending_for_source = _next_pending_proposal_for_slug(
            source_slug,
            slug_filter=slug_filter,
            exclude_proposal_id=proposal_id_int,
            proposal_source=proposal_source,
            source_proposal_type=normalized_source_type,
        )
        panel = _build_admin_panel(
            next_pending_for_source,
            slug_filter=slug_filter,
            prioritize_proposal_id=next_pending_for_source,
            review_view_mode=review_view_mode,
        )
        if normalized_source_type == SOURCE_PROPOSAL_TYPE_TAGS:
            file_id = int(proposal.get("unsorted_file_id") or 0)
            return (
                f"✅ Propuesta de etiquetas de fuente #{proposal_id_int} aceptada para archivo sin clasificar `#{file_id}`.",
                *panel,
            )
        return (
            f"✅ Propuesta de fuente #{proposal_id_int} aceptada y archivo movido a la fuente `{source_slug}`.",
            *panel,
        )

    person_slug = str(proposal.get("person_slug") or "").strip().lower()
    scope = _normalize_proposal_scope(proposal.get("proposal_scope"))
    person = _fetch_profile_for_source(person_slug, proposal_source)
    reviewed_value = str(reviewed_proposed_raw or "")
    if scope == PROPOSAL_SCOPE_CARD:
        reviewed_clean = reviewed_value.strip()
        if not reviewed_clean:
            panel = _build_admin_panel(selected, slug_filter=slug_filter, review_view_mode=review_view_mode)
            return ("❌ El JSON de tarjeta propuesto no puede estar vacío.", *panel)
        try:
            parsed = json.loads(reviewed_clean)
        except json.JSONDecodeError as exc:
            panel = _build_admin_panel(selected, slug_filter=slug_filter, review_view_mode=review_view_mode)
            return (f"❌ El JSON de tarjeta propuesto no es válido: {exc.msg}.", *panel)
        if not isinstance(parsed, dict):
            panel = _build_admin_panel(selected, slug_filter=slug_filter, review_view_mode=review_view_mode)
            return ("❌ El JSON de tarjeta propuesto debe ser un objeto.", *panel)
        card_fallback_snapshot = (
            _card_snapshot_from_person(person)
            if person is not None
            else _deserialize_card_snapshot(str(proposal.get("base_markdown") or ""))
        )
        proposed_markdown = _serialize_card_snapshot(
            _deserialize_card_snapshot(json.dumps(parsed, ensure_ascii=True), fallback=card_fallback_snapshot)
        )
    elif scope == PROPOSAL_SCOPE_CARD_ARTICLE:
        # For card_article, the reviewed value is the combined JSON
        reviewed_clean = reviewed_value.strip()
        if not reviewed_clean:
            panel = _build_admin_panel(selected, slug_filter=slug_filter, review_view_mode=review_view_mode)
            return ("❌ El JSON propuesto de tarjeta+artículo no puede estar vacío.", *panel)
        try:
            parsed = json.loads(reviewed_clean)
        except json.JSONDecodeError as exc:
            panel = _build_admin_panel(selected, slug_filter=slug_filter, review_view_mode=review_view_mode)
            return (f"❌ El JSON propuesto de tarjeta+artículo no es válido: {exc.msg}.", *panel)
        if not isinstance(parsed, dict):
            panel = _build_admin_panel(selected, slug_filter=slug_filter, review_view_mode=review_view_mode)
            return ("❌ El JSON propuesto de tarjeta+artículo debe ser un objeto.", *panel)
        # Keep the raw combined payload
        proposed_markdown = reviewed_clean
    else:
        proposed_markdown = reviewed_value
        if not proposed_markdown.strip():
            panel = _build_admin_panel(selected, slug_filter=slug_filter, review_view_mode=review_view_mode)
            return ("❌ El markdown propuesto no puede estar vacío.", *panel)

    base_image_url = ""
    proposed_image_url = ""
    if scope == PROPOSAL_SCOPE_CARD:
        base_image_url = str(proposal.get("base_image_url") or "").strip()
        submitted_proposed_image_url = str(proposal.get("proposed_image_url") or "").strip() or base_image_url
        current_image_url = str((person or {}).get("image_url") or "").strip() or base_image_url
        proposed_image_url, _ = _merge_three_way_scalar(
            base_image_url,
            current_image_url,
            submitted_proposed_image_url,
        )
        if not proposed_image_url:
            proposed_image_url = current_image_url or "/images/Logo.png"
    elif scope == PROPOSAL_SCOPE_CARD_ARTICLE:
        # Extract image URL from the card_article combined payload
        combined = _deserialize_card_article_snapshot(proposed_markdown)
        card_data = combined.get("card", {})
        base_image_url = str(proposal.get("base_image_url") or "").strip()
        submitted_proposed_image_url = str(card_data.get("image_url") or "").strip() or str(proposal.get("proposed_image_url") or "").strip() or base_image_url
        current_image_url = str((person or {}).get("image_url") or "").strip() or base_image_url
        proposed_image_url, _ = _merge_three_way_scalar(
            base_image_url,
            current_image_url,
            submitted_proposed_image_url,
        )
        if not proposed_image_url:
            proposed_image_url = current_image_url or "/images/Logo.png"

    if person is None:
        try:
            _materialize_missing_profile_for_proposal(
                proposal_id=proposal_id_int,
                person_slug=person_slug,
                proposal_person_id=int(proposal.get("person_id") or 0),
                scope=scope,
                proposed_payload=proposed_markdown,
                proposed_image_url=proposed_image_url,
                proposal_source=proposal_source,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to materialize missing profile for proposal #%s: %s", proposal_id_int, exc)
            panel = _build_admin_panel(selected, slug_filter=slug_filter, review_view_mode=review_view_mode)
            return (f"❌ No se puede aceptar la propuesta #{proposal_id_int}: {exc}", *panel)
        person = _fetch_profile_for_source(person_slug, proposal_source)
        if person is None:
            panel = _build_admin_panel(selected, slug_filter=slug_filter, review_view_mode=review_view_mode)
            return (f"❌ No se puede aceptar la propuesta #{proposal_id_int}: no se pudo crear la tarjeta `{person_slug}`.", *panel)

    person_id = int(person.get("person_id") or 0)
    if person_id <= 0:
        panel = _build_admin_panel(selected, slug_filter=slug_filter, review_view_mode=review_view_mode)
        return ("❌ No se pudo resolver el id del perfil para esta tarjeta.", *panel)

    proposed_payload_value = proposed_markdown
    if scope == PROPOSAL_SCOPE_CARD:
        proposed_payload_snapshot = _deserialize_card_snapshot(proposed_markdown, fallback=_card_snapshot_from_person(person))
        proposed_payload_snapshot["image_url"] = proposed_image_url
        proposed_payload_value = _serialize_card_snapshot(proposed_payload_snapshot)
    elif scope == PROPOSAL_SCOPE_CARD_ARTICLE:
        # For card_article, update the image_url in the combined payload if needed
        combined = _deserialize_card_article_snapshot(proposed_markdown)
        card_data = combined.get("card", {})
        if isinstance(card_data, dict):
            card_data["image_url"] = proposed_image_url
        # Re-serialize
        proposed_payload_value = json.dumps({
            "card": card_data,
            "article": str(combined.get("article") or ""),
        }, ensure_ascii=True, sort_keys=True, separators=(",", ":"))

    admin_user_id = _resolve_request_user_id(user)
    if admin_user_id <= 0:
        panel = _build_admin_panel(selected, slug_filter=slug_filter, review_view_mode=review_view_mode)
        return ("❌ No se pudo resolver el id del usuario revisor.", *panel)
    review_note = "Aceptado por revisor desde el panel de revisión"
    is_theory = proposal_source == PROPOSAL_SOURCE_THEORY
    people_table = "app.theories" if is_theory else "app.people"
    cards_table = "app.theory_cards" if is_theory else "app.people_cards"
    articles_table = "app.theory_articles" if is_theory else "app.people_articles"
    proposals_table = "app.theory_change_proposals" if is_theory else "app.people_change_proposals"

    _ensure_local_db()
    with session_scope() as session:
        default_title = str(person.get("title") or person.get("bucket") or "Sin asignar")

        if scope == PROPOSAL_SCOPE_CARD:
            base_snapshot = _card_snapshot_from_person(person)
            proposed_snapshot = _deserialize_card_snapshot(proposed_markdown, fallback=base_snapshot)
            proposed_name = str(proposed_snapshot.get("name") or "").strip() or str(base_snapshot.get("name") or "")
            proposed_title = str(proposed_snapshot.get("title") or proposed_snapshot.get("bucket") or "").strip() or str(
                base_snapshot.get("title") or base_snapshot.get("bucket") or ""
            )
            proposed_tags = [
                _normalize_tag(str(tag))
                for tag in proposed_snapshot.get("tags", [])
                if _normalize_tag(str(tag))
            ]
            resolved_name = proposed_name or str(person.get("name") or person_slug)
            if _normalize_name_key(resolved_name) != _normalize_name_key(str(person.get("name") or "")):
                if is_theory:
                    ensure_theory_name_available(session, resolved_name, exclude_slug=person_slug)
                else:
                    ensure_people_name_available(session, resolved_name, exclude_slug=person_slug)
            session.execute(
                text(
                    f"""
                    UPDATE {people_table}
                    SET name = :name,
                        updated_at = now()
                    WHERE id = :person_id
                    """
                ),
                {
                    "name": resolved_name,
                    "person_id": person_id,
                },
            )
            session.execute(
                text(
                    f"""
                    UPDATE {cards_table}
                    SET title_id = :title_id,
                        bucket = :bucket,
                        image_url = :image_url,
                        updated_at = now()
                    WHERE slug = :person_slug
                    """
                ),
                {
                    "title_id": (
                        ensure_theory_title(session, proposed_title or default_title)
                        if is_theory
                        else ensure_people_title(session, proposed_title or default_title)
                    ),
                    "bucket": proposed_title or default_title,
                    "image_url": proposed_image_url,
                    "person_slug": person_slug,
                },
            )
            if is_theory:
                sync_theory_card_taxonomy(
                    session,
                    person_id=person_id,
                    title=proposed_title or default_title,
                    tags=proposed_tags,
                )
            else:
                sync_people_card_taxonomy(
                    session,
                    person_id=person_id,
                    title=proposed_title or default_title,
                    tags=proposed_tags,
                )
        elif scope == PROPOSAL_SCOPE_CARD_ARTICLE:
            # Apply both card and article changes from combined payload
            combined = _deserialize_card_article_snapshot(proposed_markdown)
            card_data = combined.get("card", {})
            article_markdown_from_payload = str(combined.get("article") or "")
            
            base_snapshot = _card_snapshot_from_person(person)
            proposed_name = str(card_data.get("name") or "").strip() or str(base_snapshot.get("name") or "")
            proposed_title = str(card_data.get("title") or card_data.get("bucket") or "").strip() or str(
                base_snapshot.get("title") or base_snapshot.get("bucket") or ""
            )
            proposed_tags = [
                _normalize_tag(str(tag))
                for tag in card_data.get("tags", [])
                if _normalize_tag(str(tag))
            ]
            resolved_name = proposed_name or str(person.get("name") or person_slug)
            if _normalize_name_key(resolved_name) != _normalize_name_key(str(person.get("name") or "")):
                if is_theory:
                    ensure_theory_name_available(session, resolved_name, exclude_slug=person_slug)
                else:
                    ensure_people_name_available(session, resolved_name, exclude_slug=person_slug)
            # Update person name
            session.execute(
                text(
                    f"""
                    UPDATE {people_table}
                    SET name = :name,
                        updated_at = now()
                    WHERE id = :person_id
                    """
                ),
                {
                    "name": resolved_name,
                    "person_id": person_id,
                },
            )
            # Update card
            session.execute(
                text(
                    f"""
                    UPDATE {cards_table}
                    SET title_id = :title_id,
                        bucket = :bucket,
                        image_url = :image_url,
                        updated_at = now()
                    WHERE slug = :person_slug
                    """
                ),
                {
                    "title_id": (
                        ensure_theory_title(session, proposed_title or default_title)
                        if is_theory
                        else ensure_people_title(session, proposed_title or default_title)
                    ),
                    "bucket": proposed_title or default_title,
                    "image_url": proposed_image_url,
                    "person_slug": person_slug,
                },
            )
            if is_theory:
                sync_theory_card_taxonomy(
                    session,
                    person_id=person_id,
                    title=proposed_title or default_title,
                    tags=proposed_tags,
                )
            else:
                sync_people_card_taxonomy(
                    session,
                    person_id=person_id,
                    title=proposed_title or default_title,
                    tags=proposed_tags,
                )
            # Update article
            session.execute(
                text(
                    f"""
                    INSERT INTO {articles_table} (person_slug, markdown)
                    VALUES (:person_slug, :markdown)
                    ON CONFLICT (person_slug) DO UPDATE
                    SET markdown = EXCLUDED.markdown,
                        updated_at = now()
                    """
                ),
                {
                    "person_slug": person_slug,
                    "markdown": article_markdown_from_payload,
                },
            )
        else:
            session.execute(
                text(
                    f"""
                    INSERT INTO {articles_table} (person_slug, markdown)
                    VALUES (:person_slug, :markdown)
                    ON CONFLICT (person_slug) DO UPDATE
                    SET markdown = EXCLUDED.markdown,
                        updated_at = now()
                    """
                ),
                {
                    "person_slug": person_slug,
                    "markdown": proposed_markdown,
                },
            )

        session.execute(
            text(
                f"""
                UPDATE {proposals_table}
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
                "reviewer_user_id": admin_user_id,
                "review_note": review_note,
                "person_id": person_id,
                "proposed_payload": proposed_payload_value,
                "proposal_id": proposal_id_int,
            },
        )
        if is_theory:
            upsert_theory_diff_payload(
                session,
                proposal_id=proposal_id_int,
                person_id=person_id,
                scope=scope,
                base_payload=str(proposal.get("base_markdown") or ""),
                proposed_payload=proposed_markdown,
                base_image_url=base_image_url,
                proposed_image_url=proposed_image_url,
            )
        else:
            upsert_people_diff_payload(
                session,
                proposal_id=proposal_id_int,
                person_id=person_id,
                scope=scope,
                base_payload=str(proposal.get("base_markdown") or ""),
                proposed_payload=proposed_markdown,
                base_image_url=base_image_url,
                proposed_image_url=proposed_image_url,
            )
        _record_proposal_event(
            session,
            proposal_id_int,
            event_type="proposal_accepted",
            actor_user_id=admin_user_id,
            notes=review_note,
            payload={
                "person_slug": person_slug,
                "proposal_scope": scope,
                "proposed_image_url": proposed_image_url,
            },
            proposal_source=proposal_source,
        )

    next_pending_for_person = _next_pending_proposal_for_slug(
        person_slug,
        slug_filter=slug_filter,
        exclude_proposal_id=proposal_id_int,
        proposal_source=proposal_source,
    )
    panel = _build_admin_panel(
        next_pending_for_person,
        slug_filter=slug_filter,
        prioritize_proposal_id=next_pending_for_person,
        review_view_mode=review_view_mode,
    )
    return (
        f"✅ Propuesta #{proposal_id_int} aceptada y aplicada a `{person_slug}`.",
        *panel,
    )


def _open_decline_modal(proposal_id: str):
    selected = str(proposal_id or "").strip()
    selected_ref = _parse_proposal_choice_value(selected)
    if selected_ref is None:
        return (
            gr.update(visible=False),
            gr.update(value=""),
            "",
            "❌ Selecciona primero una propuesta válida.",
        )

    proposal = _fetch_proposal_by_id(
        selected_ref[1],
        proposal_source=selected_ref[0],
        source_proposal_type=selected_ref[2],
    )
    if proposal is None:
        return (
            gr.update(visible=False),
            gr.update(value=""),
            "",
            "❌ Propuesta no encontrada.",
        )

    status = str(proposal.get("status") or "").strip().lower()
    if status != "pending":
        return (
            gr.update(visible=False),
            gr.update(value=""),
            "",
            f"⚠️ Propuesta #{selected} ya está `{status or 'desconocido'}`.",
        )

    return (
        gr.update(visible=True),
        gr.update(value=""),
        "",
        gr.update(),
    )


def _cancel_decline_modal():
    return gr.update(visible=False), gr.update(value=""), ""


def _decline_admin_proposal(
    proposal_id: str,
    decline_reason: str,
    slug_filter: str,
    review_view_mode: str,
    request: gr.Request,
):
    user, is_admin, _ = _role_flags_from_request(request, refresh_if_not_admin=True)
    selected = str(proposal_id or "").strip()
    selected_ref = _parse_proposal_choice_value(selected)
    panel_selection = selected if selected_ref is not None else None
    panel = _build_admin_panel(panel_selection, slug_filter=slug_filter, review_view_mode=review_view_mode)

    if not user or not is_admin:
        return (
            "❌ Se requieren permisos de revisor.",
            gr.update(visible=False),
            gr.update(value=""),
            "",
            *panel,
        )

    if selected_ref is None:
        return (
            "❌ Selecciona primero una propuesta válida.",
            gr.update(visible=False),
            gr.update(value=""),
            "",
            *panel,
        )

    reason = (decline_reason or "").strip()
    if not reason:
        return (
            "❌ Se requiere un motivo de rechazo.",
            gr.update(visible=True),
            gr.update(value=decline_reason or ""),
            "❌ Introduce un motivo antes de rechazar esta propuesta.",
            *panel,
        )

    proposal_source, proposal_id_int, source_proposal_type = selected_ref
    proposal = _fetch_proposal_by_id(
        proposal_id_int,
        proposal_source=proposal_source,
        source_proposal_type=source_proposal_type,
    )
    if proposal is None:
        panel = _build_admin_panel(slug_filter=slug_filter, review_view_mode=review_view_mode)
        return (
            "❌ Propuesta no encontrada.",
            gr.update(visible=False),
            gr.update(value=""),
            "",
            *panel,
        )

    status = str(proposal.get("status") or "").strip().lower()
    if status != "pending":
        panel = _build_admin_panel(selected, slug_filter=slug_filter, review_view_mode=review_view_mode)
        return (
            f"⚠️ Propuesta #{proposal_id_int} ya está `{status or 'desconocido'}`.",
            gr.update(visible=False),
            gr.update(value=""),
            "",
            *panel,
        )

    admin_user_id = _resolve_request_user_id(user)
    if admin_user_id <= 0:
        panel = _build_admin_panel(selected, slug_filter=slug_filter, review_view_mode=review_view_mode)
        return (
            "❌ No se pudo resolver el id del usuario revisor.",
            gr.update(visible=False),
            gr.update(value=""),
            "",
            *panel,
        )

    if proposal_source == PROPOSAL_SOURCE_SOURCE:
        normalized_source_type = _normalize_source_proposal_type(
            proposal.get("source_proposal_type") or source_proposal_type
        )
        _ensure_local_db()
        with session_scope() as session:
            if normalized_source_type == SOURCE_PROPOSAL_TYPE_TAGS:
                session.execute(
                    text(
                        """
                        UPDATE app.unsorted_file_tag_proposals
                        SET status = 'declined',
                            reviewed_at = CURRENT_TIMESTAMP,
                            reviewer_user_id = :reviewer_user_id,
                            review_note = :review_note
                        WHERE id = :proposal_id
                        """
                    ),
                    {
                        "proposal_id": proposal_id_int,
                        "reviewer_user_id": admin_user_id,
                        "review_note": reason,
                    },
                )
            else:
                session.execute(
                    text(
                        """
                        UPDATE app.unsorted_file_push_proposals
                        SET status = 'declined',
                            reviewed_at = CURRENT_TIMESTAMP
                        WHERE id = :proposal_id
                        """
                    ),
                    {
                        "proposal_id": proposal_id_int,
                    },
                )

        panel = _build_admin_panel(selected, slug_filter=slug_filter, review_view_mode=review_view_mode)
        if normalized_source_type == SOURCE_PROPOSAL_TYPE_TAGS:
            return (
                f"✅ Propuesta de etiquetas de fuente #{proposal_id_int} rechazada.",
                gr.update(visible=False),
                gr.update(value=""),
                "",
                *panel,
            )
        return (
            f"✅ Propuesta de fuente #{proposal_id_int} rechazada.",
            gr.update(visible=False),
            gr.update(value=""),
            "",
            *panel,
        )

    _ensure_local_db()
    with session_scope() as session:
        proposals_table = (
            "app.theory_change_proposals"
            if proposal_source == PROPOSAL_SOURCE_THEORY
            else "app.people_change_proposals"
        )
        session.execute(
            text(
                f"""
                UPDATE {proposals_table}
                SET status = 'declined',
                    reviewed_at = CURRENT_TIMESTAMP,
                    reviewer_user_id = :reviewer_user_id,
                    review_note = :review_note,
                    report_triggered = 0
                WHERE id = :proposal_id
                """
            ),
            {
                "reviewer_user_id": admin_user_id,
                "review_note": reason,
                "proposal_id": proposal_id_int,
            },
        )
        _record_proposal_event(
            session,
            proposal_id_int,
            event_type="proposal_declined",
            actor_user_id=admin_user_id,
            notes=reason,
            payload={"person_slug": str(proposal.get("person_slug") or "").strip().lower()},
            proposal_source=proposal_source,
        )

    panel = _build_admin_panel(selected, slug_filter=slug_filter, review_view_mode=review_view_mode)
    return (
        f"✅ Propuesta #{proposal_id_int} rechazada.",
        gr.update(visible=False),
        gr.update(value=""),
        "",
        *panel,
    )


def _report_user_from_proposal(
    proposal_id: str,
    report_reason: str,
    slug_filter: str,
    review_view_mode: str,
    request: gr.Request,
):
    user, is_admin, _ = _role_flags_from_request(request, refresh_if_not_admin=True)
    if not user or not is_admin:
        panel = _build_admin_panel(slug_filter=slug_filter, review_view_mode=review_view_mode)
        return (
            "❌ Se requieren permisos de revisor.",
            report_reason,
            *panel,
        )

    selected = str(proposal_id or "").strip()
    selected_ref = _parse_proposal_choice_value(selected)
    if selected_ref is None:
        panel = _build_admin_panel(slug_filter=slug_filter, review_view_mode=review_view_mode)
        return (
            "❌ Selecciona primero una propuesta válida.",
            report_reason,
            *panel,
        )

    proposal_source, proposal_id_int, source_proposal_type = selected_ref
    proposal = _fetch_proposal_by_id(
        proposal_id_int,
        proposal_source=proposal_source,
        source_proposal_type=source_proposal_type,
    )
    if proposal is None:
        panel = _build_admin_panel(slug_filter=slug_filter, review_view_mode=review_view_mode)
        return (
            "❌ Propuesta no encontrada.",
            report_reason,
            *panel,
        )
    if proposal_source == PROPOSAL_SOURCE_SOURCE:
        panel = _build_admin_panel(selected, slug_filter=slug_filter, review_view_mode=review_view_mode)
        return (
            "❌ No se admite reportar propuestas de fuente.",
            report_reason,
            *panel,
        )

    reason = (report_reason or "").strip() or "Reportado por revisor desde el panel de revisión"
    admin_user_id = _resolve_request_user_id(user)
    if admin_user_id <= 0:
        panel = _build_admin_panel(selected, slug_filter=slug_filter, review_view_mode=review_view_mode)
        return (
            "❌ No se pudo resolver el id del usuario revisor.",
            report_reason,
            *panel,
        )
    admin_email = (user.get("email") or "").strip().lower()
    if not admin_email:
        admin_email = _resolve_user_email_by_id(admin_user_id)

    target_user_id = int(proposal.get("proposer_user_id") or 0)
    target_email = (proposal.get("proposer_email") or "").strip().lower()
    if not target_email and target_user_id > 0:
        target_email = _resolve_user_email_by_id(target_user_id)

    _ensure_local_db()
    with session_scope() as session:
        proposals_table = (
            "app.theory_change_proposals"
            if proposal_source == PROPOSAL_SOURCE_THEORY
            else "app.people_change_proposals"
        )
        session.execute(
            text(
                f"""
                UPDATE {proposals_table}
                SET status = 'reported',
                    reviewed_at = CURRENT_TIMESTAMP,
                    reviewer_user_id = :reviewer_user_id,
                    review_note = :review_note,
                    report_triggered = 1
                WHERE id = :proposal_id
                """
            ),
            {
                "reviewer_user_id": admin_user_id,
                "review_note": reason,
                "proposal_id": proposal_id_int,
            },
        )
        _record_proposal_event(
            session,
            proposal_id_int,
            event_type="user_reported",
            actor_user_id=admin_user_id,
            notes=reason,
            payload={"target_user_id": target_user_id, "target_email": target_email},
            proposal_source=proposal_source,
        )

    role_updated = bool(target_email) and report_and_disable_user(target_email, admin_email, reason)
    panel = _build_admin_panel(selected, slug_filter=slug_filter, review_view_mode=review_view_mode)
    if role_updated and target_email:
        message = f"✅ Usuario `{target_email}` reportado y privilegio `base_user` eliminado."
    elif target_email:
        message = "⚠️ Propuesta reportada, pero no se actualizó ningún registro de privilegios del usuario."
    else:
        message = "⚠️ Propuesta reportada, pero no se pudo resolver el correo del proponente para actualizar privilegios."
    return (
        message,
        "",
        *panel,
    )


def make_people_app() -> gr.Blocks:
    stylesheet = _load_css()
    editor_js = _load_editor_js()
    with gr.Blocks(
        title="Recetas",
        css=stylesheet or None,
        head=with_light_mode_head(editor_js),
    ) as app:
        hdr = gr.HTML()
        markdown_edit_mode_state = gr.State(False)
        card_edit_mode_state = gr.State(False)

        with gr.Column(elem_id="people-shell"):
            with gr.Row(elem_id="people-title-row"):
                title_md = gr.HTML("<h2>Recetas</h2>", elem_id="people-title")
                with gr.Column(elem_id="people-filter-row", visible=False, scale=0, min_width=210) as tag_filter_row:
                    tag_filter = gr.Dropdown(
                        label="Filtrar por etiquetas",
                        choices=[(TAG_FILTER_ALL_OPTION, TAG_FILTER_ALL_OPTION)],
                        value=[],
                        multiselect=True,
                        allow_custom_value=False,
                        interactive=True,
                        show_label=False,
                        container=False,
                        elem_id="people-tag-filter",
                    )
            markdown_edit_btn = gr.Button(
                EDIT_TOGGLE_BUTTON_LABEL,
                visible=False,
                variant="secondary",
                elem_id="the-list-markdown-edit-btn",
            )
            card_edit_btn = gr.Button(
                EDIT_TOGGLE_BUTTON_LABEL,
                visible=False,
                variant="secondary",
                elem_id="the-list-card-edit-btn",
            )
            review_btn_html = gr.HTML(visible=False, elem_id="the-list-review-link")
            tag_filter_selection_state = gr.State([])
            cards_html = gr.HTML(elem_id="people-cards")
            detail_html = gr.HTML(visible=False, elem_id="person-detail-hero")
            detail_markdown = gr.Markdown(visible=False, elem_id="person-detail-markdown")

            current_slug = gr.Textbox(value="", visible=False, interactive=False, elem_id="the-list-current-slug")
            current_markdown = gr.Textbox(
                value="",
                visible=False,
                interactive=False,
                elem_id="the-list-current-markdown",
            )
            current_name = gr.Textbox(
                value="",
                visible=False,
                interactive=False,
                elem_id="the-list-current-name",
            )
            current_bucket = gr.Textbox(
                value="",
                visible=False,
                interactive=False,
                elem_id="the-list-current-bucket",
            )
            current_tags = gr.Textbox(
                value="",
                visible=False,
                interactive=False,
                elem_id="the-list-current-tags",
            )

            with gr.Column(visible=False, elem_id="the-list-proposal-shell") as proposal_shell:
                proposal_help = gr.Markdown(elem_id="the-list-proposal-help")
                proposal_note = gr.Textbox(
                    label="Resumen del cambio del artículo",
                    lines=2,
                    placeholder="Resumen breve para revisores...",
                )
                with gr.Row(elem_id="the-list-markdown-toolbar"):
                    gr.Markdown("**Markdown de artículo propuesto**")
                    with gr.Row(elem_id="the-list-markdown-toolbar-controls"):
                        proposal_view_mode = gr.Radio(
                            choices=[
                                ("Compilado", MARKDOWN_VIEW_PREVIEW),
                                ("Markdown en bruto", MARKDOWN_VIEW_RAW),
                            ],
                            value=DEFAULT_MARKDOWN_VIEW,
                            show_label=False,
                            container=False,
                            interactive=True,
                            elem_id="the-list-proposal-view-mode",
                            scale=0,
                            min_width=0,
                        )
                with gr.Row(elem_id="the-list-proposal-editor-grid"):
                    proposal_markdown = gr.Textbox(
                        show_label=False,
                        lines=14,
                        placeholder="Edita el markdown del artículo aquí...",
                        elem_id="the-list-proposal-markdown-input",
                    )
                    proposal_preview = gr.Markdown(value="", visible=True, elem_id="the-list-proposal-preview")
                with gr.Row(elem_id="the-list-proposal-actions"):
                    submit_markdown_proposal_btn = gr.Button("Enviar propuesta de artículo", variant="primary")
                    cancel_markdown_edit_btn = gr.Button("Cancelar", variant="secondary")
                proposal_status = gr.Markdown(elem_id="the-list-proposal-status")

            with gr.Column(visible=False, elem_id="the-list-card-proposal-shell") as card_proposal_shell:
                card_proposal_help = gr.Markdown(elem_id="the-list-card-proposal-help")
                with gr.Row(elem_id="the-list-card-proposal-grid"):
                    card_proposal_name = gr.Textbox(label="Nombre de la tarjeta", elem_id="the-list-card-proposal-name")
                    card_proposal_bucket = gr.Textbox(
                        label="Título de la tarjeta",
                        elem_id="the-list-card-proposal-bucket",
                    )
                card_proposal_tags = gr.Textbox(
                    label="Etiquetas de la tarjeta",
                    lines=2,
                    placeholder="Etiquetas separadas por comas (ejemplo: zurdo, centros, resistencia)",
                    elem_id="the-list-card-proposal-tags",
                )
                with gr.Row(elem_id="the-list-card-image-row"):
                    gr.Markdown("**Imagen de la tarjeta**")
                    card_proposal_image = gr.UploadButton(
                        "+",
                        file_types=["image"],
                        file_count="single",
                        elem_id="the-list-card-image-plus-btn",
                        scale=0,
                        min_width=40,
                    )
                card_proposal_note = gr.Textbox(
                    label="Resumen del cambio de tarjeta",
                    lines=2,
                    placeholder="Resumen breve para revisores...",
                    elem_id="the-list-card-proposal-note",
                )
                with gr.Row(elem_id="the-list-card-proposal-actions"):
                    submit_card_proposal_btn = gr.Button("Enviar", variant="primary")
                    cancel_card_edit_btn = gr.Button("Cancelar", variant="secondary")
                card_proposal_status = gr.Markdown(elem_id="the-list-card-proposal-status")

        app.load(timed_page_load("/recetas", _header_people), outputs=[hdr])
        app.load(
            timed_page_load("/recetas", _load_people_page),
            outputs=[
                title_md,
                tag_filter_row,
                tag_filter,
                tag_filter_selection_state,
                cards_html,
                detail_html,
                detail_markdown,
                current_slug,
                current_markdown,
                current_name,
                current_bucket,
                current_tags,
                markdown_edit_mode_state,
                markdown_edit_btn,
                card_edit_mode_state,
                card_edit_btn,
                review_btn_html,
                proposal_shell,
                proposal_help,
                proposal_markdown,
                proposal_status,
                card_proposal_shell,
                card_proposal_help,
                card_proposal_name,
                card_proposal_bucket,
                card_proposal_tags,
                card_proposal_status,
            ],
        )
        tag_filter.input(
            timed_page_load(
                "/recetas",
                _update_people_cards_by_tag_filter,
                label="update_people_cards_by_tag_filter",
            ),
            inputs=[tag_filter, tag_filter_selection_state],
            outputs=[tag_filter, tag_filter_selection_state, cards_html],
            show_progress=False,
        )

        markdown_edit_btn.click(
            timed_page_load("/recetas", _toggle_markdown_editor, label="toggle_markdown_editor"),
            inputs=[markdown_edit_mode_state, current_slug, current_markdown],
            outputs=[
                markdown_edit_mode_state,
                detail_markdown,
                proposal_shell,
                proposal_markdown,
                proposal_preview,
                proposal_view_mode,
                markdown_edit_btn,
                proposal_status,
            ],
        )

        cancel_markdown_edit_btn.click(
            timed_page_load("/recetas", _cancel_markdown_editor, label="cancel_markdown_editor"),
            inputs=[current_markdown],
            outputs=[
                markdown_edit_mode_state,
                detail_markdown,
                proposal_shell,
                proposal_markdown,
                proposal_preview,
                proposal_view_mode,
                markdown_edit_btn,
                proposal_note,
                proposal_status,
            ],
        )

        proposal_view_mode.change(
            timed_page_load(
                "/recetas",
                _toggle_proposal_markdown_view,
                label="toggle_proposal_markdown_view",
            ),
            inputs=[proposal_view_mode, proposal_markdown],
            outputs=[proposal_markdown, proposal_preview],
        )
        proposal_markdown.input(
            timed_page_load(
                "/recetas",
                _toggle_proposal_markdown_view,
                label="live_toggle_proposal_markdown_view",
            ),
            inputs=[proposal_view_mode, proposal_markdown],
            outputs=[proposal_markdown, proposal_preview],
        )

        submit_markdown_proposal_btn.click(
            timed_page_load("/recetas", _submit_markdown_proposal, label="submit_markdown_proposal"),
            inputs=[
                current_slug,
                proposal_note,
                proposal_markdown,
                current_markdown,
                markdown_edit_mode_state,
            ],
            outputs=[
                proposal_status,
                proposal_note,
                markdown_edit_mode_state,
                detail_markdown,
                proposal_shell,
                proposal_markdown,
                proposal_preview,
                proposal_view_mode,
                markdown_edit_btn,
            ],
        )

        card_edit_btn.click(
            timed_page_load("/recetas", _toggle_card_editor, label="toggle_card_editor"),
            inputs=[card_edit_mode_state, current_slug, current_name, current_bucket, current_tags],
            outputs=[
                card_edit_mode_state,
                card_proposal_shell,
                card_proposal_name,
                card_proposal_bucket,
                card_proposal_tags,
                card_edit_btn,
                card_proposal_status,
            ],
        )

        cancel_card_edit_btn.click(
            timed_page_load("/recetas", _cancel_card_editor, label="cancel_card_editor"),
            inputs=[current_name, current_bucket, current_tags],
            outputs=[
                card_edit_mode_state,
                card_proposal_shell,
                card_proposal_name,
                card_proposal_bucket,
                card_proposal_tags,
                card_edit_btn,
                card_proposal_note,
                card_proposal_status,
                card_proposal_image,
            ],
        )

        submit_card_proposal_btn.click(
            timed_page_load("/recetas", _submit_card_proposal, label="submit_card_proposal"),
            inputs=[
                current_slug,
                card_proposal_note,
                card_proposal_name,
                card_proposal_bucket,
                card_proposal_tags,
                card_proposal_image,
                current_name,
                current_bucket,
                current_tags,
                card_edit_mode_state,
            ],
            outputs=[
                card_proposal_status,
                card_proposal_note,
                card_proposal_image,
                card_edit_mode_state,
                card_proposal_shell,
                card_proposal_name,
                card_proposal_bucket,
                card_proposal_tags,
                card_edit_btn,
            ],
        )

    return app


def make_people_review_app() -> gr.Blocks:
    stylesheet = _load_css()
    review_js = _load_review_js()
    with gr.Blocks(
        title="Revisión de Recetas",
        css=stylesheet or None,
        head=with_light_mode_head(review_js),
    ) as app:
        hdr = gr.HTML()
        with gr.Column(elem_id="the-list-admin-shell"):
            title_md = gr.Markdown("## Revisión de Recetas")
            summary_md = gr.Markdown("")
            slug_filter_state = gr.Textbox(value="", visible=False, interactive=False)
            admin_scope_state = gr.Textbox(value="", visible=False, interactive=False)
            admin_change_groups_state = gr.Textbox(value="[]", visible=False, interactive=False)
            admin_change_action = gr.Textbox(value="", visible=False, interactive=True, elem_id="the-list-review-change-action")
            admin_apply_change_btn = gr.Button(
                "Aplicar cambio de revisión",
                visible=False,
                elem_id="the-list-review-apply-change-btn",
            )
            with gr.Row(elem_id="the-list-admin-selector-row"):
                admin_selector = gr.Dropdown(
                    label="Propuestas en seguimiento",
                    choices=[],
                    value=None,
                    allow_custom_value=False,
                    interactive=True,
                    elem_id="the-list-admin-proposal-selector",
                    scale=12,
                )
                refresh_admin_btn = gr.Button(
                    "Actualizar propuestas",
                    elem_id="the-list-admin-refresh-btn",
                    variant="secondary",
                    scale=1,
                )
                admin_card_selector = gr.Dropdown(
                    label="Slug en revisión",
                    choices=[("Todos los slugs", "")],
                    value="",
                    allow_custom_value=False,
                    interactive=True,
                    elem_id="the-list-admin-card-selector",
                    scale=6,
                )
            admin_meta = gr.Markdown(elem_id="the-list-admin-meta")
            admin_images = gr.HTML(elem_id="the-list-admin-images", container=False)
            with gr.Row(elem_id="the-list-admin-review-view-mode-row"):
                admin_review_view_mode = gr.Radio(
                    choices=[
                        ("Compilado", REVIEW_VIEW_COMPILED),
                        ("Markdown en bruto", REVIEW_VIEW_RAW),
                    ],
                    value=DEFAULT_REVIEW_VIEW,
                    label="Vista de revisión",
                    show_label=False,
                    container=False,
                    interactive=True,
                    elem_id="the-list-admin-review-view-mode",
                    scale=1,
                    min_width=240,
                )
            with gr.Tabs(elem_id="the-list-admin-preview-tabs"):
                with gr.Tab("Revisión"):
                    with gr.Row(elem_id="the-list-admin-compiled-grid"):
                        admin_compiled_base = _markdown_component_allow_raw_html(elem_id="the-list-admin-compiled-base")
                        admin_compiled_current = _markdown_component_allow_raw_html(
                            elem_id="the-list-admin-compiled-current"
                        )
                        admin_compiled_proposed = _markdown_component_allow_raw_html(
                            elem_id="the-list-admin-compiled-proposed"
                        )
                with gr.Tab("Contenido de artículo en bruto"):
                    with gr.Row(elem_id="the-list-admin-raw-grid"):
                        admin_raw_base = gr.Textbox(
                            label="Contenido base (bruto)",
                            lines=14,
                            interactive=False,
                            elem_id="the-list-admin-raw-base",
                        )
                        admin_raw_current = gr.Textbox(
                            label="Contenido actual (bruto)",
                            lines=14,
                            interactive=False,
                            elem_id="the-list-admin-raw-current",
                        )
                        admin_raw_proposed = gr.Textbox(
                            label="Contenido propuesto (bruto, editable)",
                            lines=14,
                            interactive=True,
                            elem_id="the-list-admin-raw-proposed",
                        )
                    admin_diff = gr.HTML(elem_id="the-list-admin-diff")
            with gr.Row(elem_id="the-list-admin-review-actions"):
                accept_btn = gr.Button(
                    "Aceptar propuesta",
                    variant="primary",
                    elem_id="the-list-admin-accept-btn",
                )
                decline_btn = gr.Button(
                    "Rechazar propuesta",
                    variant="stop",
                    elem_id="the-list-admin-decline-btn",
                )
            report_reason = gr.Textbox(
                label="Motivo del reporte",
                lines=2,
                placeholder="Motivo para retirar el privilegio `user` al usuario...",
            )
            report_btn = gr.Button("Reportar usuario y quitar privilegio `user`", variant="stop")
            admin_status = gr.Markdown(elem_id="the-list-admin-status")
        with gr.Column(visible=False, elem_id="the-list-decline-modal-overlay") as decline_modal:
            with gr.Column(elem_id="the-list-decline-modal"):
                gr.Markdown("### Rechazar propuesta")
                decline_reason = gr.Textbox(
                    label="Motivo",
                    lines=4,
                    placeholder="Explica por qué se rechazó esta propuesta...",
                )
                decline_modal_status = gr.Markdown(elem_id="the-list-decline-modal-status")
                with gr.Row(elem_id="the-list-decline-modal-actions"):
                    decline_cancel_btn = gr.Button("Cancelar", variant="secondary")
                    decline_confirm_btn = gr.Button("Rechazar propuesta", variant="stop")

        app.load(timed_page_load("/review", _header_people_review), outputs=[hdr])
        app.load(
            timed_page_load("/review", _load_people_review_page),
            outputs=[
                title_md,
                summary_md,
                admin_card_selector,
                slug_filter_state,
                admin_selector,
                admin_meta,
                admin_images,
                admin_compiled_base,
                admin_compiled_current,
                admin_compiled_proposed,
                admin_raw_base,
                admin_raw_current,
                admin_raw_proposed,
                admin_diff,
                admin_scope_state,
                admin_change_groups_state,
                admin_status,
            ],
        )

        admin_card_selector.change(
            timed_page_load("/review", _change_admin_slug_filter, label="change_admin_slug_filter"),
            inputs=[admin_card_selector, admin_review_view_mode],
            outputs=[
                slug_filter_state,
                summary_md,
                admin_selector,
                admin_meta,
                admin_images,
                admin_compiled_base,
                admin_compiled_current,
                admin_compiled_proposed,
                admin_raw_base,
                admin_raw_current,
                admin_raw_proposed,
                admin_diff,
                admin_scope_state,
                admin_change_groups_state,
                admin_status,
            ],
        )

        refresh_admin_btn.click(
            timed_page_load("/review", _refresh_admin_panel, label="refresh_admin_panel"),
            inputs=[slug_filter_state, admin_review_view_mode],
            outputs=[
                admin_selector,
                admin_meta,
                admin_images,
                admin_compiled_base,
                admin_compiled_current,
                admin_compiled_proposed,
                admin_raw_base,
                admin_raw_current,
                admin_raw_proposed,
                admin_diff,
                admin_scope_state,
                admin_change_groups_state,
                admin_status,
            ],
        )

        admin_selector.select(
            timed_page_load("/review", _select_admin_proposal, label="select_admin_proposal"),
            inputs=[admin_selector, slug_filter_state, admin_review_view_mode],
            outputs=[
                admin_meta,
                admin_images,
                admin_compiled_base,
                admin_compiled_current,
                admin_compiled_proposed,
                admin_raw_base,
                admin_raw_current,
                admin_raw_proposed,
                admin_diff,
                admin_scope_state,
                admin_change_groups_state,
                admin_status,
            ],
        )

        admin_raw_proposed.blur(
            timed_page_load("/review", _preview_admin_proposed_edit, label="preview_admin_proposed_edit"),
            inputs=[
                admin_selector,
                admin_scope_state,
                admin_raw_base,
                admin_raw_current,
                admin_raw_proposed,
                admin_review_view_mode,
            ],
            outputs=[
                admin_compiled_base,
                admin_compiled_current,
                admin_compiled_proposed,
                admin_diff,
                admin_change_groups_state,
                admin_status,
            ],
            show_progress=False,
        )

        admin_review_view_mode.change(
            timed_page_load(
                "/review",
                _toggle_admin_review_view_mode,
                label="toggle_admin_review_view_mode",
            ),
            inputs=[
                admin_review_view_mode,
                admin_scope_state,
                admin_raw_base,
                admin_raw_current,
                admin_raw_proposed,
            ],
            outputs=[
                admin_compiled_base,
                admin_compiled_current,
                admin_compiled_proposed,
            ],
            show_progress=False,
        )

        admin_apply_change_btn.click(
            timed_page_load("/review", _apply_review_change_choice, label="apply_review_change_choice"),
            inputs=[
                admin_change_action,
                admin_scope_state,
                admin_change_groups_state,
                admin_raw_base,
                admin_raw_current,
                admin_raw_proposed,
                admin_review_view_mode,
            ],
            outputs=[
                admin_raw_proposed,
                admin_compiled_base,
                admin_compiled_current,
                admin_compiled_proposed,
                admin_diff,
                admin_change_groups_state,
                admin_status,
            ],
            show_progress=False,
        )

        accept_btn.click(
            timed_page_load("/review", _accept_admin_proposal, label="accept_admin_proposal"),
            inputs=[admin_selector, slug_filter_state, admin_raw_proposed, admin_review_view_mode],
            outputs=[
                admin_status,
                admin_selector,
                admin_meta,
                admin_images,
                admin_compiled_base,
                admin_compiled_current,
                admin_compiled_proposed,
                admin_raw_base,
                admin_raw_current,
                admin_raw_proposed,
                admin_diff,
                admin_scope_state,
                admin_change_groups_state,
            ],
        )

        decline_btn.click(
            timed_page_load("/review", _open_decline_modal, label="open_decline_modal"),
            inputs=[admin_selector],
            outputs=[decline_modal, decline_reason, decline_modal_status, admin_status],
        )

        decline_cancel_btn.click(
            timed_page_load("/review", _cancel_decline_modal, label="cancel_decline_modal"),
            outputs=[decline_modal, decline_reason, decline_modal_status],
        )

        decline_confirm_btn.click(
            timed_page_load("/review", _decline_admin_proposal, label="decline_admin_proposal"),
            inputs=[admin_selector, decline_reason, slug_filter_state, admin_review_view_mode],
            outputs=[
                admin_status,
                decline_modal,
                decline_reason,
                decline_modal_status,
                admin_selector,
                admin_meta,
                admin_images,
                admin_compiled_base,
                admin_compiled_current,
                admin_compiled_proposed,
                admin_raw_base,
                admin_raw_current,
                admin_raw_proposed,
                admin_diff,
                admin_scope_state,
                admin_change_groups_state,
            ],
        )

        report_btn.click(
            timed_page_load("/review", _report_user_from_proposal, label="report_user_from_proposal"),
            inputs=[admin_selector, report_reason, slug_filter_state, admin_review_view_mode],
            outputs=[
                admin_status,
                report_reason,
                admin_selector,
                admin_meta,
                admin_images,
                admin_compiled_base,
                admin_compiled_current,
                admin_compiled_proposed,
                admin_raw_base,
                admin_raw_current,
                admin_raw_proposed,
                admin_diff,
                admin_scope_state,
                admin_change_groups_state,
            ],
        )

    return app
