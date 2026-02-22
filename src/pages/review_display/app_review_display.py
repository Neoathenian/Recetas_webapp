from __future__ import annotations

import logging
from pathlib import Path

import gradio as gr

from src.page_timing import timed_page_load
from src.pages.header import with_light_mode_head
from src.pages.review_display.core_review_display import (
    DEFAULT_REVIEW_VIEW,
    REVIEW_VIEW_COMPILED,
    REVIEW_VIEW_RAW,
    _accept_admin_proposal,
    _apply_review_change_choice,
    _cancel_decline_modal,
    _change_admin_slug_filter,
    _decline_admin_proposal,
    _header_people_review,
    _load_people_review_page,
    _markdown_component_allow_raw_html,
    _open_decline_modal,
    _preview_admin_proposed_edit,
    _refresh_admin_panel,
    _report_user_from_proposal,
    _select_admin_proposal,
    _toggle_admin_review_view_mode,
)

logger = logging.getLogger(__name__)

ASSETS_DIR = Path(__file__).resolve().parent
CSS_PATH = ASSETS_DIR / "css" / "review_display_page.css"
REVIEW_JS_PATH = ASSETS_DIR / "js" / "review_change_picker.js"


def _read_asset(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning("Missing review display asset at %s", path)
        return ""


def _load_css() -> str:
    return _read_asset(CSS_PATH)


def _load_review_js() -> str:
    script = _read_asset(REVIEW_JS_PATH)
    if not script:
        return ""
    return f"<script>\n{script}\n</script>"


def make_review_display_app() -> gr.Blocks:
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
            with gr.Row(elem_id="the-list-admin-overview-row"):
                with gr.Column(elem_id="the-list-admin-meta-col"):
                    admin_meta = gr.Markdown(elem_id="the-list-admin-meta")
                with gr.Column(elem_id="the-list-admin-image-col", min_width=210):
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
            admin_diff = gr.HTML(elem_id="the-list-admin-diff")
            with gr.Column():
                with gr.Row(elem_id="the-list-admin-compiled-grid"):
                    admin_compiled_base = _markdown_component_allow_raw_html(elem_id="the-list-admin-compiled-base")
                    admin_compiled_current = _markdown_component_allow_raw_html(
                        elem_id="the-list-admin-compiled-current"
                    )
                    admin_compiled_proposed = _markdown_component_allow_raw_html(
                        elem_id="the-list-admin-compiled-proposed"
                    )
                with gr.Row(elem_id="the-list-admin-raw-grid", visible=False):
                    admin_raw_base = gr.Textbox(
                        label="Contenido base (bruto)",
                        lines=14,
                        interactive=False,
                        visible=False,
                        elem_id="the-list-admin-raw-base",
                    )
                    admin_raw_current = gr.Textbox(
                        label="Contenido actual (bruto)",
                        lines=14,
                        interactive=False,
                        visible=False,
                        elem_id="the-list-admin-raw-current",
                    )
                    admin_raw_proposed = gr.Textbox(
                        label="Contenido propuesto (bruto, editable)",
                        lines=14,
                        interactive=True,
                        visible=False,
                        elem_id="the-list-admin-raw-proposed",
                    )
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
                placeholder="Motivo para retirar el privilegio `base_user` al usuario...",
            )
            report_btn = gr.Button("Reportar usuario y quitar privilegio `base_user`", variant="stop")
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
            inputs=[admin_review_view_mode],
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
