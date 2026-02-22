from __future__ import annotations

import csv
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

import gradio as gr
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from src.db import make_code, session_scope
from src.employees import ensure_technician
from src.sql_reusable_functions import (
    TableInfo,
    insert_row,
    serialize_rows,
    update_row,
)

from src.pages.admin.common import (
    blank_row,
    cast_value,
    table_info_from_json,
    load_table_view,
)

logger = logging.getLogger(__name__)


def _ensure_employee(session, employee_value: str) -> None:
    employee_value = (employee_value or "").strip()
    if not employee_value:
        return

    code = make_code(employee_value, default_prefix="employee")
    email = employee_value if "@" in employee_value else f"{code}@placeholder.local"

    try:
        ensure_technician(
            session,
            employee_identifier=email or employee_value,
            display_name=employee_value,
        )
    except ValueError as exc:
        raise ValueError(f"{exc} (employee: {employee_value})") from exc


def _preprocess_ecos_registro(session, row: Dict[str, Any]) -> None:
    email_value = row.get("employee_email") or row.get("email_employee")
    if email_value:
        _ensure_employee(session, email_value)
    if "center_id" not in row:
        center_value = row.get("center")
        if center_value:
            center_row = session.execute(
                text("SELECT id FROM centers WHERE center = :center"),
                {"center": center_value},
            ).fetchone()
            if center_row and center_row.id is not None:
                row["center_id"] = int(center_row.id)


def insert_row_in_db(table: TableInfo, new_row: Dict[str, Any], session=None) -> None:
    preprocess = _preprocess_ecos_registro if table.name == "ecos_registro" else None
    insert_row(table, new_row, session=session, preprocess=preprocess)


def _extract_constraint_name(exc: Exception) -> str | None:
    details = None
    if hasattr(exc, "orig") and getattr(exc.orig, "args", None):
        details = exc.orig.args[0]
    elif exc.args:
        details = exc.args[0]
    if isinstance(details, dict):
        return details.get("n") or details.get("constraint_name")
    return None


def _update_centers_by_code(table: TableInfo, row: Dict[str, Any]) -> bool:
    center_code = row.get("center_code")
    if not center_code:
        return False

    valid_columns = {col.name for col in table.columns}
    assignments: List[str] = []
    params: Dict[str, Any] = {}

    for column in valid_columns:
        if column == "center_code":
            continue
        if column not in row:
            continue
        assignments.append(f'"{column}" = :set_{column}')
        params[f"set_{column}"] = row.get(column)

    assignments.append('"center_code" = :set_center_code')
    params["set_center_code"] = center_code
    params["center_code_match"] = center_code

    if not assignments:
        return False

    sql = text(
        'UPDATE app."centers" '
        f'SET {", ".join(assignments)} '
        'WHERE "center_code" = :center_code_match'
    )
    with session_scope() as scoped_session:
        result = scoped_session.execute(sql, params)
        return result.rowcount > 0


def _handle_centers_unique_conflict(
    table: TableInfo, row: Dict[str, Any], exc: IntegrityError
) -> bool:
    if table.name != "centers":
        return False
    constraint = _extract_constraint_name(exc)
    if constraint != "centers_center_code_key":
        return False
    return _update_centers_by_code(table, row)


def insert_rows_in_db(table: TableInfo, rows: Sequence[Dict[str, Any]]) -> tuple[int, List[str]]:
    if not rows:
        return 0, []

    successes = 0
    failures: List[str] = []
    for index, row in enumerate(rows, start=1):
        try:
            insert_row_in_db(table, row, session=None)
            successes += 1
        except IntegrityError as exc:
            if _handle_centers_unique_conflict(table, row, exc):
                successes += 1
                continue
            logger.warning("Failed to import row %s into %s: %s", index, table.name, exc)
            failures.append(f"row {index}: {exc}")
        except Exception as exc:  # pragma: no cover - error reporting path
            logger.warning("Failed to import row %s into %s: %s", index, table.name, exc)
            failures.append(f"row {index}: {exc}")
    return successes, failures


def update_row_in_db(
    table: TableInfo,
    original_row: Dict[str, Any],
    new_row: Dict[str, Any],
) -> bool:
    preprocess = _preprocess_ecos_registro if table.name == "ecos_registro" else None
    return update_row(table, original_row, new_row, preprocess=preprocess)


def _resolve_uploaded_file(upload: Any) -> Tuple[Path, str]:
    if upload is None:
        raise ValueError("Choose a CSV file to upload.")

    if isinstance(upload, list):
        if not upload:
            raise ValueError("Choose a CSV file to upload.")
        upload = upload[0]

    if upload is None:
        raise ValueError("Choose a CSV file to upload.")

    name_hint: str | None = None
    candidates: List[Path] = []

    if isinstance(upload, (str, Path)):
        path_obj = Path(upload)
        name_hint = path_obj.name
        candidates.append(path_obj)
    elif isinstance(upload, dict):
        name_hint = upload.get("orig_name") or upload.get("name") or upload.get("path")
        for key in ("path", "name"):
            value = upload.get(key)
            if isinstance(value, str):
                candidates.append(Path(value))
    else:
        for attr in ("orig_name", "name", "path"):
            value = getattr(upload, attr, None)
            if isinstance(value, str):
                if attr == "orig_name" and not name_hint:
                    name_hint = value
                else:
                    candidates.append(Path(value))
        file_obj = getattr(upload, "file", None)
        inner_name = getattr(file_obj, "name", None) if file_obj is not None else None
        if isinstance(inner_name, str):
            candidates.append(Path(inner_name))

    for candidate in candidates:
        if candidate and candidate.exists():
            display = name_hint or candidate.name
            try:
                display_name = Path(display).name
            except Exception:  # pragma: no cover
                display_name = str(display)
            return candidate, display_name

    raise ValueError("Unable to access the uploaded file on disk.")


