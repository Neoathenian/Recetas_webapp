from __future__ import annotations

from typing import Dict

from sqlalchemy import text

from src.db import session_scope


def _normalize_email(email: str | None) -> str:
    return (email or "").strip().lower()


def get_local_role_flags(email: str | None) -> Dict[str, bool]:
    """
    Backward-compatible helper name.
    Role flags are now sourced from app.user_privileges in Postgres.
    """
    normalized_email = _normalize_email(email)
    if not normalized_email:
        return {
            "base_user": False,
            "reviewer": False,
            "editor": False,
            "admin": False,
            "creator": False,
        }

    with session_scope() as session:
        row = session.execute(
            text(
                """
                SELECT
                    p.base_user,
                    p.reviewer,
                    COALESCE((to_jsonb(p) ->> 'editor')::boolean, FALSE) AS editor,
                    COALESCE(
                        (to_jsonb(p) ->> 'admin')::boolean,
                        (to_jsonb(p) ->> 'reviewer_creator')::boolean,
                        FALSE
                    ) AS admin,
                    COALESCE((to_jsonb(p) ->> 'creator')::boolean, FALSE) AS creator
                FROM app.user_privileges p
                WHERE lower(p.email) = lower(:email)
                """
            ),
            {"email": normalized_email},
        ).mappings().one_or_none()

    if row is None:
        return {
            "base_user": False,
            "reviewer": False,
            "editor": False,
            "admin": False,
            "creator": False,
        }

    return {
        "base_user": bool(row.get("base_user")),
        "reviewer": bool(row.get("reviewer")),
        "editor": bool(row.get("editor")),
        "admin": bool(row.get("admin")),
        "creator": bool(row.get("creator")),
    }


def report_and_disable_user(email: str | None, reported_by: str | None, reason: str | None) -> bool:
    normalized_email = _normalize_email(email)
    if not normalized_email:
        return False

    with session_scope() as session:
        session.execute(
            text(
                """
                ALTER TABLE app.user_privileges
                ADD COLUMN IF NOT EXISTS editor BOOLEAN NOT NULL DEFAULT FALSE
                """
            )
        )
        session.execute(
            text(
                """
                ALTER TABLE app.user_privileges
                ADD COLUMN IF NOT EXISTS admin BOOLEAN NOT NULL DEFAULT FALSE
                """
            )
        )
        session.execute(
            text(
                """
                UPDATE app.user_privileges AS p
                SET admin = TRUE
                WHERE p.admin = FALSE
                  AND COALESCE((to_jsonb(p) ->> 'reviewer_creator')::boolean, FALSE)
                """
            )
        )
        existing = session.execute(
            text(
                """
                SELECT email
                FROM app.user_privileges
                WHERE lower(email) = lower(:email)
                """
            ),
            {"email": normalized_email},
        ).scalar_one_or_none()
        if existing is None:
            return False

        _ = reported_by
        _ = reason
        result = session.execute(
            text(
                """
                UPDATE app.user_privileges
                SET base_user = FALSE,
                    reviewer = FALSE,
                    editor = FALSE,
                    admin = FALSE
                WHERE lower(email) = lower(:email)
                """
            ),
            {"email": normalized_email},
        )
    return bool(result.rowcount)
