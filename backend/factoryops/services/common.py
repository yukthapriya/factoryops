from datetime import UTC, datetime, timedelta

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from factoryops.errors import BadRequest

MAX_WINDOW = timedelta(days=31)


def resolve_window(
    start: datetime | None,
    end: datetime | None,
    default: timedelta = timedelta(hours=24),
    max_window: timedelta = MAX_WINDOW,
) -> tuple[datetime, datetime]:
    """Normalize a [start, end) range to UTC, applying defaults and an upper bound."""
    if end is not None and end.tzinfo is None:
        raise BadRequest("end must include a timezone offset")
    end = end.astimezone(UTC) if end else datetime.now(UTC)
    if start is not None and start.tzinfo is None:
        raise BadRequest("start must include a timezone offset")
    start = start.astimezone(UTC) if start else end - default
    if start >= end:
        raise BadRequest("start must be before end")
    if end - start > max_window:
        raise BadRequest(f"time range may not exceed {max_window.days} days")
    return start, end


def bucket_seconds_for(start: datetime, end: datetime) -> int:
    span = (end - start).total_seconds()
    for limit, bucket in ((3600, 60), (6 * 3600, 300), (24 * 3600, 900), (7 * 86400, 3600)):
        if span <= limit:
            return bucket
    return 6 * 3600


def bucket_expr(column, seconds: int, origin: datetime):
    return func.date_bin(func.make_interval(0, 0, 0, 0, 0, 0, seconds), column, origin)


def paginate(session: Session, stmt: Select, page: int, page_size: int) -> tuple[list, int]:
    total = session.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    rows = session.scalars(stmt.limit(page_size).offset((page - 1) * page_size)).unique().all()
    return list(rows), total


def parse_sort(sort: str | None, allowed: dict, default):
    if not sort:
        return default
    desc = sort.startswith("-")
    key = sort.lstrip("-")
    if key not in allowed:
        raise BadRequest(f"sort must be one of {sorted(allowed)} (prefix with '-' for descending)")
    col = allowed[key]
    return col.desc() if desc else col.asc()
