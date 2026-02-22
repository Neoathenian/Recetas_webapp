from __future__ import annotations

import html
import json
import logging
import os
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

import gradio as gr
from sqlalchemy import text

from src.db import session_scope
from src.login_logic import get_user
from src.pages.header import render_header, with_light_mode_head
from src.page_timing import timed_page_load
logger = logging.getLogger(__name__)

ASSETS_DIR = Path(__file__).resolve().parent
CSS_DIR = ASSETS_DIR / "css"
JS_DIR = ASSETS_DIR / "js"

PRIVILEGE_FIELDS: Sequence[str] = ("base_user", "reviewer", "editor", "admin", "creator")
PRIVILEGE_LABELS: Dict[str, str] = {
    "base_user": "Base User",
    "reviewer": "Reviewer",
    "editor": "Editor",
    "admin": "Admin",
    "creator": "Creator",
}
TRUE_VALUES = {"1", "true", "yes", "on"}

_PRIVILEGE_QUERY = text(
    """
    SELECT
        u.id,
        u.name,
        u.username,
        u.email,
        u.is_active,
        COALESCE(p.base_user, FALSE)        AS base_user,
        COALESCE(p.reviewer, FALSE)         AS reviewer,
        COALESCE((to_jsonb(p) ->> 'editor')::boolean, FALSE) AS editor,
        COALESCE(
            (to_jsonb(p) ->> 'admin')::boolean,
            FALSE
        ) AS admin,
        COALESCE((to_jsonb(p) ->> 'creator')::boolean, FALSE) AS creator
    FROM app."user" u
    LEFT JOIN app.user_privileges p
        ON lower(p.email) = lower(u.email)
    ORDER BY u.name
    """
)


def _ensure_privilege_columns(session) -> None:
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


