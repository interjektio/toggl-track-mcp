"""Date and time helpers.

Every datetime produced here is timezone-aware. Never use naive datetimes or
`datetime.utcnow()`; call `utc_now()` instead.
"""

import re
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .errors import DateRangeError

_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def utc_now() -> datetime:
    """Return the current time as an aware UTC datetime."""
    return datetime.now(UTC)


def load_timezone(name: str | None) -> tzinfo | None:
    """Return the IANA timezone `name`, or None if it is empty or unknown."""
    if not name:
        return None
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError, ValueError:
        return None


def parse_date(value: str, field: str) -> date:
    """Parse a strict YYYY-MM-DD date."""
    text = value.strip()
    if not _DATE_RE.fullmatch(text):
        raise DateRangeError(f"{field} must be a date in YYYY-MM-DD format, got {text[:40]!r}.")
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise DateRangeError(f"{field} is not a valid calendar date: {text!r}.") from exc


def resolve_date_range(
    start: str, end: str, *, default_days: int, today: date
) -> tuple[date, date]:
    """Resolve optional, inclusive YYYY-MM-DD bounds.

    A missing end defaults to `today`; a missing start defaults to the
    `default_days`-day window that ends on (and includes) the end date.
    """
    end_day = parse_date(end, "end_date") if end.strip() else today
    if start.strip():
        start_day = parse_date(start, "start_date")
    else:
        start_day = end_day - timedelta(days=default_days - 1)
    if start_day > end_day:
        raise DateRangeError(
            f"start_date {start_day.isoformat()} is after end_date {end_day.isoformat()}."
        )
    return start_day, end_day


def local_day_bounds(start: date, end: date, tz: tzinfo) -> tuple[datetime, datetime]:
    """Return the UTC instants [start 00:00, day after end 00:00) in timezone `tz`.

    This makes `end` inclusive: every entry that starts on the local calendar
    day `end` is inside the range.
    """
    lower = datetime.combine(start, time.min, tzinfo=tz).astimezone(UTC)
    upper = datetime.combine(end + timedelta(days=1), time.min, tzinfo=tz).astimezone(UTC)
    return lower, upper


def format_rfc3339_utc(moment: datetime) -> str:
    """Format an aware datetime as RFC 3339 in UTC with a `Z` suffix.

    A `Z` suffix avoids `+hh:mm` offsets, whose `+` some servers decode as a space.
    """
    if moment.tzinfo is None:
        raise ValueError("refusing to format a naive datetime")
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_api_datetime(value: object) -> datetime | None:
    """Parse an ISO 8601 timestamp from the API into an aware datetime."""
    if not isinstance(value, str) or not value:
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    if moment.tzinfo is None:
        # Toggl documents all timestamps as UTC.
        moment = moment.replace(tzinfo=UTC)
    return moment


def format_duration(seconds: float) -> str:
    """Format a number of seconds as e.g. `3h 05m`."""
    total_minutes = int(max(seconds, 0) // 60)
    hours, minutes = divmod(total_minutes, 60)
    return f"{hours}h {minutes:02d}m"