def _parse_csv_rows(table: TableInfo, file_path: Path) -> List[Dict[str, Any]]:
    try:
        with file_path.open("r", newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                raise ValueError("CSV file must include a header row.")

            header = []
            for field in reader.fieldnames:
                if field is None:
                    raise ValueError("CSV header contains an empty column name.")
                trimmed = field.strip()
                if not trimmed:
                    raise ValueError("CSV header contains an empty column name.")
                header.append(trimmed)

            if len(set(header)) != len(header):
                duplicates = sorted({name for name in header if header.count(name) > 1})
                raise ValueError(f"CSV header has duplicate columns: {', '.join(duplicates)}.")

            reader.fieldnames = header

            table_columns = [col.name for col in table.columns]
            missing = [col for col in table_columns if col not in header]
            if missing:
                raise ValueError(f"CSV file is missing columns: {', '.join(missing)}.")

            unexpected = [col for col in header if col not in table_columns]
            if unexpected:
                raise ValueError(f"CSV file has unexpected columns: {', '.join(unexpected)}.")

            parsed_rows: List[Dict[str, Any]] = []
            for row_number, csv_row in enumerate(reader, start=2):
                if not csv_row:
                    continue
                if all(
                    (value is None)
                    or (isinstance(value, str) and not value.strip())
                    for value in csv_row.values()
                ):
                    continue

                record: Dict[str, Any] = {}
                for column in table.columns:
                    raw_value = csv_row.get(column.name)
                    if isinstance(raw_value, str):
                        stripped = raw_value.strip()
                        raw_value = None if stripped.lower() == "null" else stripped
                    if raw_value in (None, ""):
                        value = None
                    else:
                        try:
                            value = cast_value(raw_value, column)
                        except ValueError as exc:
                            raise ValueError(f"Row {row_number}: {exc}") from exc

                    if value is None and not column.nullable and not column.has_default:
                        raise ValueError(f"Row {row_number}: Column '{column.name}' requires a value.")

                    record[column.name] = value

                parsed_rows.append(record)
    except UnicodeDecodeError as exc:
        raise ValueError("CSV file must be UTF-8 encoded.") from exc
    except csv.Error as exc:
        raise ValueError(f"CSV parsing error: {exc}.") from exc

    if not parsed_rows:
        raise ValueError("CSV file does not contain any data rows.")

    return parsed_rows


def handle_csv_upload(info_json: str, rows_json: str, upload_value: Any):
    if not info_json:
        return (
            gr.update(),
            "Select a table before uploading.",
            rows_json,
            gr.update(visible=False),
            gr.update(visible=False),
            "",
            "",
        )

    table = table_info_from_json(info_json)

    if not upload_value:
        return (
            gr.update(),
            "Choose a CSV file to upload.",
            rows_json,
            gr.update(visible=False),
            gr.update(visible=False),
            "",
            "",
        )

    try:
        file_path, display_name = _resolve_uploaded_file(upload_value)
        rows_to_insert = _parse_csv_rows(table, file_path)
        success_count, failed_rows = insert_rows_in_db(table, rows_to_insert)
    except ValueError as exc:
        return (
            gr.update(),
            f"❌ {exc}",
            rows_json,
            gr.update(visible=False),
            gr.update(visible=False),
            "",
            "",
        )
    except Exception as exc:  # pragma: no cover
        logger.exception("Failed to import CSV for %s", table.name)
        return (
            gr.update(),
            f"❌ Failed to import CSV: {exc}",
            rows_json,
            gr.update(visible=False),
            gr.update(visible=False),
            "",
            "",
        )

    new_info, refreshed_rows, html, summary = load_table_view(table.name)
    headers = [col.name for col in new_info.columns] or [""]
    total_rows = len(rows_to_insert)
    messages: List[str] = []
    if success_count:
        messages.append(
            f"✅ Uploaded {success_count} of {total_rows} row{'s' if total_rows != 1 else ''} "
            f"from {display_name}. {summary}"
        )
    else:
        messages.append(
            f"⚠️ No rows from {display_name} were imported. {summary}"
        )

    if failed_rows:
        preview = "; ".join(failed_rows[:5])
        if len(failed_rows) > 5:
            preview += " …"
        messages.append(f"⚠️ {len(failed_rows)} row(s) failed: {preview}")

    message = "\n".join(messages)

    return (
        html,
        message,
        json.dumps(serialize_rows(refreshed_rows)),
        gr.update(
            headers=headers,
            col_count=(len(headers), "dynamic"),
            value=[blank_row(new_info)],
            visible=False,
        ),
        gr.update(visible=False),
        "",
        "",
    )
