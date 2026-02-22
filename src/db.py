from __future__ import annotations

import logging
import os
import re
import atexit
import time
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, text, event
from sqlalchemy.engine import Engine
from sqlalchemy.engine.url import URL, make_url
from sqlalchemy.orm import Session, sessionmaker

from src.page_timing import has_active_timing, record_sql_time

logger = logging.getLogger(__name__)
timing_logger = logging.getLogger("uvicorn.error")

DEFAULT_DB_URL = "postgresql+pg8000://postgres:postgres@localhost:5432/postgres"
TRUE_VALUES = {"1", "true", "yes", "on"}

_ENGINE: Engine | None = None
_SessionLocal: sessionmaker | None = None
_CLOUD_SQL_CONNECTOR = None
_QUERY_START_KEY = "page_timing_query_start"


def _format_sql_for_log(statement: str) -> str:
    compact = " ".join((statement or "").split())
    if len(compact) <= 180:
        return compact
    return f"{compact[:177]}..."


def _log_duration(event_name: str, start: float, **fields: object) -> None:
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    if fields:
        field_text = " ".join(f"{key}={value}" for key, value in fields.items())
        timing_logger.info("db.timing event=%s ms=%.2f %s", event_name, elapsed_ms, field_text)
        return
    timing_logger.info("db.timing event=%s ms=%.2f", event_name, elapsed_ms)


def _sanitize_url(url: URL) -> str:
    safe_url = url._replace(password="***")
    return str(safe_url)


def _is_truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in TRUE_VALUES


def _is_local_host(host: str | None) -> bool:
    normalized = str(host or "").strip().lower()
    return normalized in {"localhost", "127.0.0.1", "::1"}


def _disallow_local_db() -> bool:
    # Safe default: do not silently connect to local postgres unless explicitly allowed.
    return not _is_truthy(os.getenv("ALLOW_LOCAL_DB"))


def _assert_not_local_target(host: str | None, *, source: str) -> None:
    if _disallow_local_db() and _is_local_host(host):
        raise RuntimeError(
            f"Refusing local database target from {source} ({host}). "
            "Set ALLOW_LOCAL_DB=1 to override."
        )


def _resolve_database_url() -> str:
    explicit_url = os.getenv("DATABASE_URL")
    if explicit_url:
        return explicit_url

    host = os.getenv("PGHOST") or os.getenv("DB_HOST")
    if host:
        user = os.getenv("PGUSER") or os.getenv("DB_USER", "postgres")
        password = os.getenv("PGPASSWORD") or os.getenv("DB_PASSWORD", "")
        port = os.getenv("PGPORT") or os.getenv("DB_PORT", "5432")
        dbname = os.getenv("PGDATABASE") or os.getenv("DB_NAME", "postgres")
        return f"postgresql+pg8000://{user}:{password}@{host}:{port}/{dbname}"

    return DEFAULT_DB_URL


def _attach_sql_timing(engine: Engine) -> None:
    if getattr(engine, "_page_timing_attached", False):
        return
    setattr(engine, "_page_timing_attached", True)

    @event.listens_for(engine, "before_cursor_execute")
    def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        if not has_active_timing():
            return
        conn.info.setdefault(_QUERY_START_KEY, []).append(time.perf_counter())

    @event.listens_for(engine, "after_cursor_execute")
    def _after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        start_times = conn.info.get(_QUERY_START_KEY)
        if not start_times:
            return
        start = start_times.pop()
        elapsed = time.perf_counter() - start
        record_sql_time(elapsed)
        timing_logger.info(
            "db.query.timing ms=%.2f rows=%s stmt=%s",
            elapsed * 1000.0,
            getattr(cursor, "rowcount", None),
            _format_sql_for_log(statement),
        )


def _attach_connection_defaults(engine: Engine) -> None:
    if getattr(engine, "_connection_defaults_attached", False):
        return
    setattr(engine, "_connection_defaults_attached", True)


