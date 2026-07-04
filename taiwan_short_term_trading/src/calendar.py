"""Trading-calendar helpers.

This module provides a conservative weekday calendar with optional holiday
overrides. Replace or extend it with official Taiwan exchange holidays before
running final production-grade studies.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Iterable

import pandas as pd


def _holiday_set(holidays: Iterable[date | str] | None) -> set[pd.Timestamp]:
    if holidays is None:
        return set()
    return {pd.Timestamp(day).normalize() for day in holidays}


def is_trading_day(day: date | str | pd.Timestamp, holidays: Iterable[date | str] | None = None) -> bool:
    """Return True when a date is a weekday and not in the supplied holidays."""

    ts = pd.Timestamp(day).normalize()
    return ts.weekday() < 5 and ts not in _holiday_set(holidays)


def trading_days(
    start: date | str | pd.Timestamp,
    end: date | str | pd.Timestamp,
    holidays: Iterable[date | str] | None = None,
) -> pd.DatetimeIndex:
    """Return weekday trading days between start and end, inclusive."""

    holiday_values = _holiday_set(holidays)
    days = pd.date_range(start=start, end=end, freq="B")
    if not holiday_values:
        return days
    return pd.DatetimeIndex([day for day in days if day.normalize() not in holiday_values])


def next_trading_day(day: date | str | pd.Timestamp, holidays: Iterable[date | str] | None = None) -> pd.Timestamp:
    """Return the next trading day after the supplied date."""

    current = pd.Timestamp(day).normalize() + timedelta(days=1)
    while not is_trading_day(current, holidays=holidays):
        current += timedelta(days=1)
    return current


def previous_trading_day(day: date | str | pd.Timestamp, holidays: Iterable[date | str] | None = None) -> pd.Timestamp:
    """Return the previous trading day before the supplied date."""

    current = pd.Timestamp(day).normalize() - timedelta(days=1)
    while not is_trading_day(current, holidays=holidays):
        current -= timedelta(days=1)
    return current
