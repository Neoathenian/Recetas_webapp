from __future__ import annotations

import gradio as gr

from src.pages.admin.common import blank_row, table_info_from_json


def prepare_new_row(info_json: str):
    table = table_info_from_json(info_json)
    headers = [col.name for col in table.columns] or [""]
    blank = [blank_row(table)]
    return (
        gr.update(
            headers=headers,
            col_count=(len(headers), "dynamic"),
            value=blank,
            visible=True,
        ),
        gr.update(visible=True),
        f"Creating new row in {table.name}. Fill the fields and click Save Row.",
        "new",
        "-1",
    )
