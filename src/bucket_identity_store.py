from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Dict, Iterable, List
from urllib.parse import quote, unquote

from src.gcs_storage import download_bytes, get_bucket, storage_client, upload_bytes

logger = logging.getLogger(__name__)

PRIVILEGE_FIELDS: tuple[str, ...] = ("base_user", "reviewer", "editor", "admin", "creator")
USERS_PREFIX = (os.getenv("RECETAS_USERS_PREFIX") or "app/users").strip("/ ")
PRIVILEGES_PREFIX = (os.getenv("RECETAS_PRIVILEGES_PREFIX") or "app/privileges").strip("/ ")
USER_PREFERENCES_PREFIX = (os.getenv("RECETAS_USER_PREFERENCES_PREFIX") or "app/user_preferences").strip("/ ")

RECETAS_VIEW_MODE_ICON = "icon"
RECETAS_VIEW_MODE_LIST = "list"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _normalize_email(email: str | None) -> str:
    return (email or "").strip().lower()


def _slug_code(value: str, default_prefix: str = "usuario") -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower()).strip("-")
    return normalized or default_prefix


def _email_blob_key(email: str) -> str:
    return quote(_normalize_email(email), safe="")


def _decode_email_blob_key(blob_name: str) -> str:
    stem = str(blob_name or "").strip().split("/")[-1]
    if stem.endswith(".json"):
        stem = stem[:-5]
    return _normalize_email(unquote(stem))


def _user_blob_name(email: str) -> str:
    return f"{USERS_PREFIX}/{_email_blob_key(email)}.json"


def _privileges_blob_name(email: str) -> str:
    return f"{PRIVILEGES_PREFIX}/{_email_blob_key(email)}.json"


def _user_preferences_blob_name(email: str) -> str:
    return f"{USER_PREFERENCES_PREFIX}/{_email_blob_key(email)}.json"


def _download_json_blob(blob_name: str) -> Dict[str, object] | None:
    try:
        payload = download_bytes(blob_name)
    except FileNotFoundError:
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("No se pudo descargar `%s`: %s", blob_name, exc)
        return None
    try:
        parsed = json.loads(payload.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("No se pudo parsear JSON `%s`: %s", blob_name, exc)
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def _upload_json_blob(blob_name: str, payload: Dict[str, object]) -> None:
    body = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    upload_bytes(
        body.encode("utf-8"),
        blob_name,
        content_type="application/json; charset=utf-8",
        cache_seconds=0,
    )


def _iter_json_blobs(prefix: str) -> Iterable[str]:
    normalized_prefix = str(prefix or "").strip().strip("/")
    if not normalized_prefix:
        return []
    client = storage_client()
    bucket = client.bucket(get_bucket().name)
    names: List[str] = []
    for blob in client.list_blobs(bucket, prefix=f"{normalized_prefix}/"):
        name = str(getattr(blob, "name", "") or "").strip()
        if not name or not name.endswith(".json"):
            continue
        names.append(name)
    names.sort()
    return names


def empty_privileges() -> Dict[str, bool]:
    return {name: False for name in PRIVILEGE_FIELDS}


def _is_truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "si", "sí"}


def _normalize_recetas_view_mode(value: object) -> str:
    if str(value or "").strip().lower() == RECETAS_VIEW_MODE_LIST:
        return RECETAS_VIEW_MODE_LIST
    return RECETAS_VIEW_MODE_ICON


def empty_user_preferences() -> Dict[str, object]:
    return {
        "recetas_view_mode": RECETAS_VIEW_MODE_ICON,
        "recetas_only_verified": True,
        "recetas_show_thermomix": True,
    }


def get_user_preferences(email: str | None) -> Dict[str, object]:
    normalized_email = _normalize_email(email)
    preferences = empty_user_preferences()
    if not normalized_email:
        return preferences

    raw = _download_json_blob(_user_preferences_blob_name(normalized_email)) or {}
    preferences["recetas_view_mode"] = _normalize_recetas_view_mode(raw.get("recetas_view_mode"))
    preferences["recetas_only_verified"] = _is_truthy(raw.get("recetas_only_verified", True))
    preferences["recetas_show_thermomix"] = _is_truthy(raw.get("recetas_show_thermomix", True))
    return preferences


