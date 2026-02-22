from __future__ import annotations

import csv
import datetime as dt
import io
import logging
import tempfile
import zipfile
from decimal import Decimal
from pathlib import Path
from typing import Any, Sequence, Tuple
from uuid import uuid4

import gradio as gr

from src.sql_reusable_functions import (  # noqa: E501 - keep imports tidy for clarity
    TableInfo,
    fetch_rows,
    get_table_info,
    list_tables,
)

logger = logging.getLogger(__name__)


def _format_csv_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dt.datetime):
        return value.isoformat()
    if isinstance(value, dt.time):
        return value.isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return str(value)


def _table_to_csv_bytes(table: TableInfo, rows: Sequence[dict[str, Any]]) -> bytes:
    output = io.StringIO()
    writer = csv.writer(output)
    header = [col.name for col in table.columns]
    writer.writerow(header)
    for row in rows:
        if header:
            values = [_format_csv_value(row.get(column)) for column in header]
        else:
            values = []
        writer.writerow(values)
    return output.getvalue().encode("utf-8")


def _export_tables_zip() -> Tuple[Path, int, int]:
    tables = list_tables()
    if not tables:
        raise ValueError("No tables available to export.")

    zip_path = Path(tempfile.gettempdir()) / f"the_list_tables-{uuid4().hex}.zip"
    total_rows = 0
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for table_name in tables:
            table = get_table_info(table_name)
            rows = fetch_rows(table)
            csv_bytes = _table_to_csv_bytes(table, rows)
            archive.writestr(f"{table_name}.csv", csv_bytes)
            total_rows += len(rows)

    return zip_path, len(tables), total_rows


def handle_download_all_tables():
    try:
        zip_path, table_count, row_count = _export_tables_zip()
    except ValueError as exc:
        return gr.update(), f"❌ {exc}"
    except Exception as exc:  # pragma: no cover
        logger.exception("Failed to export tables to ZIP.")
        return gr.update(), f"❌ Failed to export tables: {exc}"

    message = (
        f"📦 Prepared download with {table_count} table{'s' if table_count != 1 else ''} "
        f"and {row_count} row{'s' if row_count != 1 else ''}."
    )
    return str(zip_path), message
