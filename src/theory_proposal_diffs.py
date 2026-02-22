from __future__ import annotations

import json

from sqlalchemy import text
from sqlalchemy.orm import Session

PROPOSAL_SCOPE_ARTICLE = "article"
PROPOSAL_SCOPE_CARD = "card"
PROPOSAL_SCOPE_CARD_ARTICLE = "card_article"
LEGACY_PROPOSAL_SCOPE_DESCRIPTION = "description"


def normalize_proposal_scope(value: object) -> str:
    normalized = str(value or "").strip().lower()
    if normalized == PROPOSAL_SCOPE_CARD:
        return PROPOSAL_SCOPE_CARD
    if normalized == PROPOSAL_SCOPE_CARD_ARTICLE:
        return PROPOSAL_SCOPE_CARD_ARTICLE
    if normalized in {PROPOSAL_SCOPE_ARTICLE, LEGACY_PROPOSAL_SCOPE_DESCRIPTION}:
        return PROPOSAL_SCOPE_ARTICLE
    return PROPOSAL_SCOPE_ARTICLE


def _table_exists(session: Session, table_name: str) -> bool:
    return bool(
        session.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM information_schema.tables
                    WHERE table_schema = 'app'
                      AND table_name = :table_name
                )
                """
            ),
            {"table_name": table_name},
        ).scalar_one()
    )


def _column_exists(session: Session, table_name: str, column_name: str) -> bool:
    return bool(
        session.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_schema = 'app'
                      AND table_name = :table_name
                      AND column_name = :column_name
                )
                """
            ),
            {"table_name": table_name, "column_name": column_name},
        ).scalar_one()
    )