def set_user_preferences(
    email: str,
    *,
    recetas_view_mode: object | None = None,
    recetas_only_verified: object | None = None,
    recetas_show_thermomix: object | None = None,
) -> Dict[str, object]:
    normalized_email = _normalize_email(email)
    if not normalized_email:
        raise ValueError("El correo es obligatorio para guardar preferencias.")

    current = get_user_preferences(normalized_email)
    if recetas_view_mode is not None:
        current["recetas_view_mode"] = _normalize_recetas_view_mode(recetas_view_mode)
    if recetas_only_verified is not None:
        current["recetas_only_verified"] = _is_truthy(recetas_only_verified)
    if recetas_show_thermomix is not None:
        current["recetas_show_thermomix"] = _is_truthy(recetas_show_thermomix)

    existing = _download_json_blob(_user_preferences_blob_name(normalized_email)) or {}
    created_at = str(existing.get("created_at") or "").strip() or _utc_now_iso()
    payload: Dict[str, object] = {
        "email": normalized_email,
        "created_at": created_at,
        "updated_at": _utc_now_iso(),
    }
    payload.update(current)
    _upload_json_blob(_user_preferences_blob_name(normalized_email), payload)
    return current


def get_user_privileges(email: str | None) -> Dict[str, bool]:
    normalized_email = _normalize_email(email)
    privileges = empty_privileges()
    if not normalized_email:
        return privileges

    raw = _download_json_blob(_privileges_blob_name(normalized_email)) or {}
    for key in PRIVILEGE_FIELDS:
        privileges[key] = bool(raw.get(key))
    return privileges


def set_user_privilege(email: str, privilege: str, enabled: bool) -> Dict[str, bool]:
    normalized_privilege = (privilege or "").strip().lower()
    if normalized_privilege not in PRIVILEGE_FIELDS:
        raise ValueError(f"Privilegio no compatible: {privilege}")
    current = get_user_privileges(email)
    current[normalized_privilege] = bool(enabled)
    set_user_privileges(email, **current)
    return current


def set_user_privileges(email: str, **flags: bool) -> Dict[str, bool]:
    normalized_email = _normalize_email(email)
    if not normalized_email:
        raise ValueError("El correo es obligatorio para guardar privilegios.")
    current = get_user_privileges(normalized_email)
    for key, value in flags.items():
        normalized_key = (key or "").strip().lower()
        if normalized_key in current:
            current[normalized_key] = bool(value)

    existing = _download_json_blob(_privileges_blob_name(normalized_email)) or {}
    created_at = str(existing.get("created_at") or "").strip() or _utc_now_iso()
    payload: Dict[str, object] = {
        "email": normalized_email,
        "created_at": created_at,
        "updated_at": _utc_now_iso(),
    }
    payload.update(current)
    _upload_json_blob(_privileges_blob_name(normalized_email), payload)
    return current


def disable_user_privileges(email: str, *, include_creator: bool = False) -> Dict[str, bool]:
    disable_map = {
        "base_user": False,
        "reviewer": False,
        "editor": False,
        "admin": False,
    }
    if include_creator:
        disable_map["creator"] = False
    return set_user_privileges(email, **disable_map)


def get_user_record(email: str | None) -> Dict[str, object] | None:
    normalized_email = _normalize_email(email)
    if not normalized_email:
        return None
    payload = _download_json_blob(_user_blob_name(normalized_email))
    if payload is None:
        return None
    result = dict(payload)
    result["email"] = normalized_email
    result["id"] = str(result.get("id") or normalized_email)
    result["user_id"] = str(result.get("user_id") or result["id"])
    result["username"] = str(result.get("username") or "").strip()
    result["name"] = str(result.get("name") or "").strip()
    result["is_active"] = bool(result.get("is_active", True))
    return result