def _build_engine() -> Engine:
    build_start = time.perf_counter()
    instance_name = os.getenv("INSTANCE_CONNECTION_NAME")
    host = os.getenv("PGHOST") or os.getenv("DB_HOST")
    raw_url = os.getenv("DATABASE_URL")

    if raw_url:
        try:
            url = make_url(raw_url)
        except Exception as exc:
            logger.error("Invalid DATABASE_URL provided: %s", exc)
        else:
            _assert_not_local_target(url.host, source="DATABASE_URL")
            logger.warning("Database target from DATABASE_URL: %s", _sanitize_url(url))
            step_start = time.perf_counter()
            engine = create_engine(url, pool_pre_ping=True, future=True)
            _log_duration("create_engine.database_url", step_start)
            _attach_sql_timing(engine)
            _attach_connection_defaults(engine)
            _log_duration("build_engine.total", build_start, source="DATABASE_URL")
            return engine

    if instance_name:
        if host:
            logger.warning(
                "INSTANCE_CONNECTION_NAME is set; ignoring PGHOST/DB_HOST and using Cloud SQL connector."
            )
        logger.warning("Database target from Cloud SQL connector: %s", instance_name)
        step_start = time.perf_counter()
        engine = _build_cloud_sql_engine(instance_name)
        _log_duration("build_engine.cloud_sql", step_start)
        _log_duration("build_engine.total", build_start, source="INSTANCE_CONNECTION_NAME")
        return engine

    if host:
        _assert_not_local_target(host, source="PGHOST/DB_HOST")
        url = make_url(_resolve_database_url())
        logger.warning("Database target from PGHOST/DB_HOST: %s", _sanitize_url(url))
        step_start = time.perf_counter()
        engine = create_engine(url, pool_pre_ping=True, future=True)
        _log_duration("create_engine.pghost", step_start)
        _attach_sql_timing(engine)
        _attach_connection_defaults(engine)
        _log_duration("build_engine.total", build_start, source="PGHOST/DB_HOST")
        return engine

    if _disallow_local_db():
        raise RuntimeError(
            "Database is not configured. Set INSTANCE_CONNECTION_NAME or DATABASE_URL "
            "(or PGHOST/DB_HOST) to start the app."
        )

    url = make_url(DEFAULT_DB_URL)
    logger.warning(
        "Falling back to default database connection %s; set DATABASE_URL / PGHOST / DB_HOST or INSTANCE_CONNECTION_NAME to connect elsewhere.",
        _sanitize_url(url),
    )
    step_start = time.perf_counter()
    engine = create_engine(url, pool_pre_ping=True, future=True)
    _log_duration("create_engine.default_fallback", step_start)
    _attach_sql_timing(engine)
    _attach_connection_defaults(engine)
    _log_duration("build_engine.total", build_start, source="DEFAULT_DB_URL")
    return engine


def _build_cloud_sql_engine(instance_name: str) -> Engine:
    build_start = time.perf_counter()
    global _CLOUD_SQL_CONNECTOR
    try:
        from google.cloud.sql.connector import Connector, IPTypes
    except ModuleNotFoundError as exc:
        raise RuntimeError("cloud-sql-python-connector is required to use INSTANCE_CONNECTION_NAME") from exc

    db_user = os.getenv("DB_USER", "postgres")
    db_pass = os.getenv("DB_PASS") or os.getenv("DB_PASSWORD") or os.getenv("PGPASSWORD") or ""
    db_name = os.getenv("DB_NAME", "postgres")
    ip_preference = os.getenv("DB_IP_TYPE", "public").lower()
    ip_type = IPTypes.PRIVATE if ip_preference == "private" else IPTypes.PUBLIC

    connector = Connector()
    _CLOUD_SQL_CONNECTOR = connector

    def getconn():
        connect_start = time.perf_counter()
        connection = connector.connect(
            instance_name,
            "pg8000",
            user=db_user,
            password=db_pass,
            db=db_name,
            ip_type=ip_type,
        )
        _log_duration(
            "cloud_sql.connector.connect",
            connect_start,
            instance=instance_name,
            ip_type=ip_type.name.lower(),
        )
        return connection

    atexit.register(connector.close)
    logger.info(
        "Creating Cloud SQL connector engine for instance %s (user=%s db=%s ip_type=%s)",
        instance_name,
        db_user,
        db_name,
        ip_type.name.lower(),
    )
    create_start = time.perf_counter()
    engine = create_engine("postgresql+pg8000://", creator=getconn, pool_pre_ping=True, future=True)
    _log_duration("cloud_sql.create_engine", create_start)
    _attach_sql_timing(engine)
    _attach_connection_defaults(engine)
    _log_duration("cloud_sql.build_engine_total", build_start, instance=instance_name)
    return engine