def _merge_card_payload_with_image(raw_payload: object, image_url: object) -> str:
    payload_text = str(raw_payload or "").strip()
    image_text = str(image_url or "").strip()
    parsed: dict[str, object] = {}
    if payload_text:
        try:
            payload_json = json.loads(payload_text)
            if isinstance(payload_json, dict):
                parsed = dict(payload_json)
        except json.JSONDecodeError:
            parsed = {}
    merged = {
        "name": str(parsed.get("name") or "").strip(),
        "title": str(parsed.get("title") or parsed.get("bucket") or "").strip(),
        "tags": [
            str(tag).strip().lower()
            for tag in (parsed.get("tags") if isinstance(parsed.get("tags"), list) else [])
            if str(tag).strip()
        ],
        "image_url": image_text or str(parsed.get("image_url") or "").strip(),
    }
    return json.dumps(merged, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _resolve_person_id_for_proposal(session: Session, proposal_id: int) -> int:
    return int(
        session.execute(
            text(
                """
                SELECT c.person_id
                FROM app.theory_change_proposals p
                JOIN app.theory_cards c
                    ON c.slug = p.person_slug
                WHERE p.id = :proposal_id
                """
            ),
            {"proposal_id": int(proposal_id)},
        ).scalar_one_or_none()
        or 0
    )


def _backfill_from_legacy_columns(session: Session) -> None:
    has_base_markdown = _column_exists(session, "theory_change_proposals", "base_markdown")
    has_proposed_markdown = _column_exists(session, "theory_change_proposals", "proposed_markdown")
    has_base_image_url = _column_exists(session, "theory_change_proposals", "base_image_url")
    has_proposed_image_url = _column_exists(session, "theory_change_proposals", "proposed_image_url")
    if not (has_base_markdown and has_proposed_markdown):
        return

    session.execute(
        text(
            """
            UPDATE app.theory_change_proposals
            SET base_payload = CASE
                    WHEN COALESCE(base_payload, '') = '' THEN COALESCE(base_markdown, '')
                    ELSE base_payload
                END,
                proposed_payload = CASE
                    WHEN COALESCE(proposed_payload, '') = '' THEN COALESCE(proposed_markdown, '')
                    ELSE proposed_payload
                END
            WHERE COALESCE(lower(proposal_scope), '') IN ('article', 'description')
            """
        )
    )

    if not (has_base_image_url and has_proposed_image_url):
        return

    card_rows = session.execute(
        text(
            """
            SELECT
                id,
                COALESCE(base_payload, '') AS base_payload,
                COALESCE(proposed_payload, '') AS proposed_payload,
                COALESCE(base_markdown, '') AS base_markdown,
                COALESCE(proposed_markdown, '') AS proposed_markdown,
                COALESCE(base_image_url, '') AS base_image_url,
                COALESCE(proposed_image_url, '') AS proposed_image_url
            FROM app.theory_change_proposals
            WHERE COALESCE(lower(proposal_scope), '') = 'card'
            """
        )
    ).mappings().all()

    for row in card_rows:
        base_payload_source = str(row["base_payload"] or "").strip() or str(row["base_markdown"] or "")
        proposed_payload_source = str(row["proposed_payload"] or "").strip() or str(row["proposed_markdown"] or "")
        session.execute(
            text(
                """
                UPDATE app.theory_change_proposals
                SET base_payload = :base_payload,
                    proposed_payload = :proposed_payload
                WHERE id = :proposal_id
                """
            ),
            {
                "proposal_id": int(row["id"]),
                "base_payload": _merge_card_payload_with_image(base_payload_source, row["base_image_url"]),
                "proposed_payload": _merge_card_payload_with_image(proposed_payload_source, row["proposed_image_url"]),
            },
        )


def _backfill_from_legacy_diff_tables(session: Session) -> None:
    if _table_exists(session, "theory_article_change_diffs"):
        session.execute(
            text(
                """
                UPDATE app.theory_change_proposals p
                SET person_id = CASE
                        WHEN COALESCE(p.person_id, 0) <= 0 THEN ad.person_id
                        ELSE p.person_id
                    END,
                    base_payload = CASE
                        WHEN COALESCE(p.base_payload, '') = '' THEN ad.base_markdown
                        ELSE p.base_payload
                    END,
                    proposed_payload = CASE
                        WHEN COALESCE(p.proposed_payload, '') = '' THEN ad.proposed_markdown
                        ELSE p.proposed_payload
                    END
                FROM app.theory_article_change_diffs ad
                WHERE ad.proposal_id = p.id
                  AND COALESCE(lower(p.proposal_scope), '') IN ('article', 'description')
                """
            )
        )

    if not _table_exists(session, "theory_card_change_diffs"):
        return

    card_rows = session.execute(
        text(
            """
            SELECT
                p.id,
                p.person_id,
                cd.person_id AS diff_person_id,
                COALESCE(p.base_payload, '') AS base_payload,
                COALESCE(p.proposed_payload, '') AS proposed_payload,
                COALESCE(cd.base_payload, '') AS diff_base_payload,
                COALESCE(cd.proposed_payload, '') AS diff_proposed_payload,
                COALESCE(cd.base_image_url, '') AS base_image_url,
                COALESCE(cd.proposed_image_url, '') AS proposed_image_url
            FROM app.theory_change_proposals p
            JOIN app.theory_card_change_diffs cd
                ON cd.proposal_id = p.id
            WHERE COALESCE(lower(p.proposal_scope), '') = 'card'
            """
        )
    ).mappings().all()

    for row in card_rows:
        base_payload_source = str(row["base_payload"] or "").strip() or str(row["diff_base_payload"] or "")
        proposed_payload_source = str(row["proposed_payload"] or "").strip() or str(row["diff_proposed_payload"] or "")
        merged_person_id = int(row["person_id"] or 0) or int(row["diff_person_id"] or 0)
        session.execute(
            text(
                """
                UPDATE app.theory_change_proposals
                SET person_id = CASE
                        WHEN COALESCE(person_id, 0) <= 0 THEN :person_id
                        ELSE person_id
                    END,
                    base_payload = :base_payload,
                    proposed_payload = :proposed_payload
                WHERE id = :proposal_id
                """
            ),
            {
                "proposal_id": int(row["id"]),
                "person_id": merged_person_id,
                "base_payload": _merge_card_payload_with_image(base_payload_source, row["base_image_url"]),
                "proposed_payload": _merge_card_payload_with_image(proposed_payload_source, row["proposed_image_url"]),
            },
        )


def ensure_theory_diff_tables(session: Session) -> None:
    _ = session
    return


def upsert_theory_diff_payload(
    session: Session,
    *,
    proposal_id: int,
    person_id: int,
    scope: str,
    base_payload: str,
    proposed_payload: str,
    base_image_url: str = "",
    proposed_image_url: str = "",
) -> None:
    proposal_scope = normalize_proposal_scope(scope)
    normalized_person_id = int(person_id or 0)
    if normalized_person_id <= 0:
        normalized_person_id = _resolve_person_id_for_proposal(session, int(proposal_id))
    if normalized_person_id <= 0:
        raise ValueError(f"Could not resolve person_id for proposal_id={int(proposal_id)}")

    base_payload_value = str(base_payload or "")
    proposed_payload_value = str(proposed_payload or "")
    if proposal_scope == PROPOSAL_SCOPE_CARD:
        base_payload_value = _merge_card_payload_with_image(base_payload_value, base_image_url)
        proposed_payload_value = _merge_card_payload_with_image(proposed_payload_value, proposed_image_url)

    session.execute(
        text(
            """
            UPDATE app.theory_change_proposals
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
            "proposal_scope": proposal_scope,
            "base_payload": base_payload_value,
            "proposed_payload": proposed_payload_value,
        },
    )
