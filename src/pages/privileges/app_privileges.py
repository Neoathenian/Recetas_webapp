from __future__ import annotations

import html
import json
import logging
import os
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

import gradio as gr

from src.bucket_identity_store import (
    PRIVILEGE_FIELDS as BUCKET_PRIVILEGE_FIELDS,
    get_user_privileges,
    list_users_with_privileges,
    set_user_active,
    set_user_privilege,
)
from src.login_logic import get_user
from src.pages.header import render_header, with_light_mode_head
from src.page_timing import timed_page_load
logger = logging.getLogger(__name__)

ASSETS_DIR = Path(__file__).resolve().parent
CSS_DIR = ASSETS_DIR / "css"
JS_DIR = ASSETS_DIR / "js"

PRIVILEGE_FIELDS: Sequence[str] = tuple(BUCKET_PRIVILEGE_FIELDS)
PRIVILEGE_LABELS: Dict[str, str] = {
    "base_user": "Usuario base",
    "reviewer": "Revisor",
    "editor": "Editor",
    "admin": "Administrador",
    "creator": "Creador",
}
TRUE_VALUES = {"1", "true", "yes", "on"}

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
    return [dict(row) for row in list_users_with_privileges()]


def _summary_for_rows(rows: Sequence[Dict[str, object]]) -> str:
    count = len(rows)
    suffix = "" if count == 1 else "s"
    return f"{count} usuario{suffix} listado{suffix}."


def _render_privilege_button(email: str | None, privilege: str, enabled: bool) -> str:
    safe_privilege = html.escape(privilege, quote=True)
    email_value = (email or "").strip()
    email_attr = html.escape(email_value, quote=True)
    label = "SÍ" if enabled else "NO"
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
        return "<span class='priv-missing'>Sin ID</span>"
    email_attr = html.escape((email or "").strip(), quote=True)
    state_attr = "true" if is_active else "false"
    state_class = "is-on" if is_active else "is-off"
    icon = "✓" if is_active else "✕"
    label = "Activo" if is_active else "Inactivo"
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
    headers = ["Nombre", "Usuario", "Correo", "Activo"] + [PRIVILEGE_LABELS[key] for key in visible_fields]
    header_cells = "".join(f"<th scope='col'>{html.escape(label)}</th>" for label in headers)
    body_rows: List[str] = []
    for row in rows:
        name = html.escape(str(row.get("name") or ""))
        username = html.escape(str(row.get("username") or ""))
        email = (row.get("email") or "").strip()
        email_display = (
            html.escape(email)
            if email
            else "<span class='priv-missing'>Sin correo</span>"
        )
        cells = [
            f"<td class='priv-col-name'><div class='priv-name'>{name or '—'}</div></td>",
            f"<td class='priv-col-username'>{username or '—'}</td>",
            f"<td class='priv-col-email'>{email_display}</td>",
            "<td class='priv-col-active'>{button}</td>".format(
                button=_render_active_toggle(row.get("email") or row.get("id"), bool(row.get("is_active")), row.get("email"))
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
            "<tr class='priv-empty'><td colspan='{cols}'>No hay usuarios registrados.</td></tr>".format(
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


def _set_user_active_state(email: str, is_active: bool, *, can_manage_creator: bool) -> str:
    email_value = (email or "").strip().lower()
    if not email_value:
        raise ValueError("Falta el correo del usuario.")

    privileges = get_user_privileges(email_value)
    if bool(privileges.get("creator")) and not can_manage_creator:
        raise PermissionError("Solo un usuario creador puede modificar usuarios con privilegio de creador.")

    set_user_active(email_value, bool(is_active))
    if not bool(is_active):
        # Al desactivar, retiramos privilegios operativos.
        set_user_privilege(email_value, "base_user", False)
        set_user_privilege(email_value, "reviewer", False)
        set_user_privilege(email_value, "editor", False)
        set_user_privilege(email_value, "admin", False)
        if can_manage_creator:
            set_user_privilege(email_value, "creator", False)

    state_label = "activado" if is_active else "desactivado"
    return f"Usuario {email_value} {state_label}"


def _apply_privilege_change(email: str, privilege: str, enabled: bool, *, can_manage_creator: bool) -> None:
    normalized = (privilege or "").strip().lower()
    if normalized not in PRIVILEGE_FIELDS:
        raise ValueError(f"Privilegio no compatible: {privilege}")
    if normalized == "creator" and not can_manage_creator:
        raise PermissionError("Solo un usuario creador puede modificar el privilegio de creador.")
    email_value = (email or "").strip()
    if not email_value:
        raise ValueError("El usuario no tiene correo registrado.")
    set_user_privilege(email_value, normalized, bool(enabled))


def _handle_refresh(request: gr.Request):
    can_manage_privileges, can_manage_creator = _request_role_flags(request)
    if not can_manage_privileges:
        return "", "❌ No tienes permiso para gestionar privilegios."
    html_table, summary = _load_table_payload(can_manage_creator=can_manage_creator)
    return html_table, f"ℹ️ {summary}"


def _handle_toggle(payload_json: str, request: gr.Request):
    can_manage_privileges, can_manage_creator = _request_role_flags(request)
    if not can_manage_privileges:
        return "", "❌ No tienes permiso para gestionar privilegios."
    if not payload_json:
        table_html, summary = _load_table_payload(can_manage_creator=can_manage_creator)
        return table_html, f"⚠️ No se recibieron cambios. {summary}"
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
        label = PRIVILEGE_LABELS.get(normalized_privilege, normalized_privilege.title() or "Privilegio")
        state = "SÍ" if next_state else "NO"
        return html_table, f"✅ {label} para {email} = {state}. {summary}"
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to toggle privilege flag")
        html_table, summary = _load_table_payload(can_manage_creator=can_manage_creator)
        return html_table, f"❌ La actualización falló: {exc}. {summary}"


def _handle_active_toggle(payload_json: str, request: gr.Request):
    can_manage_privileges, can_manage_creator = _request_role_flags(request)
    if not can_manage_privileges:
        return "", "❌ No tienes permiso para gestionar privilegios."
    if not payload_json:
        table_html, summary = _load_table_payload(can_manage_creator=can_manage_creator)
        return table_html, f"⚠️ No se recibieron cambios. {summary}"
    try:
        payload = json.loads(payload_json)
        row_id = payload.get("rowId")
        email = (payload.get("email") or "").strip()
        next_state = payload.get("nextState")
        target_email = email or str(row_id or "").strip()
        if not target_email:
            raise ValueError("Falta el correo del usuario.")
        if next_state is None:
            raise ValueError("Falta el estado de destino.")
        message = _set_user_active_state(target_email, bool(next_state), can_manage_creator=can_manage_creator)
        html_table, summary = _load_table_payload(can_manage_creator=can_manage_creator)
        return html_table, f"✅ {message}. {summary}"
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to toggle user active state")
        html_table, summary = _load_table_payload(can_manage_creator=can_manage_creator)
        return html_table, f"❌ La actualización falló: {exc}. {summary}"


def make_privileges_app() -> gr.Blocks:
    stylesheet = _load_privileges_css()
    table_js = with_light_mode_head(_load_privileges_js())
    with gr.Blocks(
        title="Consola de privilegios",
        css=stylesheet or None,
        head=table_js,
    ) as app:
        hdr = gr.HTML()
        app.load(timed_page_load("/privileges", _header_privileges), outputs=[hdr])

        with gr.Column(elem_id="privileges-shell"):
            gr.Markdown("## Privilegios de usuario", elem_id="privileges-title")
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