def _read_asset(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning("Missing privileges asset at %s", path)
        return ""


def _load_privileges_css() -> str:
    return _read_asset(CSS_DIR / "privileges_page.css")


def _load_privileges_js() -> str:
    script = _read_asset(JS_DIR / "privileges_table.js")
    if not script:
        return ""
    return f"<script>\n{script}\n</script>"


def _header_privileges(request: gr.Request):
    return render_header(path="/privileges", request=request)


def _is_truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in TRUE_VALUES


def _request_role_flags(request: gr.Request | None) -> tuple[bool, bool]:
    if str(os.getenv("THELIST_DEBUG_PRIVILEGES", "")).strip().lower() in TRUE_VALUES:
        # Debug pages mode should bypass role gates on this console.
        return True, True

    user = get_user(request) or {}
    privileges = user.get("privileges")
    if not isinstance(privileges, dict):
        privileges = {}
    can_manage_creator = _is_truthy(privileges.get("creator"))
    can_manage_privileges = (
        can_manage_creator
        or _is_truthy(privileges.get("admin"))
        or _is_truthy(privileges.get("reviewer_creator"))
    )
    return can_manage_privileges, can_manage_creator


def _visible_privilege_fields(can_manage_creator: bool) -> tuple[str, ...]:
    if can_manage_creator:
        return tuple(PRIVILEGE_FIELDS)
    return tuple(key for key in PRIVILEGE_FIELDS if key != "creator")


def _fetch_privilege_rows() -> List[Dict[str, object]]:
    with session_scope() as session:
        _ensure_privilege_columns(session)
        rows = session.execute(_PRIVILEGE_QUERY).mappings().all()
    return [dict(row) for row in rows]


def _summary_for_rows(rows: Sequence[Dict[str, object]]) -> str:
    count = len(rows)
    suffix = "" if count == 1 else "s"
    return f"{count} user{suffix} listed."


def _render_privilege_button(email: str | None, privilege: str, enabled: bool) -> str:
    safe_privilege = html.escape(privilege, quote=True)
    email_value = (email or "").strip()
    email_attr = html.escape(email_value, quote=True)
    label = "TRUE" if enabled else "FALSE"
    state_attr = "true" if enabled else "false"
    classes = ["priv-flag", f"is-{state_attr}"]
    disabled_attr = ""
    if not email_value:
        classes.append("is-disabled")
        disabled_attr = " disabled aria-disabled='true'"
    return (
        "<button type='button' class='{classes}' data-privilege='{priv}' "
        "data-state='{state}' data-email='{email}' aria-pressed='{state}'{disabled}>"
        "{label}"
        "</button>"
    ).format(
        classes=" ".join(classes),
        priv=safe_privilege,
        state=state_attr,
        email=email_attr,
        disabled=disabled_attr,
        label=label,
    )


def _render_active_toggle(row_id: object, is_active: bool, email: str | None) -> str:
    if row_id in (None, ""):
        return "<span class='priv-missing'>No ID</span>"
    email_attr = html.escape((email or "").strip(), quote=True)
    state_attr = "true" if is_active else "false"
    state_class = "is-on" if is_active else "is-off"
    icon = "✓" if is_active else "✕"
    label = "Active" if is_active else "Inactive"
    return (
        "<button type='button' class='priv-active-toggle {cls}' data-row-id='{row}' "
        "data-state='{state}' data-email='{email}' aria-pressed='{state}'>"
        "<span class='toggle-track'><span class='toggle-thumb'>{icon}</span></span>"
        "<span class='sr-only'>{label}</span>"
        "</button>"
    ).format(
        cls=state_class,
        row=html.escape(str(row_id), quote=True),
        state=state_attr,
        email=email_attr,
        icon=icon,
        label=label,
    )


def _render_privileges_table(rows: Sequence[Dict[str, object]], *, visible_fields: Sequence[str]) -> str:
    headers = ["Name", "Username", "Email", "Active"] + [PRIVILEGE_LABELS[key] for key in visible_fields]
    header_cells = "".join(f"<th scope='col'>{html.escape(label)}</th>" for label in headers)
    body_rows: List[str] = []
    for row in rows:
        name = html.escape(str(row.get("name") or ""))
        username = html.escape(str(row.get("username") or ""))
        email = (row.get("email") or "").strip()
        email_display = (
            html.escape(email)
            if email
            else "<span class='priv-missing'>No email</span>"
        )
        cells = [
            f"<td class='priv-col-name'><div class='priv-name'>{name or '—'}</div></td>",
            f"<td class='priv-col-username'>{username or '—'}</td>",
            f"<td class='priv-col-email'>{email_display}</td>",
            "<td class='priv-col-active'>{button}</td>".format(
                button=_render_active_toggle(row.get("id"), bool(row.get("is_active")), row.get("email"))
            ),
        ]
        for key in visible_fields:
            enabled = bool(row.get(key))
            cells.append(
                "<td class='priv-col-flag'>{button}</td>".format(
                    button=_render_privilege_button(email, key, enabled)
                )
            )
        body_rows.append(f"<tr>{''.join(cells)}</tr>")

    if not body_rows:
        body_rows.append(
            "<tr class='priv-empty'><td colspan='{cols}'>No users registered.</td></tr>".format(
                cols=4 + len(visible_fields)
            )
        )

    return f"""
    <div class="priv-table-wrapper">
      <table>
        <thead>
          <tr>{header_cells}</tr>
        </thead>
        <tbody>
          {''.join(body_rows)}
        </tbody>
      </table>
    </div>
    """


def _load_table_payload(*, can_manage_creator: bool) -> Tuple[str, str]:
    rows = _fetch_privilege_rows()
    html_table = _render_privileges_table(rows, visible_fields=_visible_privilege_fields(can_manage_creator))
    summary = _summary_for_rows(rows)
    return html_table, summary


def _upsert_privileges(session, email: str, **flags: bool) -> None:
    email_value = (email or "").strip()
    if not email_value:
        return
    _ensure_privilege_columns(session)
    filtered = {key: bool(value) for key, value in flags.items() if key in PRIVILEGE_FIELDS}
    if not filtered:
        return
    assignments = [f'{col} = :{col}' for col in filtered]
    params = {"email": email_value, **filtered}
    insert_cols = ["email", *filtered.keys()]
    insert_vals = [":email", *(f":{col}" for col in filtered)]
    result = session.execute(
        text(
            f"""
            UPDATE app.user_privileges
            SET {', '.join(assignments)}
            WHERE lower(email) = lower(:email)
            """
        ),
        params,
    )
    if result.rowcount == 0:
        session.execute(
            text(
                f"""
                INSERT INTO app.user_privileges ({', '.join(insert_cols)})
                VALUES ({', '.join(insert_vals)})
                """
            ),
            params,
        )


def _sync_user_state_from_privileges(session, email: str) -> None:
    email_value = (email or "").strip()
    if not email_value:
        return

    priv_row = session.execute(
        text(
            """
            SELECT
                p.base_user,
                p.reviewer,
                COALESCE((to_jsonb(p) ->> 'editor')::boolean, FALSE) AS editor,
                COALESCE(
                    (to_jsonb(p) ->> 'admin')::boolean,
                    FALSE
                ) AS admin,
                COALESCE((to_jsonb(p) ->> 'creator')::boolean, FALSE) AS creator
            FROM app.user_privileges p
            WHERE lower(p.email) = lower(:email)
            """
        ),
        {"email": email_value},
    ).mappings().one_or_none()
    if priv_row is None:
        return

    user_row = session.execute(
        text(
            """
            SELECT id
            FROM app."user"
            WHERE lower(email) = lower(:email)
            """
        ),
        {"email": email_value},
    ).mappings().one_or_none()
    if not user_row:
        return

    user_id = user_row["id"]
    should_activate = any(bool(priv_row.get(key)) for key in PRIVILEGE_FIELDS)
    session.execute(
        text('UPDATE app."user" SET is_active = :state WHERE id = :user_id'),
        {"state": should_activate, "user_id": user_id},
    )


def _set_user_active_state(user_id: int, is_active: bool, *, can_manage_creator: bool) -> str:
    with session_scope() as session:
        row = session.execute(
            text(
                """
                SELECT
                    u.name,
                    u.email,
                    COALESCE(
                        (to_jsonb(p) ->> 'creator')::boolean,
                        FALSE
                    ) AS creator
                FROM app."user"
                u
                LEFT JOIN app.user_privileges p
                    ON lower(p.email) = lower(u.email)
                WHERE u.id = :user_id
                """
            ),
            {"user_id": user_id},
        ).mappings().one_or_none()
        if not row:
            raise ValueError(f"User {user_id} not found.")
        if bool(row.get("creator")) and not can_manage_creator:
            raise PermissionError("Only a creator user can modify users with the creator privilege.")

        email_value = (row.get("email") or "").strip()
        session.execute(
            text('UPDATE app."user" SET is_active = :state WHERE id = :user_id'),
            {"state": is_active, "user_id": user_id},
        )
        if email_value:
            if not is_active:
                disable_flags = {
                    "base_user": False,
                    "reviewer": False,
                    "editor": False,
                    "admin": False,
                }
                if can_manage_creator:
                    disable_flags["creator"] = False
                _upsert_privileges(session, email_value, **disable_flags)
        name = row.get("name") or user_id
        state_label = "activated" if is_active else "deactivated"
        return f"User {name} {state_label}"


def _apply_privilege_change(email: str, privilege: str, enabled: bool, *, can_manage_creator: bool) -> None:
    normalized = (privilege or "").strip().lower()
    if normalized not in PRIVILEGE_FIELDS:
        raise ValueError(f"Unsupported privilege: {privilege}")
    if normalized == "creator" and not can_manage_creator:
        raise PermissionError("Only a creator user can modify the creator privilege.")
    email_value = (email or "").strip()
    if not email_value:
        raise ValueError("The user has no registered email.")

    stmt = text(
        f"""
        INSERT INTO app.user_privileges (email, "{normalized}")
        VALUES (:email, :flag)
        ON CONFLICT (email) DO UPDATE SET "{normalized}" = EXCLUDED."{normalized}"
        """
    )
    with session_scope() as session:
        _ensure_privilege_columns(session)
        session.execute(stmt, {"email": email_value, "flag": bool(enabled)})
        _sync_user_state_from_privileges(session, email_value)


def _handle_refresh(request: gr.Request):
    can_manage_privileges, can_manage_creator = _request_role_flags(request)
    if not can_manage_privileges:
        return "", "❌ You do not have permission to manage privileges."
    html_table, summary = _load_table_payload(can_manage_creator=can_manage_creator)
    return html_table, f"ℹ️ {summary}"


def _handle_toggle(payload_json: str, request: gr.Request):
    can_manage_privileges, can_manage_creator = _request_role_flags(request)
    if not can_manage_privileges:
        return "", "❌ You do not have permission to manage privileges."
    if not payload_json:
        table_html, summary = _load_table_payload(can_manage_creator=can_manage_creator)
        return table_html, f"⚠️ No changes were received. {summary}"
    try:
        payload = json.loads(payload_json)
        privilege = payload.get("privilege")
        email = payload.get("email")
        next_state = payload.get("nextState")
        if not isinstance(next_state, bool):
            next_state = bool(next_state)
        _apply_privilege_change(email, privilege, next_state, can_manage_creator=can_manage_creator)
        html_table, summary = _load_table_payload(can_manage_creator=can_manage_creator)
        normalized_privilege = (privilege or "").strip().lower()
        label = PRIVILEGE_LABELS.get(normalized_privilege, normalized_privilege.title() or "Privilege")
        state = "TRUE" if next_state else "FALSE"
        return html_table, f"✅ {label} for {email} = {state}. {summary}"
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to toggle privilege flag")
        html_table, summary = _load_table_payload(can_manage_creator=can_manage_creator)
        return html_table, f"❌ Update failed: {exc}. {summary}"


def _handle_active_toggle(payload_json: str, request: gr.Request):
    can_manage_privileges, can_manage_creator = _request_role_flags(request)
    if not can_manage_privileges:
        return "", "❌ You do not have permission to manage privileges."
    if not payload_json:
        table_html, summary = _load_table_payload(can_manage_creator=can_manage_creator)
        return table_html, f"⚠️ No changes were received. {summary}"
    try:
        payload = json.loads(payload_json)
        row_id = payload.get("rowId")
        next_state = payload.get("nextState")
        if row_id in (None, ""):
            raise ValueError("Missing user identifier.")
        if next_state is None:
            raise ValueError("Missing target state.")
        message = _set_user_active_state(int(row_id), bool(next_state), can_manage_creator=can_manage_creator)
        html_table, summary = _load_table_payload(can_manage_creator=can_manage_creator)
        return html_table, f"✅ {message}. {summary}"
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to toggle user active state")
        html_table, summary = _load_table_payload(can_manage_creator=can_manage_creator)
        return html_table, f"❌ Update failed: {exc}. {summary}"


def make_privileges_app() -> gr.Blocks:
    stylesheet = _load_privileges_css()
    table_js = with_light_mode_head(_load_privileges_js())
    with gr.Blocks(
        title="Privileges Console",
        css=stylesheet or None,
        head=table_js,
    ) as app:
        hdr = gr.HTML()
        app.load(timed_page_load("/privileges", _header_privileges), outputs=[hdr])

        with gr.Column(elem_id="privileges-shell"):
            gr.Markdown("## User Privileges", elem_id="privileges-title")
            table_html = gr.HTML(elem_id="privileges-table")
            status_md = gr.Markdown("", elem_id="privileges-status")

        toggle_payload = gr.Textbox(
            value="",
            show_label=False,
            interactive=False,
            visible=False,
            elem_id="priv-toggle-payload",
        )
        toggle_trigger = gr.Button(
            "_toggle",
            visible=False,
            elem_id="priv-toggle-trigger",
        )
        active_payload = gr.Textbox(
            value="",
            show_label=False,
            interactive=False,
            visible=False,
            elem_id="priv-active-payload",
        )
        active_trigger = gr.Button(
            "_toggle_active",
            visible=False,
            elem_id="priv-active-trigger",
        )

        app.load(timed_page_load("/privileges", _handle_refresh), outputs=[table_html, status_md])
        toggle_trigger.click(
            _handle_toggle,
            inputs=[toggle_payload],
            outputs=[table_html, status_md],
        )
        active_trigger.click(
            _handle_active_toggle,
            inputs=[active_payload],
            outputs=[table_html, status_md],
        )

    return app
