from __future__ import annotations

from contextlib import contextmanager
import contextvars
from dataclasses import dataclass
import functools
import logging
import time
from typing import Callable, Generator, Optional, TypeVar

DEFAULT_LOGGER_NAME = "uvicorn.error"
logger = logging.getLogger(DEFAULT_LOGGER_NAME)

T = TypeVar("T")

_CURRENT_TIMING: contextvars.ContextVar["PageTiming | None"] = contextvars.ContextVar(
    "page_timing", default=None
)


@dataclass
class PageTiming:
    page: str
    callback: str
    start: float
    sql_seconds: float = 0.0

    def add_sql(self, seconds: float) -> None:
        self.sql_seconds += seconds


def has_active_timing() -> bool:
    return _CURRENT_TIMING.get() is not None


def record_sql_time(seconds: float) -> None:
    timing = _CURRENT_TIMING.get()
    if timing is None:
        return
    timing.add_sql(seconds)


@contextmanager
def page_load_timing(
    page: str, callback: str, log: Optional[logging.Logger] = None
) -> Generator[PageTiming, None, None]:
    start = time.perf_counter()
    timing = PageTiming(page=page, callback=callback, start=start)
    token = _CURRENT_TIMING.set(timing)
    try:
        yield timing
    finally:
        total = time.perf_counter() - start
        sql_seconds = timing.sql_seconds
        non_sql = total - sql_seconds
        if non_sql < 0:
            non_sql = 0.0
        resolved_log = log or logger
        resolved_log.info(
            "page_load.timing page=%s callback=%s total_ms=%.2f sql_ms=%.2f non_sql_ms=%.2f",
            page,
            callback,
            total * 1000,
            sql_seconds * 1000,
            non_sql * 1000,
        )
        _CURRENT_TIMING.reset(token)


def timed_page_load(
    page: str,
    func: Callable[..., T],
    label: Optional[str] = None,
    log: Optional[logging.Logger] = None,
) -> Callable[..., T]:
    callback = label or func.__name__
    resolved_logger = log or logging.getLogger(DEFAULT_LOGGER_NAME)

    @functools.wraps(func)
    def _wrapped(*args, **kwargs) -> T:
        with page_load_timing(page, callback, resolved_logger):
            return func(*args, **kwargs)

    return _wrapped
