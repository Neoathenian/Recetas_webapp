from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict

import gradio as gr

from src.bucket_identity_store import (
    RECETAS_VIEW_MODE_ICON,
    RECETAS_VIEW_MODE_LIST,
    get_user_preferences,
    set_user_preferences,
)
from src.login_logic import get_user
from src.page_timing import timed_page_load
from src.pages.header import render_header, with_light_mode_head

logger = logging.getLogger(__name__)

ASSETS_DIR = Path(__file__).resolve().parent
CSS_PATH = ASSETS_DIR / "css" / "profile_page.css"


def _read_asset(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning("Missing profile asset at %s", path)
        return ""


def _load_css() -> str:
    return _read_asset(CSS_PATH)


def _header_profile(request: gr.Request):
    return render_header(path="/profile", request=request)


def _normalize_view_mode(value: object) -> str:
    normalized = str(value or "").strip().lower()
    if normalized == RECETAS_VIEW_MODE_LIST:
        return RECETAS_VIEW_MODE_LIST
    return RECETAS_VIEW_MODE_ICON


def _view_mode_button_updates(current_mode: object, *, interactive: bool = True) -> tuple[gr.update, gr.update]:
    normalized = _normalize_view_mode(current_mode)
    is_icon = normalized == RECETAS_VIEW_MODE_ICON
    return (
        gr.update(value="Iconos", variant="primary" if is_icon else "secondary", interactive=interactive),
        gr.update(value="Lista", variant="primary" if not is_icon else "secondary", interactive=interactive),
    )


def _resolve_user_email(request: gr.Request | None) -> str:
    user = get_user(request, refresh_privileges=False) or {}
    return str(user.get("email") or "").strip().lower()


def _settings_state(
    view_mode: object,
    only_verified: object,
    show_thermomix: object,
) -> Dict[str, object]:
    return {
        "recetas_view_mode": _normalize_view_mode(view_mode),
        "recetas_only_verified": bool(only_verified),
        "recetas_show_thermomix": bool(show_thermomix),
    }


def _load_profile_settings(request: gr.Request):
    email = _resolve_user_email(request)
    if not email:
        base_state = _settings_state(RECETAS_VIEW_MODE_ICON, True, True)
        icon_update, list_update = _view_mode_button_updates(RECETAS_VIEW_MODE_ICON, interactive=False)
        return (
            RECETAS_VIEW_MODE_ICON,
            icon_update,
            list_update,
            gr.update(value=True, interactive=False),
            gr.update(value=True, interactive=False),
            "Inicia sesión para editar tu perfil.",
            base_state,
            False,
        )

    preferences = get_user_preferences(email)
    view_mode = _normalize_view_mode(preferences.get("recetas_view_mode"))
    only_verified = bool(preferences.get("recetas_only_verified"))
    show_thermomix = bool(preferences.get("recetas_show_thermomix"))
    current_state = _settings_state(view_mode, only_verified, show_thermomix)
    icon_update, list_update = _view_mode_button_updates(view_mode, interactive=True)
    return (
        view_mode,
        icon_update,
        list_update,
        gr.update(value=only_verified, interactive=True),
        gr.update(value=show_thermomix, interactive=True),
        (
            f"Preferencias cargadas para {email}. "
            "Se guardan automáticamente."
        ),
        current_state,
        True,
    )


def _auto_save_profile_settings(
    recetas_view_mode: object,
    recetas_only_verified: object,
    recetas_show_thermomix: object,
    current_state: Dict[str, object] | None,
    autosave_enabled: bool,
    request: gr.Request,
):
    previous_state = current_state or _settings_state(RECETAS_VIEW_MODE_ICON, True, True)
    next_state = _settings_state(
        recetas_view_mode,
        recetas_only_verified,
        recetas_show_thermomix,
    )
    if not autosave_enabled:
        return previous_state, gr.update()

    if (
        _normalize_view_mode(previous_state.get("recetas_view_mode")) == _normalize_view_mode(next_state.get("recetas_view_mode"))
        and bool(previous_state.get("recetas_only_verified")) == bool(next_state.get("recetas_only_verified"))
        and bool(previous_state.get("recetas_show_thermomix", True)) == bool(next_state.get("recetas_show_thermomix"))
    ):
        return next_state, gr.update()

    email = _resolve_user_email(request)
    if not email:
        return previous_state, "Inicia sesión para guardar cambios."

    preferences = set_user_preferences(
        email,
        recetas_view_mode=next_state["recetas_view_mode"],
        recetas_only_verified=next_state["recetas_only_verified"],
        recetas_show_thermomix=next_state["recetas_show_thermomix"],
    )

    request_obj = getattr(request, "request", request)
    session = getattr(request_obj, "session", None)
    if session is not None and hasattr(session, "get"):
        session_user = session.get("user")
        if isinstance(session_user, dict):
            session_user["preferences"] = dict(preferences)
            session["user"] = session_user

    saved_state = _settings_state(
        preferences.get("recetas_view_mode"),
        preferences.get("recetas_only_verified"),
        preferences.get("recetas_show_thermomix"),
    )
    mode_label = "Lista" if saved_state.get("recetas_view_mode") == RECETAS_VIEW_MODE_LIST else "Iconos"
    solo_label = "activo" if bool(saved_state.get("recetas_only_verified")) else "inactivo"
    thermomix_label = "visible" if bool(saved_state.get("recetas_show_thermomix")) else "oculta"
    return (
        saved_state,
        (
            f"Guardado automático. Vista: {mode_label}. Solo: {solo_label}. "
            f"Thermomix: {thermomix_label}."
        ),
    )


def _set_profile_view_mode(
    target_mode: str,
    recetas_only_verified: object,
    recetas_show_thermomix: object,
    current_state: Dict[str, object] | None,
    autosave_enabled: bool,
    request: gr.Request,
):
    normalized_mode = _normalize_view_mode(target_mode)
    icon_update, list_update = _view_mode_button_updates(normalized_mode, interactive=bool(autosave_enabled))
    saved_state, status = _auto_save_profile_settings(
        normalized_mode,
        recetas_only_verified,
        recetas_show_thermomix,
        current_state,
        autosave_enabled,
        request,
    )
    return normalized_mode, icon_update, list_update, saved_state, status


def _set_icon_view(
    recetas_only_verified: object,
    recetas_show_thermomix: object,
    current_state: Dict[str, object] | None,
    autosave_enabled: bool,
    request: gr.Request,
):
    return _set_profile_view_mode(
        RECETAS_VIEW_MODE_ICON,
        recetas_only_verified,
        recetas_show_thermomix,
        current_state,
        autosave_enabled,
        request,
    )


def _set_list_view(
    recetas_only_verified: object,
    recetas_show_thermomix: object,
    current_state: Dict[str, object] | None,
    autosave_enabled: bool,
    request: gr.Request,
):
    return _set_profile_view_mode(
        RECETAS_VIEW_MODE_LIST,
        recetas_only_verified,
        recetas_show_thermomix,
        current_state,
        autosave_enabled,
        request,
    )


def _on_recipe_settings_change(
    recetas_only_verified: object,
    recetas_show_thermomix: object,
    current_view_mode: object,
    current_state: Dict[str, object] | None,
    autosave_enabled: bool,
    request: gr.Request,
):
    return _auto_save_profile_settings(
        current_view_mode,
        recetas_only_verified,
        recetas_show_thermomix,
        current_state,
        autosave_enabled,
        request,
    )


def make_profile_app() -> gr.Blocks:
    stylesheet = _load_css()
    with gr.Blocks(
        title="Perfil",
        css=stylesheet or None,
        head=with_light_mode_head(None),
    ) as app:
        hdr = gr.HTML()
        view_mode_state = gr.State(RECETAS_VIEW_MODE_ICON)
        settings_state = gr.State(_settings_state(RECETAS_VIEW_MODE_ICON, True, True))
        autosave_enabled_state = gr.State(False)

        with gr.Column(elem_id="profile-shell"):
            gr.HTML(
                """
                <section id="profile-head">
                  <p class="profile-head__kicker">Ajustes personales</p>
                  <h2 class="profile-head__title">Diseña tu inicio</h2>
                  <p class="profile-head__desc">
                    Elige cómo quieres abrir Recetas y si prefieres arrancar con el filtro Solo.
                  </p>
                </section>
                """.strip()
            )

            with gr.Column(elem_id="profile-panel"):
                gr.HTML(
                    """
                    <div class="profile-panel__header">
                      <h3>Recetas</h3>
                      <p>Selecciona la vista inicial para tu navegación diaria.</p>
                    </div>
                    """.strip()
                )
                with gr.Row(elem_id="profile-view-buttons"):
                    view_icon_btn = gr.Button(
                        "Iconos",
                        variant="primary",
                        elem_id="profile-view-icon-btn",
                        min_width=120,
                    )
                    view_list_btn = gr.Button(
                        "Lista",
                        variant="secondary",
                        elem_id="profile-view-list-btn",
                        min_width=120,
                    )

                recetas_only_verified = gr.Checkbox(
                    value=True,
                    label="Iniciar con `Solo` activado (solo recetas verificadas)",
                    interactive=True,
                    elem_id="profile-recetas-only-verified",
                )
                recetas_show_thermomix = gr.Checkbox(
                    value=True,
                    label="Mostrar recetas thermomix",
                    interactive=True,
                    elem_id="profile-recetas-show-thermomix",
                )

            status_md = gr.Markdown("", elem_id="profile-status")

        app.load(timed_page_load("/profile", _header_profile), outputs=[hdr])
        app.load(
            timed_page_load("/profile", _load_profile_settings),
            outputs=[
                view_mode_state,
                view_icon_btn,
                view_list_btn,
                recetas_only_verified,
                recetas_show_thermomix,
                status_md,
                settings_state,
                autosave_enabled_state,
            ],
        )

        view_icon_btn.click(
            timed_page_load("/profile", _set_icon_view, label="profile_set_icon_view"),
            inputs=[
                recetas_only_verified,
                recetas_show_thermomix,
                settings_state,
                autosave_enabled_state,
            ],
            outputs=[view_mode_state, view_icon_btn, view_list_btn, settings_state, status_md],
            show_progress=False,
        )
        view_list_btn.click(
            timed_page_load("/profile", _set_list_view, label="profile_set_list_view"),
            inputs=[
                recetas_only_verified,
                recetas_show_thermomix,
                settings_state,
                autosave_enabled_state,
            ],
            outputs=[view_mode_state, view_icon_btn, view_list_btn, settings_state, status_md],
            show_progress=False,
        )

        recipe_settings_handler = timed_page_load(
            "/profile",
            _on_recipe_settings_change,
            label="profile_change_recipe_settings",
        )
        recipe_settings_inputs = [
            recetas_only_verified,
            recetas_show_thermomix,
            view_mode_state,
            settings_state,
            autosave_enabled_state,
        ]
        recetas_only_verified.change(
            recipe_settings_handler,
            inputs=recipe_settings_inputs,
            outputs=[settings_state, status_md],
            show_progress=False,
        )
        recetas_show_thermomix.change(
            timed_page_load(
                "/profile",
                _on_recipe_settings_change,
                label="profile_change_show_thermomix",
            ),
            inputs=recipe_settings_inputs,
            outputs=[settings_state, status_md],
            show_progress=False,
        )

    return app
