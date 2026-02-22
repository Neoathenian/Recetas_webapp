from __future__ import annotations

from typing import Optional, Tuple

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from src.db import make_code

_PLACEHOLDER_DOMAIN = "placeholder.local"


def normalize_email(raw: Optional[str], *, default_prefix: str = "user") -> str:
    """
    Normalize an arbitrary identifier into an email-like string.
    Falls back to a placeholder domain when the identifier lacks an @ symbol.
    """
    value = (raw or "").strip().lower()
    if value and "@" in value:
        return value
    slug = make_code(value or default_prefix, default_prefix=default_prefix)
    return f"{slug}@{_PLACEHOLDER_DOMAIN}"


def ensure_user(
    session: Session,
    *,
    user_identifier: str,
    display_name: Optional[str] = None,
) -> Tuple[int, str, str]:
    """
    Ensure a row exists in app."user" for the provided identifier.
    """
    email = normalize_email(user_identifier or display_name)
    name = (display_name or "").strip() or email.split("@", 1)[0]
    username = name

    row = session.execute(
        text(
            """
            INSERT INTO app."user" (name, username, email)
            VALUES (:name, :username, :email)
            ON CONFLICT (email) DO UPDATE
            SET name = EXCLUDED.name,
                username = COALESCE("user".username, EXCLUDED.username)
            RETURNING id, email, name
            """
        ),
        {"name": name, "username": username, "email": email},
    ).mappings().one()

    resolved_email = row["email"]

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
            ALTER TABLE app.user_privileges
            ADD COLUMN IF NOT EXISTS creator BOOLEAN NOT NULL DEFAULT FALSE
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
    # Every newly registered account starts with base_user + editor access.
    # Keep existing records unchanged (e.g. if a creator removed privileges later on purpose).
    session.execute(
        text(
            """
            INSERT INTO app.user_privileges (email, base_user, editor)
            VALUES (:email, TRUE, TRUE)
            ON CONFLICT (email) DO NOTHING
            """
        ),
        {"email": resolved_email},
    )

    return int(row["id"]), resolved_email, row["name"]


def ensure_employee(
    session: Session,
    *,
    name: str,
    email: str,
) -> Tuple[int, str, str]:
    """
    Backward-compatible alias over app."user".
    """
    return ensure_user(
        session,
        user_identifier=email or name,
        display_name=name,
    )


def ensure_technician(
    session: Session,
    *,
    employee_identifier: str,
    display_name: Optional[str] = None,
) -> Tuple[int, str, str]:
    """
    Backward-compatible alias over app."user".
    """
    return ensure_user(
        session,
        user_identifier=employee_identifier,
        display_name=display_name,
    )


def lookup_technician_id_by_email(session: Session, email: Optional[str]) -> Optional[int]:
    """
    Backward-compatible lookup that resolves app."user".id by email.
    """
    email = (email or "").strip()
    if not email:
        return None
    return session.execute(
        text(
            """
            SELECT id
            FROM app."user"
            WHERE lower(email) = lower(:email)
            """
        ),
        {"email": email},
    ).scalar_one_or_none()


def lookup_radiologist_id_by_email(session: Session, email: Optional[str]) -> Optional[int]:
    """
    Backward-compatible lookup that resolves app."user".id by email.
    """
    return lookup_technician_id_by_email(session, email)


def lookup_employee_by_email(session: Session, email: Optional[str]) -> Optional[RowMapping]:
    """
    Backward-compatible lookup against app."user".
    """
    email = (email or "").strip()
    if not email:
        return None
    return session.execute(
        text(
            """
            SELECT id, email, name, username, is_active
            FROM app."user"
            WHERE lower(email) = lower(:email)
            """
        ),
        {"email": email},
    ).mappings().one_or_none()
