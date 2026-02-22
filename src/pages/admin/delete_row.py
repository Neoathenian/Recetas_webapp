from __future__ import annotations

import json
import logging
from typing import Any, Dict

import gradio as gr

from src.pages.admin.common import (
    blank_row,
    load_table_view,
    rows_from_json,
    table_info_from_json,
)
from src.sql_reusable_functions import delete_row as delete_row_operation, serialize_rows

logger = logging.getLogger(__name__)


def _delete_row_in_db(table, row: Dict[str, Any]) -> None:
    delete_row_operation(table, row)


def delete_row(info_json: str, rows_json: str, index_value: str):
    table = table_info_from_json(info_json)
    rows = rows_from_json(rows_json)
    if not index_value:
        return (
            gr.update(),
            "Select a row before deleting.",
            rows_json,
            gr.update(),
            gr.update(),
            "",
            "",
        )
    idx = int(float(index_value))
    if idx < 0 or idx >= len(rows):
        return (
            gr.update(),
            "Row index out of range.",
            rows_json,
            gr.update(),
            gr.update(),
            "",
            "",
        )

    try:
        _delete_row_in_db(table, rows[idx])
    except Exception as exc:
        logger.exception("Failed to delete row from %s", table.name)
        return (
            gr.update(),
            f"❌ {exc}",
            rows_json,
            gr.update(),
            gr.update(),
            "",
            index_value,
        )

    _, refreshed_rows, html, summary = load_table_view(table.name)
    headers = [col.name for col in table.columns] or [""]
    return (
        html,
        f"✅ Row deleted. {summary}",
        json.dumps(serialize_rows(refreshed_rows)),
        gr.update(
            headers=headers,
            col_count=(len(headers), "dynamic"),
            value=[blank_row(table)],
            visible=False,
        ),
        gr.update(visible=False),
        "",
        "",
    )
