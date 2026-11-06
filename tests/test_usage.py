from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from custom_components.genesisenergy.usage import (
    backfill_days,
    build_statistics,
    date_chunks,
    fetch_start_date,
    merge_usage,
    previous_sum,
)

NZDT = timezone(timedelta(hours=13))
TODAY = date(2026, 11, 7)


def usage(start: str, kw: float = 1.0, cost: float = 0.5):
    return {"startDate": start, "kw": kw, "costNZD": cost}


def test_backfill_days_prefers_options_then_data_then_default():
    assert backfill_days({"backfill_days": 30}, {"backfill_days": 365}) == 365
    assert backfill_days({"backfill_days": 30}, {}) == 30
    assert backfill_days({}, {}) == 90


def test_fetch_start_backfills_full_history_when_pending():
    assert fetch_start_date(TODAY, None, 365, True) == TODAY - timedelta(days=365)


def test_fetch_start_defaults_to_refresh_window():
    recent = datetime(2026, 11, 6, 10, tzinfo=UTC)
    assert fetch_start_date(TODAY, recent, 365, False) == date(2026, 11, 3)
    assert fetch_start_date(TODAY, None, 365, False) == date(2026, 11, 3)


def test_fetch_start_fills_gap_after_downtime():
    stale = datetime(2026, 10, 20, 5, tzinfo=UTC)
    assert fetch_start_date(TODAY, stale, 365, False) == date(2026, 10, 19)


def test_fetch_start_gap_fill_is_bounded_by_history_setting():
    ancient = datetime(2025, 1, 1, tzinfo=UTC)
    assert fetch_start_date(TODAY, ancient, 30, False) == TODAY - timedelta(days=30)


def test_fetch_start_never_shrinks_below_refresh_window():
    assert fetch_start_date(TODAY, None, 0, True) == date(2026, 11, 3)


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        (date(2026, 1, 1), date(2026, 1, 1), [(date(2026, 1, 1), date(2026, 1, 1))]),
        (
            date(2026, 1, 1),
            date(2026, 1, 10),
            [
                (date(2026, 1, 1), date(2026, 1, 4)),
                (date(2026, 1, 5), date(2026, 1, 8)),
                (date(2026, 1, 9), date(2026, 1, 10)),
            ],
        ),
        (date(2026, 1, 2), date(2026, 1, 1), []),
    ],
)
def test_date_chunks(start, end, expected):
    assert date_chunks(start, end) == expected


def test_date_chunks_cover_range_without_overlap():
    chunks = date_chunks(date(2024, 11, 7), TODAY)
    assert chunks[0][0] == date(2024, 11, 7)
    assert chunks[-1][1] == TODAY
    for (_, prev_end), (next_start, _) in zip(chunks, chunks[1:], strict=False):
        assert next_start == prev_end + timedelta(days=1)


def test_merge_usage_dedupes_and_sorts():
    a = usage("2026-11-01T01:00:00+13:00", kw=1)
    b = usage("2026-11-01T00:00:00+13:00", kw=2)
    a_dupe = usage("2026-11-01T01:00:00+13:00", kw=3)
    merged = merge_usage([[a, b], [a_dupe]])
    assert [m["kw"] for m in merged] == [2, 3]


def test_merge_usage_handles_dst_offsets():
    before = usage("2026-09-27T01:00:00+12:00")
    after = usage("2026-09-27T03:00:00+13:00")
    assert merge_usage([[after, before]]) == [before, after]


def test_previous_sum_uses_newest_stat_before_cutoff():
    cutoff = datetime(2026, 11, 3, 0, tzinfo=NZDT)
    stats = [
        {"start": (cutoff + timedelta(hours=1)).timestamp(), "sum": 99},
        {"start": (cutoff - timedelta(hours=1)).timestamp(), "sum": 10},
        {"start": (cutoff - timedelta(hours=2)).timestamp(), "sum": 9},
    ]
    assert previous_sum(stats, cutoff) == 10


def test_previous_sum_skips_missing_sums_and_defaults_to_zero():
    cutoff = datetime(2026, 11, 3, tzinfo=NZDT)
    assert previous_sum([{"start": cutoff.timestamp() - 3600, "sum": None}], cutoff) == 0
    assert previous_sum([], cutoff) == 0


def test_build_statistics_accumulates_from_previous_sums():
    kw, cost = build_statistics(
        [
            usage("2026-11-03T00:00:00+13:00", kw=1.25, cost=0.3),
            usage("2026-11-03T01:00:00+13:00", kw=0.5, cost=0.2),
        ],
        kw_sum=10,
        cost_sum=5,
    )
    assert [s["sum"] for s in kw] == [11.25, 11.75]
    assert [s["state"] for s in kw] == [1.25, 0.5]
    assert [s["sum"] for s in cost] == [5.3, 5.5]
    assert kw[0]["start"] == datetime(2026, 11, 3, 0, tzinfo=NZDT)
