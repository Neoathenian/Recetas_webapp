from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable, Dict, List, Sequence, Tuple

from sqlalchemy import text
from sqlalchemy.orm import Session

from src.db import session_scope

PreprocessHook = Callable[[Session, Dict[str, Any]], None] | None


@dataclass(frozen=True)
class ColumnInfo:
    name: str
    data_type: str
    nullable: bool
    has_default: bool


@dataclass(frozen=True)
class TableInfo:
    name: str
    columns: Sequence[ColumnInfo]
    primary_key: Sequence[str]


def list_tables() -> List[str]:
    with session_scope() as session:
        rows = session.execute(
            text(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'app'
                ORDER BY table_name
                """
            )
        ).scalars()
        return list(rows)


def get_table_info(table: str) -> TableInfo:
    if not table:
        raise ValueError("No table provided")
    if not table.replace("_", "").isalnum():
        raise ValueError(f"Invalid table name: {table}")

    with session_scope() as session:
        columns = session.execute(
            text(
                """
                SELECT column_name, data_type, is_nullable, column_default
                FROM information_schema.columns
                WHERE table_schema = 'app' AND table_name = :table
                ORDER BY ordinal_position
                """
            ),
            {"table": table},
        ).all()
        pk_columns = session.execute(
            text(
                """
                SELECT kcu.column_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                  ON tc.constraint_name = kcu.constraint_name
                 AND tc.table_schema   = kcu.table_schema
                 AND tc.table_name     = kcu.table_name
                WHERE tc.constraint_type = 'PRIMARY KEY'
                  AND tc.table_schema = 'app'
                  AND tc.table_name = :table
                ORDER BY kcu.ordinal_position
                """
            ),
            {"table": table},
        ).scalars()

    return TableInfo(
        name=table,
        columns=[
            ColumnInfo(
                name=row.column_name,
                data_type=row.data_type,
                nullable=row.is_nullable == "YES",
                has_default=row.column_default is not None,
            )
            for row in columns
        ],
        primary_key=list(pk_columns),
    )


def fetch_rows(table: TableInfo) -> List[Dict[str, Any]]:
    column_clause = ", ".join(f'"{col.name}"' for col in table.columns)
    sql = f'SELECT {column_clause} FROM app."{table.name}"'
    if table.primary_key:
        sql += " ORDER BY " + ", ".join(f'"{pk}"' for pk in table.primary_key)
    query = text(sql)
    with session_scope() as session:
        rows = session.execute(query).mappings().all()
        return [dict(row) for row in rows]


def serialize_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    payload: List[Dict[str, Any]] = []
    for row in rows:
        converted: Dict[str, Any] = {}
        for key, value in row.items():
            if isinstance(value, (dt.date, dt.datetime, dt.time)):
                converted[key] = value.isoformat()
            elif isinstance(value, Decimal):
                converted[key] = str(value)
            else:
                converted[key] = value
        payload.append(converted)
    return payload


def build_row_matcher(table: TableInfo, row: Dict[str, Any]) -> tuple[str, Dict[str, Any]]:
    clauses: list[str] = []
    params: Dict[str, Any] = {}
    for col in table.columns:
        col_name = col.name
        if col_name not in row:
            continue
        value = row[col_name]
        if value is None:
            clauses.append(f'"{col_name}" IS NULL')
        else:
            param_name = f"col_{col_name}"
            params[param_name] = value
            clauses.append(f'"{col_name}" = :{param_name}')
    if not clauses:
        raise ValueError("Unable to build row filter for deletion.")
    return " AND ".join(clauses), params


def delete_row(table: TableInfo, row: Dict[str, Any], session: Session | None = None) -> None:
    params: Dict[str, Any]
    if table.primary_key:
        params = {}
        for pk in table.primary_key:
            value = row.get(pk)
            if value is None:
                raise ValueError("Cannot delete row with missing primary key value.")
            params[f"pk_{pk}"] = value
        where_clause = " AND ".join(f'"{pk}" = :pk_{pk}' for pk in table.primary_key)
    else:
        where_clause, params = build_row_matcher(table, row)

    def _execute(sess: Session) -> None:
        result = sess.execute(
            text(f'DELETE FROM app."{table.name}" WHERE {where_clause}'),
            params,
        )
        if result.rowcount == 0:
            raise ValueError("Unable to delete row because no matching record was found.")

    if session is None:
        with session_scope() as scoped_session:
            _execute(scoped_session)
    else:
        _execute(session)


def insert_row(
    table: TableInfo,
    new_row: Dict[str, Any],
    session: Session | None = None,
    preprocess: PreprocessHook = None,
) -> None:
    columns = []
    params = {}
    for col in table.columns:
        value = new_row.get(col.name)
        if value is None:
            if not col.nullable and not col.has_default:
                raise ValueError(f"Column '{col.name}' requires a value.")
            continue
        columns.append(col.name)
        params[col.name] = value

    if not columns:
        sql = text(f'INSERT INTO app."{table.name}" DEFAULT VALUES')
    else:
        cols_sql = ", ".join(f'"{col}"' for col in columns)
        vals_sql = ", ".join(f":{col}" for col in columns)
        if table.name == "user" and "email" in columns:
            update_cols = [col for col in columns if col != "email"]
            if update_cols:
                update_clause = ", ".join(f'"{col}" = EXCLUDED."{col}"' for col in update_cols)
                sql_text = (
                    f'INSERT INTO app."{table.name}" ({cols_sql}) VALUES ({vals_sql}) '
                    f'ON CONFLICT (email) DO UPDATE SET {update_clause}'
                )
            else:
                sql_text = (
                    f'INSERT INTO app."{table.name}" ({cols_sql}) VALUES ({vals_sql}) '
                    "ON CONFLICT (email) DO NOTHING"
                )
        else:
            sql_text = f'INSERT INTO app."{table.name}" ({cols_sql}) VALUES ({vals_sql})'
        sql = text(sql_text)

    def _execute(sess: Session) -> None:
        if preprocess:
            preprocess(sess, new_row)
        if params:
            sess.execute(sql, params)
        else:
            sess.execute(sql)

    if session is None:
        with session_scope() as scoped_session:
            _execute(scoped_session)
    else:
        _execute(session)


def update_row(
    table: TableInfo,
    original_row: Dict[str, Any],
    new_row: Dict[str, Any],
    session: Session | None = None,
    preprocess: PreprocessHook = None,
) -> bool:
    set_clauses: List[str] = []
    params: Dict[str, Any] = {}
    changed = False

    if table.primary_key:
        where_clause = " AND ".join(f'"{pk}" = :orig_{pk}' for pk in table.primary_key)
        for pk in table.primary_key:
            params[f"orig_{pk}"] = original_row.get(pk)
    else:
        where_clause, matcher_params = build_row_matcher(table, original_row)
        params.update(matcher_params)

    for col in table.columns:
        new_value = new_row.get(col.name)
        orig_value = original_row.get(col.name)
        if new_value != orig_value:
            params[f"set_{col.name}"] = new_value
            set_clauses.append(f'"{col.name}" = :set_{col.name}')
            changed = True

    if not changed:
        return False

    sql = text(f'UPDATE app."{table.name}" SET {", ".join(set_clauses)} WHERE {where_clause}')

    def _execute(sess: Session) -> bool:
        if preprocess:
            preprocess(sess, new_row)
        result = sess.execute(sql, params)
        if result.rowcount == 0:
            raise ValueError("Unable to update row because no matching record was found.")
        return True

    if session is None:
        with session_scope() as scoped_session:
            return _execute(scoped_session)
    return _execute(session)
