from __future__ import annotations

import json
import logging
from pathlib import Path

import gradio as gr

from src.pages.admin.add_row import prepare_new_row
from src.pages.admin.common import (
    blank_row,
    load_table_view,
    table_info_to_json,
)
from src.pages.admin.delete_row import delete_row as delete_row_handler
from src.pages.admin.download_all_tables import handle_download_all_tables
from src.pages.admin.edit_row import load_row_for_edit, save_row, save_inline_row
from src.pages.admin.upload_csv import handle_csv_upload
from src.pages.header import render_header, with_light_mode_head
from src.page_timing import timed_page_load
from src.sql_reusable_functions import list_tables, serialize_rows

logger = logging.getLogger(__name__)

ASSETS_DIR = Path(__file__).parent
CSS_DIR = ASSETS_DIR / "css"
JS_DIR = ASSETS_DIR / "js"


def _read_asset(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning("Missing admin asset: %s", path)
        return ""


ADMIN_CSS = _read_asset(CSS_DIR / "admin_page.css")
ADMIN_TABLE_INTERACTION_JS = _read_asset(JS_DIR / "admin_table_interaction.js")


def _header_admin(request: gr.Request):
    return render_header(path="/admin", request=request)


def _init_admin():
    tables = list_tables()
    if not tables:
        return (
            gr.update(choices=[], value=None),
            "<p>No tables in schema <code>app</code>.</p>",
            "No tables available.",
            "",
            json.dumps([]),
            gr.update(
                headers=[""],
                col_count=(1, "dynamic"),
                value=[[None]],
                visible=False,
            ),
            "",
            "",
            gr.update(visible=False),
            gr.update(visible=False),
        )

    table = tables[0]
    info, rows, html, summary = load_table_view(table)
    headers = [col.name for col in info.columns] or [""]
    return (
        gr.update(choices=tables, value=table),
        html,
        summary,
        table_info_to_json(info),
        json.dumps(serialize_rows(rows)),
        gr.update(
            headers=headers,
            col_count=(len(headers), "dynamic"),
            value=[blank_row(info)],
            visible=False,
        ),
        "",
        "",
        gr.update(visible=False),
        gr.update(visible=False),
    )


def _handle_table_select(table: str):
    tables = list_tables()
    if not tables:
        return _init_admin()

    if not table or table not in tables:
        table = tables[0]

    info, rows, html, summary = load_table_view(table)
    headers = [col.name for col in info.columns] or [""]
    return (
        gr.update(choices=tables, value=table),
        html,
        summary,
        table_info_to_json(info),
        json.dumps(serialize_rows(rows)),
        gr.update(
            headers=headers,
            col_count=(len(headers), "dynamic"),
            value=[blank_row(info)],
            visible=False,
        ),
        "",
        "",
        gr.update(visible=False),
        gr.update(visible=False),
    )


def make_admin_app() -> gr.Blocks:
    with gr.Blocks(
        title="The List Admin",
        css=ADMIN_CSS,
        head=with_light_mode_head(None),
    ) as admin_app:
        hdr = gr.HTML()
        admin_app.load(timed_page_load("/admin", _header_admin), outputs=[hdr])

        info_state = gr.State("")
        rows_state = gr.State("")
        edit_mode_state = gr.State("")

        with gr.Column(elem_id="admin-shell"):
            gr.Markdown("## Database Explorer")
            with gr.Row(equal_height=True, elem_classes=["admin-toolbar"]):
                add_row_btn = gr.Button("➕ Add Row", variant="secondary")
                table_list = gr.Dropdown(
                    label="Select table",
                    choices=list_tables(),
                    interactive=True,
                )
            with gr.Column(elem_id="admin-upload-area"):
                upload_btn = gr.UploadButton(
                    "📤 Upload CSV",
                    file_types=["text/csv", ".csv"],
                    file_count="single",
                    variant="secondary",
                    elem_id="admin-upload-button",
                )
                download_all_btn = gr.DownloadButton(
                    "📥 Download All Tables",
                    variant="secondary",
                    elem_id="admin-download-all-button",
                )
            with gr.Column(elem_id="admin-editor-area"):
                save_row_btn = gr.Button(
                    "Save Row", variant="primary", visible=False
                )
                edit_df = gr.Dataframe(
                    headers=[""],
                    col_count=(1, "dynamic"),
                    row_count=1,
                    interactive=True,
                    wrap=True,
                    label="Row editor",
                    visible=False,
                    elem_id="admin-editor-df",
                )
            table_html = gr.HTML()
            status = gr.Markdown("Choose a table to view data.")

        selected_index_box = gr.Textbox(
            value="",
            show_label=False,
            elem_id="admin-selected-index",
            elem_classes=["admin-hidden"],
        )
        load_row_trigger = gr.Button(
            "_load",
            elem_id="admin-load-trigger",
            elem_classes=["admin-hidden"],
        )
        delete_row_trigger = gr.Button(
            "_delete",
            elem_id="admin-delete-trigger",
            elem_classes=["admin-hidden"],
        )
        inline_payload_box = gr.Textbox(
            value="",
            show_label=False,
            elem_id="admin-inline-payload",
            elem_classes=["admin-hidden"],
        )
        inline_save_trigger = gr.Button(
            "_inline_save",
            elem_id="admin-inline-save-trigger",
            elem_classes=["admin-hidden"],
        )

        with gr.Group(
            visible=False,
            elem_classes=["modal-overlay", "admin-delete-dialog"],
        ) as delete_dialog:
            with gr.Column(elem_classes=["modal-content"]):
                gr.Markdown(
                    "Are you sure you want to delete this row?",
                    elem_classes=["modal-text"],
                )
                with gr.Row(elem_classes=["modal-actions"]):
                    confirm_delete_btn = gr.Button("Delete", variant="stop")
                    cancel_delete_btn = gr.Button(
                        "Cancel", variant="secondary"
                    )

        admin_app.load(
            timed_page_load("/admin", _init_admin),
            inputs=None,
            outputs=[
                table_list,
                table_html,
                status,
                info_state,
                rows_state,
                edit_df,
                edit_mode_state,
                selected_index_box,
                save_row_btn,
                delete_dialog,
            ],
        )

        gr.on(
            triggers=[admin_app.load],
            fn=None,
            inputs=None,
            outputs=None,
            js=ADMIN_TABLE_INTERACTION_JS,
            queue=False,
            show_api=False,
        )

        table_list.change(
            _handle_table_select,
            inputs=[table_list],
            outputs=[
                table_list,
                table_html,
                status,
                info_state,
                rows_state,
                edit_df,
                edit_mode_state,
                selected_index_box,
                save_row_btn,
                delete_dialog,
            ],
        )

        upload_btn.upload(
            handle_csv_upload,
            inputs=[info_state, rows_state, upload_btn],
            outputs=[
                table_html,
                status,
                rows_state,
                edit_df,
                save_row_btn,
                edit_mode_state,
                selected_index_box,
            ],
        )

        download_all_btn.click(
            handle_download_all_tables,
            inputs=None,
            outputs=[download_all_btn, status],
        )

        add_row_btn.click(
            prepare_new_row,
            inputs=[info_state],
            outputs=[
                edit_df,
                save_row_btn,
                status,
                edit_mode_state,
                selected_index_box,
            ],
        )

        load_row_trigger.click(
            load_row_for_edit,
            inputs=[info_state, rows_state, selected_index_box],
            outputs=[edit_df, save_row_btn, status, edit_mode_state],
        )

        delete_row_trigger.click(
            delete_row_handler,
            inputs=[info_state, rows_state, selected_index_box],
            outputs=[
                table_html,
                status,
                rows_state,
                edit_df,
                save_row_btn,
                edit_mode_state,
                selected_index_box,
            ],
        )

        inline_save_trigger.click(
            save_inline_row,
            inputs=[info_state, rows_state, inline_payload_box],
            outputs=[
                table_html,
                status,
                rows_state,
                edit_df,
                save_row_btn,
                edit_mode_state,
                selected_index_box,
            ],
        )

        confirm_delete_btn.click(
            lambda: gr.update(visible=False),
            None,
            [delete_dialog],
        ).then(
            fn=delete_row_handler,
            inputs=[info_state, rows_state, selected_index_box],
            outputs=[
                table_html,
                status,
                rows_state,
                edit_df,
                save_row_btn,
                edit_mode_state,
                selected_index_box,
            ],
        )

        cancel_delete_btn.click(
            lambda: gr.update(visible=False),
            None,
            [delete_dialog],
        )

        save_row_btn.click(
            save_row,
            inputs=[info_state, rows_state, edit_df, edit_mode_state],
            outputs=[
                table_html,
                status,
                rows_state,
                edit_df,
                save_row_btn,
                edit_mode_state,
                selected_index_box,
            ],
        )

    return admin_app
