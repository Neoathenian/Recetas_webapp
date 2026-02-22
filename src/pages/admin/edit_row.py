from __future__ import annotations

import json
import logging
from typing import Any, Dict

import gradio as gr

from src.pages.admin.common import (
    blank_row,
    cast_value,
    load_table_view,
    normalize_editor_rows,
    row_from_df,
    rows_from_json,
    table_info_from_json,
)
from src.pages.admin.upload_csv import insert_row_in_db, update_row_in_db
from src.sql_reusable_functions import serialize_rows

logger = logging.getLogger(__name__)


def load_row_for_edit(info_json: str, rows_json: str, index_value: str):
    table = table_info_from_json(info_json)
    rows = rows_from_json(rows_json)
    if not index_value:
        raise ValueError("No row selected.")
    idx = int(float(index_value))
    if idx < 0 or idx >= len(rows):
        raise ValueError("Row index out of range.")

    headers = [col.name for col in table.columns] or [""]
    values = (
        [[row.get(col.name) for col in table.columns]]
        if table.columns
        else [[None]]
    )
    return (
        gr.update(
            headers=headers,
            col_count=(len(headers), "dynamic"),
            value=values,
            visible=True,
        ),
        gr.update(visible=True),
        f"Editing row #{idx + 1} in {table.name}.",
        str(idx),
    )


def save_row(
    info_json: str,
    rows_json: str,
    df_rows: Any,
    selected_index: str,
):
    if not info_json:
        return (
            gr.update(),
            "Select a table first.",
            rows_json,
            gr.update(),
            gr.update(),
            selected_index,
            selected_index,
        )

    table = table_info_from_json(info_json)
    headers = [col.name for col in table.columns] or [""]
    editor_rows = normalize_editor_rows(df_rows)

    try:
        new_row = row_from_df(editor_rows, table)
    except Exception as exc:
        fallback_rows = editor_rows if editor_rows else [blank_row(table)]
        return (
            gr.update(),
            f"❌ {exc}",
            rows_json,
            gr.update(
                headers=headers,
                col_count=(len(headers), "dynamic"),
                value=fallback_rows,
                visible=True,
            ),
            gr.update(visible=True),
            selected_index,
            selected_index,
        )

    rows = rows_from_json(rows_json)
    try:
        if selected_index in ("new", "-1", ""):
            insert_row_in_db(table, new_row)
            action = "inserted"
        else:
            idx = int(float(selected_index))
            if idx < 0 or idx >= len(rows):
                raise ValueError("Row index out of range.")
            updated = update_row_in_db(table, rows[idx], new_row)
            action = "updated" if updated else "unchanged"
    except Exception as exc:
        logger.exception("Failed to save row in %s", table.name)
        fallback_rows = editor_rows if editor_rows else [blank_row(table)]
        return (
            gr.update(),
            f"❌ {exc}",
            rows_json,
            gr.update(
                headers=headers,
                col_count=(len(headers), "dynamic"),
                value=fallback_rows,
                visible=True,
            ),
            gr.update(visible=True),
            selected_index,
            selected_index,
        )

    _, refreshed_rows, html, summary = load_table_view(table.name)
    return (
        html,
        f"✅ Row {action}. {summary}",
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


def save_inline_row(info_json: str, rows_json: str, payload_json: str):
    if not info_json:
        return (
            gr.update(),
            "Select a table first.",
            rows_json,
            gr.update(visible=False),
            gr.update(visible=False),
            "",
            "",
        )

    if not payload_json:
        return (
            gr.update(),
            "Select a row before editing inline.",
            rows_json,
            gr.update(visible=False),
            gr.update(visible=False),
            "",
            "",
        )

    table = table_info_from_json(info_json)
    headers = [col.name for col in table.columns] or [""]

    try:
        payload = json.loads(payload_json)
    except json.JSONDecodeError:
        return (
            gr.update(),
            "Invalid inline edit payload.",
            rows_json,
            gr.update(visible=False),
            gr.update(visible=False),
            "",
            "",
        )

    if not isinstance(payload, dict):
        return (
            gr.update(),
            "Invalid inline edit payload.",
            rows_json,
            gr.update(visible=False),
            gr.update(visible=False),
            "",
            "",
        )

    rows = rows_from_json(rows_json)
    try:
        idx = int(float(payload.get("index", -1)))
    except (ValueError, TypeError):
        idx = -1
    if idx < 0 or idx >= len(rows):
        return (
            gr.update(),
            "Row index out of range.",
            rows_json,
            gr.update(visible=False),
            gr.update(visible=False),
            "",
            "",
        )

    values = payload.get("values")
    if not isinstance(values, dict):
        return (
            gr.update(),
            "Inline edit payload missing values.",
            rows_json,
            gr.update(visible=False),
            gr.update(visible=False),
            "",
            "",
        )

    original_row = rows[idx]
    new_row = dict(original_row)

    for column in table.columns:
        if column.name not in values:
            continue
        raw_value = values[column.name]
        normalized = raw_value
        if isinstance(raw_value, str):
            stripped = raw_value.strip()
            if not stripped or stripped.lower() == "null":
                normalized = None
            else:
                dtype = column.data_type.lower()
                if "char" not in dtype and "text" not in dtype:
                    normalized = stripped
        if normalized in (None, ""):
            value = None
        else:
            try:
                value = cast_value(normalized, column)
            except Exception as exc:
                return (
                    gr.update(),
                    f"❌ {exc}",
                    rows_json,
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
        new_row[column.name] = value

    try:
        updated = update_row_in_db(table, original_row, new_row)
    except Exception as exc:  # pragma: no cover
        logger.exception("Failed to save inline row in %s", table.name)
        return (
            gr.update(),
            f"❌ {exc}",
            rows_json,
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

    _, refreshed_rows, html, summary = load_table_view(table.name)
    action = "updated" if updated else "unchanged"
    return (
        html,
        f"✅ Row {action}. {summary}",
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
