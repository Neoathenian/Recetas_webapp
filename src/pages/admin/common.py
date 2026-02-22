from __future__ import annotations

import datetime as dt
import html as html_utils
import json
from decimal import Decimal
from typing import Any, Dict, List, Tuple
from uuid import uuid4

try:
    import pandas as pd  # type: ignore
except ImportError:  # pragma: no cover
    pd = None  # type: ignore

from src.sql_reusable_functions import ColumnInfo, TableInfo, fetch_rows, get_table_info


def table_info_to_json(info: TableInfo) -> str:
    return json.dumps(
        {
            "name": info.name,
            "columns": [
                {
                    "name": col.name,
                    "data_type": col.data_type,
                    "nullable": col.nullable,
                    "has_default": col.has_default,
                }
                for col in info.columns
            ],
            "primary_key": list(info.primary_key),
        }
    )


def table_info_from_json(info_json: str) -> TableInfo:
    if not info_json:
        raise ValueError("Table metadata missing; select a table first.")
    data = json.loads(info_json)
    return TableInfo(
        name=data["name"],
        columns=[
            ColumnInfo(
                name=col["name"],
                data_type=col["data_type"],
                nullable=col["nullable"],
                has_default=col["has_default"],
            )
            for col in data["columns"]
        ],
        primary_key=data["primary_key"],
    )


def rows_from_json(rows_json: str) -> List[Dict[str, Any]]:
    return json.loads(rows_json) if rows_json else []


def blank_row(table: TableInfo) -> List[Any]:
    return [None for _ in table.columns]


def normalize_editor_rows(data: Any) -> List[List[Any]]:
    if data is None:
        return []
    if isinstance(data, list):
        return data
    if pd is not None and isinstance(data, pd.DataFrame):
        sanitized = data.astype(object).where(pd.notna(data), None)
        return sanitized.values.tolist()
    if hasattr(data, "tolist"):
        try:
            converted = data.tolist()
        except Exception:
            converted = None
        if isinstance(converted, list):
            return converted
    return [[data]]


def cast_value(value: Any, column: ColumnInfo) -> Any:
    if value in (None, ""):
        if column.nullable or column.has_default:
            return None
        raise ValueError(f"Column '{column.name}' requires a value.")

    dtype = column.data_type.lower()
    try:
        if dtype in {"integer", "bigint", "smallint"}:
            return int(value)
        if dtype in {"numeric", "decimal", "double precision", "real"}:
            return Decimal(str(value))
        if dtype == "boolean":
            if isinstance(value, bool):
                return value
            return str(value).lower() in {"true", "1", "yes", "y"}
        if dtype == "date":
            if isinstance(value, dt.date):
                return value
            return dt.date.fromisoformat(str(value))
        return str(value)
    except Exception as exc:
        raise ValueError(
            f"Invalid value '{value}' for column '{column.name}' ({column.data_type})"
        ) from exc


def row_from_df(data: List[List[Any]], table: TableInfo) -> Dict[str, Any]:
    if not data or not data[0]:
        raise ValueError("Provide values before saving.")
    row_data = data[0]
    values: Dict[str, Any] = {}
    for idx, column in enumerate(table.columns):
        cell = row_data[idx] if idx < len(row_data) else None
        if cell is None and not column.nullable and not column.has_default:
            raise ValueError(f"Column '{column.name}' requires a value.")
        values[column.name] = (
            cast_value(cell, column) if cell not in (None, "") else None
        )
    return values


def _render_table_html(table: TableInfo, rows: List[Dict[str, Any]]) -> str:
    table_id = f"admin-table-{uuid4().hex}"
    column_names = [col.name for col in table.columns]
    header_cells = "".join(f"<th>{col}</th>" for col in column_names)
    rows_html = []
    for idx, row in enumerate(rows):
        cell_chunks: List[str] = []
        for column in table.columns:
            attr_name = html_utils.escape(column.name, quote=True)
            value = "" if row.get(column.name) is None else row.get(column.name)
            cell_chunks.append(f"<td data-column='{attr_name}'>{value}</td>")
        cell_html = "".join(cell_chunks)
        rows_html.append(
            f"<tr data-row='{idx}'><td class='cell-index'>{idx + 1}</td>{cell_html}"
            "<td class='actions'>"
            f"<button class='edit-btn' data-row='{idx}' title='Edit row'>✏️</button>"
            f"<button class='trash-btn' data-row='{idx}' title='Delete row'>🗑</button>"
            "</td></tr>"
        )
    if not rows_html:
        rows_html.append(
            "<tr class='no-data'><td colspan='{0}'>No rows yet.</td></tr>".format(
                len(column_names) + 2
            )
        )

    html = f"""
    <div class="admin-table-wrapper" data-table-id="{table_id}">
      <style>
        .admin-table-wrapper table {{
          width: 100%;
          border-collapse: collapse;
        }}
        .admin-table-wrapper thead {{
          background: #f3f4f6;
        }}
        .admin-table-wrapper th,
        .admin-table-wrapper td {{
          padding: 8px 12px;
          border-bottom: 1px solid #e5e7eb;
          text-align: left;
          font-size: 14px;
        }}
        .admin-table-wrapper tbody tr:hover {{
          background: #f9fafb;
          cursor: pointer;
        }}
        .admin-table-wrapper .cell-index {{
          width: 48px;
          color: #6b7280;
          font-weight: 600;
        }}
        .admin-table-wrapper td.actions {{
          width: 90px;
          text-align: center;
          white-space: nowrap;
        }}
        .admin-table-wrapper td.actions button {{
          margin: 0 4px;
        }}
        .admin-table-wrapper tr.row-editing {{
          background: #fef9c3;
        }}
        .admin-table-wrapper tr.row-editing td {{
          background: #fefce8;
        }}
        .admin-table-wrapper td[data-column] input.inline-editor-input {{
          width: 100%;
          border: 1px solid #d1d5db;
          border-radius: 4px;
          padding: 4px 6px;
          font-size: 14px;
          background: #fff;
        }}
        .admin-table-wrapper td[data-column] input.inline-editor-input:focus {{
          outline: 2px solid #2563eb;
          outline-offset: 1px;
        }}
        .admin-table-wrapper button.edit-btn,
        .admin-table-wrapper button.trash-btn {{
          background: transparent;
          border: none;
          color: #6b7280;
          font-size: 16px;
          cursor: pointer;
          transition: color 0.2s ease;
        }}
        .admin-table-wrapper button.edit-btn:hover {{
          color: #2563eb;
        }}
        .admin-table-wrapper button.edit-btn.save-mode {{
          color: #2563eb;
          font-weight: 600;
        }}
        .admin-table-wrapper button.trash-btn:hover {{
          color: #dc2626;
        }}
        .admin-table-wrapper tr.no-data td {{
          text-align: center;
          color: #6b7280;
          font-style: italic;
        }}
      </style>
      <table>
        <thead>
          <tr>
            <th>#</th>
            {header_cells}
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {''.join(rows_html)}
        </tbody>
      </table>
    </div>
    """
    return html


def load_table_view(table_name: str) -> Tuple[TableInfo, List[Dict[str, Any]], str, str]:
    info = get_table_info(table_name)
    rows = fetch_rows(info)
    html = _render_table_html(info, rows)
    summary = (
        f"{len(rows)} row{'s' if len(rows) != 1 else ''} in {table_name}."
    )
    return info, rows, html, summary
