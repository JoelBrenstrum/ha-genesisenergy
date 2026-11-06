"""Pure helpers for turning Genesis Energy usage into recorder statistics."""

from collections.abc import Iterable, Mapping
from datetime import date, datetime, timedelta
from typing import Any

from .const import CONF_BACKFILL_DAYS, DEFAULT_BACKFILL_DAYS

REFRESH_DAYS = 4
CHUNK_DAYS = 4


def backfill_days(data: Mapping[str, Any], options: Mapping[str, Any]) -> int:
    """Return the configured number of days of history to import."""
    return int(options.get(CONF_BACKFILL_DAYS, data.get(CONF_BACKFILL_DAYS, DEFAULT_BACKFILL_DAYS)))


def fetch_start_date(
    today: date,
    latest_stat_start: datetime | None,
    history_days: int,
    backfill_pending: bool,
) -> date:
    """Return the first day to request from the API."""
    floor = today - timedelta(days=max(history_days, REFRESH_DAYS))
    if backfill_pending:
        return floor
    start = today - timedelta(days=REFRESH_DAYS)
    if latest_stat_start is not None:
        start = min(start, latest_stat_start.date() - timedelta(days=1))
    return max(start, floor)


def date_chunks(start: date, end: date, chunk_days: int = CHUNK_DAYS) -> list[tuple[date, date]]:
    """Split an inclusive date range into inclusive chunks of at most chunk_days days."""
    chunks = []
    current = start
    while current <= end:
        chunk_end = min(current + timedelta(days=chunk_days - 1), end)
        chunks.append((current, chunk_end))
        current = chunk_end + timedelta(days=1)
    return chunks


def parse_start(entry: Mapping[str, Any]) -> datetime:
    """Return the timezone-aware start of a usage entry."""
    return datetime.fromisoformat(entry["startDate"])


def merge_usage(chunks: Iterable[Iterable[Mapping[str, Any]]]) -> list[Mapping[str, Any]]:
    """Combine usage chunks, dropping duplicate hours and sorting by start time."""
    by_start = {}
    for chunk in chunks:
        for entry in chunk:
            by_start[parse_start(entry)] = entry
    return [by_start[start] for start in sorted(by_start)]


def previous_sum(stats: Iterable[Mapping[str, Any]], before: datetime) -> float:
    """Return the running sum of the newest statistic that starts before the given time."""
    cutoff = before.timestamp()
    for stat in sorted(stats, key=lambda s: s["start"], reverse=True):
        if stat["start"] < cutoff and stat.get("sum") is not None:
            return stat["sum"]
    return 0


def build_statistics(
    usage: Iterable[Mapping[str, Any]], kw_sum: float, cost_sum: float
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return hourly consumption and cost statistics with running sums."""
    kw_statistics = []
    cost_statistics = []
    for entry in usage:
        start = parse_start(entry)
        kw_sum += entry["kw"]
        cost_sum += entry["costNZD"]
        kw_statistics.append({"start": start, "state": entry["kw"], "sum": round(kw_sum, 2)})
        cost_statistics.append(
            {"start": start, "state": entry["costNZD"], "sum": round(cost_sum, 2)}
        )
    return kw_statistics, cost_statistics