def get_engine() -> Engine:
    global _ENGINE
    if _ENGINE is None:
        start = time.perf_counter()
        _ENGINE = _build_engine()
        _log_duration("get_engine.initialize", start)
    return _ENGINE


def get_session_factory() -> sessionmaker:
    global _SessionLocal
    if _SessionLocal is None:
        init_start = time.perf_counter()
        engine = get_engine()
        try:
            connectivity_start = time.perf_counter()
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            _log_duration("session_factory.connectivity_check", connectivity_start)
        except Exception as exc:
            logger.error("Database connectivity check failed: %s", exc)
            raise
        factory_start = time.perf_counter()
        _SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
        _log_duration("session_factory.create", factory_start)
        _log_duration("session_factory.initialize_total", init_start)
    return _SessionLocal


def _apply_session_defaults(session: Session, *, scope_name: str) -> None:
    _ = session
    _ = scope_name


@contextmanager
def session_scope() -> Iterator[Session]:
    scope_start = time.perf_counter()
    factory_start = time.perf_counter()
    factory = get_session_factory()
    _log_duration("session_scope.get_factory", factory_start)
    session_start = time.perf_counter()
    session: Session = factory()
    _log_duration("session_scope.create_session", session_start)
    _apply_session_defaults(session, scope_name="session_scope")
    try:
        yield session
        commit_start = time.perf_counter()
        session.commit()
        _log_duration("session_scope.commit", commit_start)
    except Exception as exc:
        rollback_start = time.perf_counter()
        session.rollback()
        _log_duration("session_scope.rollback", rollback_start)
        logger.error("Database transaction rolled back: %s", exc, exc_info=True)
        raise
    finally:
        close_start = time.perf_counter()
        session.close()
        _log_duration("session_scope.close", close_start)
        _log_duration("session_scope.total", scope_start)


@contextmanager
def readonly_session_scope() -> Iterator[Session]:
    scope_start = time.perf_counter()
    factory_start = time.perf_counter()
    factory = get_session_factory()
    _log_duration("readonly_session_scope.get_factory", factory_start)
    session_start = time.perf_counter()
    session: Session = factory()
    _log_duration("readonly_session_scope.create_session", session_start)
    _apply_session_defaults(session, scope_name="readonly_session_scope")
    try:
        yield session
    except Exception as exc:
        rollback_start = time.perf_counter()
        session.rollback()
        _log_duration("readonly_session_scope.rollback", rollback_start)
        logger.error("Read-only database transaction rolled back: %s", exc, exc_info=True)
        raise
    finally:
        close_start = time.perf_counter()
        session.close()
        _log_duration("readonly_session_scope.close", close_start)
        _log_duration("readonly_session_scope.total", scope_start)


def make_code(value: str, default_prefix: str = "code") -> str:
    """
    Generate a lowercase code usable for code_* columns.
    Ensures the value is not empty by falling back to the provided prefix.
    """
    value = value.strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return slug or default_prefix
