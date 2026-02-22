from __future__ import annotations

import datetime as dt
from decimal import Decimal, InvalidOperation
from typing import Dict, Iterable, List, Optional, Set, Tuple

from sqlalchemy import text


TIME_OFF_REQUEST_TYPES: list[str] = [
    "Vacation",
    "Illness",
    "Personal matters",
    "Appointments and administrative errands",
    "Marriage or civil union",
    "Birth or adoption",
    "Family bereavement",
    "Civic duty",
    "Authorized unpaid leave",
    "Maternity / paternity leave",
]

STATUS_VALUES: tuple[str, ...] = ("requested", "accepted", "denied")

_VACATION_REQUEST_TYPES = {"Vacation", "Personal matters"}
_VACATION_DECREMENT_TYPES = {"Vacation"}


def _normalize_text(value: str | None) -> str:
    return (value or "").strip()


def is_vacation_request(request_type: str | None) -> bool:
    return _normalize_text(request_type) in _VACATION_REQUEST_TYPES


def should_decrement_vacation(request_type: str | None) -> bool:
    return _normalize_text(request_type) in _VACATION_DECREMENT_TYPES


def center_for_request(request_type: str | None) -> str:
    return "Vacation" if is_vacation_request(request_type) else "Leave"


def format_days(value: object) -> str:
    if value is None:
        return "0"
    if isinstance(value, Decimal):
        num = value
    else:
        try:
            num = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            return str(value)
    quantized = num.quantize(Decimal("0.01"))
    text = f"{quantized:f}"
    text = text.rstrip("0").rstrip(".")
    return text or "0"


def build_choices(items: Iterable[str]) -> list[str]:
    choices: list[str] = []
    for item in items:
        text = _normalize_text(item)
        if text and text not in choices:
            choices.append(text)
    return choices


def _expand_holiday_ranges(
    holiday_start: dt.date | None,
    holiday_end: dt.date | None,
    holiday_year: int | None,
    window_start: dt.date,
    window_end: dt.date,
) -> List[Tuple[dt.date, dt.date]]:
    if holiday_start is None or holiday_end is None:
        return []
    year_candidates = [holiday_year] if holiday_year is not None else list(
        range(window_start.year - 1, window_end.year + 2)
    )
    ranges: List[Tuple[dt.date, dt.date]] = []
    for year in year_candidates:
        try:
            start = dt.date(year, holiday_start.month, holiday_start.day)
            end = dt.date(year, holiday_end.month, holiday_end.day)
        except ValueError:
            continue
        if end < start:
            try:
                end = dt.date(year + 1, holiday_end.month, holiday_end.day)
            except ValueError:
                continue
        if end < window_start or start > window_end:
            continue
        ranges.append((max(start, window_start), min(end, window_end)))
    return ranges


def fetch_employee_holidays(
    session,
    employee_id: int,
    start: dt.date,
    end: dt.date,
) -> Set[dt.date]:
    try:
        exists_holidays = session.execute(text("SELECT to_regclass('holidays') AS oid")).scalar()
        exists_variations = session.execute(text("SELECT to_regclass('holidays_variations') AS oid")).scalar()
        exists_employees = session.execute(text("SELECT to_regclass('holidays_employees') AS oid")).scalar()
        if exists_holidays is None or exists_variations is None or exists_employees is None:
            return set()
    except Exception:
        return set()

    rows = session.execute(
        text(
            """
            SELECT
                fv.start_date,
                fv.end_date,
                fv.year
            FROM holidays_variations fv
            JOIN holidays_employees fe
              ON fe.id_holiday = fv.id_holiday
            WHERE fe.id_employee = :employee_id
              AND (fv.year IS NULL OR fv.year BETWEEN :start_year AND :end_year)
            """
        ),
        {"employee_id": employee_id, "start_year": start.year, "end_year": end.year},
    ).fetchall()

    dates: Set[dt.date] = set()
    for row in rows:
        raw_year = getattr(row, "year", None)
        try:
            holiday_year = int(raw_year) if raw_year is not None else None
        except (TypeError, ValueError):
            holiday_year = None
        ranges = _expand_holiday_ranges(
            getattr(row, "start_date", None),
            getattr(row, "end_date", None),
            holiday_year,
            start,
            end,
        )
        for range_start, range_end in ranges:
            current = range_start
            while current <= range_end:
                dates.add(current)
                current += dt.timedelta(days=1)
    return dates


def compute_time_off_days(
    start: dt.date,
    end: dt.date,
    weekend_requested: bool,
    holidays: Set[dt.date],
) -> List[dt.date]:
    included: List[dt.date] = []
    current = start
    while current <= end:
        if current in holidays:
            current += dt.timedelta(days=1)
            continue
        if not weekend_requested and current.weekday() >= 5:
            current += dt.timedelta(days=1)
            continue
        included.append(current)
        current += dt.timedelta(days=1)
    return included


def fetch_accepted_vacation_dates(
    session,
    start: dt.date,
    end: dt.date,
    employee_ids: Optional[Iterable[int]] = None,
) -> Dict[int, Set[dt.date]]:
    if not isinstance(start, dt.date) or not isinstance(end, dt.date):
        return {}
    if end < start:
        start, end = end, start
    if employee_ids is not None:
        employee_ids = [int(value) for value in employee_ids if value is not None]
        if not employee_ids:
            return {}

    query = [
        """
        SELECT
            employee_id,
            start_date,
            end_date,
            weekend_requested,
            request_type
        FROM permissions
        WHERE status = 'accepted'
          AND request_type = ANY(:vacation)
          AND end_date >= :start_date
          AND start_date <= :end_date
        """
    ]
    params = {
        "start_date": start,
        "end_date": end,
        "vacation": list(_VACATION_REQUEST_TYPES),
    }
    if employee_ids is not None:
        query.append("AND employee_id = ANY(:employee_ids)")
        params["employee_ids"] = list(employee_ids)

    rows = session.execute(text("\n".join(query)), params).mappings().all()
    if not rows:
        return {}

    holidays_cache: Dict[int, Set[dt.date]] = {}
    protected: Dict[int, Set[dt.date]] = {}

    def _coerce_date(value: object) -> Optional[dt.date]:
        if isinstance(value, dt.datetime):
            return value.date()
        if isinstance(value, dt.date):
            return value
        return None

    for row in rows:
        employee_id_raw = row.get("employee_id")
        if employee_id_raw is None:
            continue
        employee_id = int(employee_id_raw)
        request_start = _coerce_date(row.get("start_date"))
        request_end = _coerce_date(row.get("end_date"))
        if request_start is None or request_end is None:
            continue
        if request_end < request_start:
            request_start, request_end = request_end, request_start
        range_start = max(start, request_start)
        range_end = min(end, request_end)
        if range_end < range_start:
            continue
        holidays = holidays_cache.get(employee_id)
        if holidays is None:
            holidays = fetch_employee_holidays(session, employee_id, start, end)
            holidays_cache[employee_id] = holidays
        dates = compute_time_off_days(range_start, range_end, bool(row.get("weekend_requested")), holidays)
        if dates:
            protected.setdefault(employee_id, set()).update(dates)

    return protected
