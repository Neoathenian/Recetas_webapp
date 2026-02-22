from __future__ import annotations

from typing import Dict

from src.bucket_identity_store import disable_user_privileges, get_user_privileges


def _normalize_email(email: str | None) -> str:
    return (email or "").strip().lower()


def get_local_role_flags(email: str | None) -> Dict[str, bool]:
    """
    Backward-compatible helper name.
    Role flags are sourced from bucket-backed privilege documents.
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
    return get_user_privileges(normalized_email)


def report_and_disable_user(email: str | None, reported_by: str | None, reason: str | None) -> bool:
    normalized_email = _normalize_email(email)
    if not normalized_email:
        return False
    _ = reported_by
    _ = reason
    disable_user_privileges(normalized_email, include_creator=False)
    return True