def upsert_user_record(
    *,
    email: str,
    display_name: str = "",
    username: str = "",
    mark_login: bool = True,
) -> Dict[str, object]:
    normalized_email = _normalize_email(email)
    if not normalized_email:
        raise ValueError("El correo del usuario es obligatorio.")

    existing = get_user_record(normalized_email) or {}
    now_iso = _utc_now_iso()
    resolved_name = str(display_name or existing.get("name") or "").strip()
    if not resolved_name:
        resolved_name = normalized_email.split("@", 1)[0]
    resolved_username = str(username or existing.get("username") or "").strip()
    if not resolved_username:
        resolved_username = resolved_name

    payload: Dict[str, object] = {
        "id": str(existing.get("id") or normalized_email),
        "user_id": str(existing.get("user_id") or normalized_email),
        "user_code": str(existing.get("user_code") or _slug_code(normalized_email, default_prefix="usuario")),
        "email": normalized_email,
        "name": resolved_name,
        "username": resolved_username,
        "is_active": bool(existing.get("is_active", True)),
        "created_at": str(existing.get("created_at") or now_iso),
        "updated_at": now_iso,
    }
    if mark_login:
        payload["last_login_at"] = now_iso
    elif existing.get("last_login_at"):
        payload["last_login_at"] = existing.get("last_login_at")

    _upload_json_blob(_user_blob_name(normalized_email), payload)
    return payload


def set_user_active(email: str, is_active: bool) -> Dict[str, object]:
    record = upsert_user_record(email=email, mark_login=False)
    record["is_active"] = bool(is_active)
    record["updated_at"] = _utc_now_iso()
    _upload_json_blob(_user_blob_name(email), record)
    return record


def list_user_records() -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    for blob_name in _iter_json_blobs(USERS_PREFIX):
        payload = _download_json_blob(blob_name)
        if payload is None:
            continue
        row = dict(payload)
        email = _normalize_email(str(row.get("email") or "").strip() or _decode_email_blob_key(blob_name))
        if not email:
            continue
        row["email"] = email
        row["id"] = str(row.get("id") or email)
        row["user_id"] = str(row.get("user_id") or row["id"])
        row["name"] = str(row.get("name") or "").strip()
        row["username"] = str(row.get("username") or "").strip()
        row["is_active"] = bool(row.get("is_active", True))
        rows.append(row)
    rows.sort(key=lambda item: (str(item.get("name") or "").lower(), str(item.get("email") or "").lower()))
    return rows


def list_users_with_privileges() -> List[Dict[str, object]]:
    users_by_email: Dict[str, Dict[str, object]] = {
        _normalize_email(str(row.get("email") or "")): dict(row)
        for row in list_user_records()
        if _normalize_email(str(row.get("email") or ""))
    }

    privileges_by_email: Dict[str, Dict[str, bool]] = {}
    for blob_name in _iter_json_blobs(PRIVILEGES_PREFIX):
        payload = _download_json_blob(blob_name)
        if payload is None:
            continue
        email = _normalize_email(str(payload.get("email") or "").strip() or _decode_email_blob_key(blob_name))
        if not email:
            continue
        flags = empty_privileges()
        for key in PRIVILEGE_FIELDS:
            flags[key] = bool(payload.get(key))
        privileges_by_email[email] = flags

    all_emails = sorted(set(users_by_email) | set(privileges_by_email))
    merged: List[Dict[str, object]] = []
    for email in all_emails:
        user_row = users_by_email.get(email) or {}
        flags = privileges_by_email.get(email) or empty_privileges()
        name = str(user_row.get("name") or "").strip() or email.split("@", 1)[0]
        username = str(user_row.get("username") or "").strip() or name
        row: Dict[str, object] = {
            "id": str(user_row.get("id") or email),
            "name": name,
            "username": username,
            "email": email,
            "is_active": bool(user_row.get("is_active", any(flags.values()))),
        }
        row.update(flags)
        merged.append(row)

    merged.sort(key=lambda item: (str(item.get("name") or "").lower(), str(item.get("email") or "").lower()))
    return merged
