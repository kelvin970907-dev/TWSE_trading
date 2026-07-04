"""Event-study and event-candidate generation helpers."""

from __future__ import annotations

import argparse
import hashlib
import math
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from config.settings import get_settings
from src.backtests.costs import TaiwanCostModel, estimate_net_return
from src.db import get_connection, init_db, upsert_dataframe
from src.features.flow_features import (
    FLOW_FEATURE_COLUMNS,
    add_event_flow_features_from_db,
    ensure_flow_feature_columns,
)


EVENT_CANDIDATE_COLUMNS = [
    "event_id",
    "symbol",
    "trade_date",
    "market",
    "event_type",
    "day0_return",
    "day0_open",
    "day0_high",
    "day0_low",
    "day0_close",
    "day0_volume_shares",
    "day0_turnover_twd",
    "close_location",
    "volume_ratio_20d",
    "touched_limit_up",
    "closed_limit_up",
    "failed_limit_up",
    "next_trade_date",
    *FLOW_FEATURE_COLUMNS,
]

DAILY_PRICE_EVENT_COLUMNS = [
    "symbol",
    "trade_date",
    "market",
    "open",
    "high",
    "low",
    "close",
    "volume_shares",
    "turnover_twd",
    "daily_return",
    "limit_up_price",
    "touched_limit_up",
    "closed_limit_up",
]

EVENT_TYPES = [
    "near_limit_8_9",
    "near_limit_9_10",
    "touched_limit_not_closed",
    "closed_limit_up",
    "failed_limit_up",
]

DAILY_STUDY_RETURN_COLUMNS = [
    "next_open_return",
    "next_high_return",
    "next_low_return",
    "next_close_return",
    "open_to_close_return",
    "open_to_high_return",
    "open_to_low_return",
    "approx_net_next_open_return",
    "approx_net_next_close_return",
    "approx_net_open_to_close_return",
]

DAILY_STUDY_RATE_COLUMNS = [
    "win_next_open",
    "win_next_close",
    "hit_plus_2_intraday",
    "hit_minus_2_intraday",
    "win_net_next_open",
    "win_net_next_close",
]

DEFAULT_TAX_BPS = 15.0
DEFAULT_COMMISSION_BPS = 28.5
DEFAULT_SLIPPAGE_BPS = 10.0


def generate_event_candidates(
    daily_prices: pd.DataFrame,
    *,
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
    markets: Sequence[str] | None = None,
) -> pd.DataFrame:
    """Generate Day 0 event candidates from `daily_prices`.

    `volume_ratio_20d` uses the average volume of the prior 20 rows for the same
    `(market, symbol)`, excluding the current day. `close_location` is undefined
    when `high == low` and is stored as null.
    """

    missing = sorted(set(DAILY_PRICE_EVENT_COLUMNS) - set(daily_prices.columns))
    if missing:
        raise ValueError(f"daily_prices is missing required columns: {missing}")

    start_ts = pd.Timestamp(start).normalize()
    end_ts = pd.Timestamp(end).normalize()
    if end_ts < start_ts:
        raise ValueError(f"end date {end_ts.date()} is before start date {start_ts.date()}")

    market_values = normalize_markets(markets)
    frame = daily_prices.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.normalize()
    frame["market"] = frame["market"].astype("string").str.upper().str.strip()
    frame["symbol"] = frame["symbol"].astype("string").str.strip()
    frame = frame[frame["market"].isin(market_values)].copy()
    if frame.empty:
        return empty_event_candidates()

    numeric_columns = [
        "open",
        "high",
        "low",
        "close",
        "volume_shares",
        "turnover_twd",
        "daily_return",
        "limit_up_price",
    ]
    for column in numeric_columns:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")

    bool_columns = ["touched_limit_up", "closed_limit_up"]
    for column in bool_columns:
        frame[column] = frame[column].fillna(False).astype(bool)

    frame = frame.sort_values(["market", "symbol", "trade_date"]).reset_index(drop=True)
    group_keys = ["market", "symbol"]
    prior_20_volume = (
        frame.groupby(group_keys)["volume_shares"]
        .transform(lambda values: values.shift(1).rolling(window=20, min_periods=1).mean())
    )
    frame["volume_ratio_20d"] = np.where(
        prior_20_volume > 0,
        frame["volume_shares"] / prior_20_volume,
        np.nan,
    )
    frame["next_trade_date"] = frame.groupby(group_keys)["trade_date"].shift(-1)

    price_range = frame["high"] - frame["low"]
    frame["close_location"] = np.where(
        price_range > 0,
        (frame["close"] - frame["low"]) / price_range,
        np.nan,
    )
    frame["failed_limit_up"] = frame["touched_limit_up"] & (
        (frame["close_location"] < 0.8) | (frame["close"] < frame["limit_up_price"])
    )

    in_range = (frame["trade_date"] >= start_ts) & (frame["trade_date"] <= end_ts)
    frame = frame[in_range].copy()
    if frame.empty:
        return empty_event_candidates()

    event_frames = [
        _candidate_rows(
            frame,
            "near_limit_8_9",
            (frame["daily_return"] >= 0.08)
            & (frame["daily_return"] < 0.09)
            & ~frame["touched_limit_up"],
        ),
        _candidate_rows(
            frame,
            "near_limit_9_10",
            (frame["daily_return"] >= 0.09)
            & (frame["daily_return"] < 0.10)
            & ~frame["touched_limit_up"],
        ),
        _candidate_rows(
            frame,
            "touched_limit_not_closed",
            frame["touched_limit_up"] & ~frame["closed_limit_up"],
        ),
        _candidate_rows(frame, "closed_limit_up", frame["closed_limit_up"]),
        _candidate_rows(frame, "failed_limit_up", frame["failed_limit_up"]),
    ]
    events = [event_frame for event_frame in event_frames if not event_frame.empty]
    if not events:
        return empty_event_candidates()

    output = pd.concat(events, ignore_index=True)
    output["event_id"] = output.apply(
        lambda row: make_event_id(
            market=str(row["market"]),
            symbol=str(row["symbol"]),
            trade_date=pd.Timestamp(row["trade_date"]),
            event_type=str(row["event_type"]),
        ),
        axis=1,
    )
    output = ensure_flow_feature_columns(output)
    output = output[EVENT_CANDIDATE_COLUMNS]
    output = output.drop_duplicates(subset=["event_id"], keep="last")
    return output.reset_index(drop=True)


def build_event_candidates(
    *,
    db_path: Path | str,
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
    markets: Sequence[str] | None = None,
) -> int:
    """Build and store event candidates from `daily_prices`."""

    init_db(db_path)
    market_values = normalize_markets(markets)
    with get_connection(db_path) as conn:
        daily_prices = load_daily_prices_for_events(conn)
        candidates = generate_event_candidates(
            daily_prices,
            start=start,
            end=end,
            markets=market_values,
        )
        if not candidates.empty:
            candidates = add_event_flow_features_from_db(conn, candidates)
        delete_event_candidates(conn, start=start, end=end, markets=market_values)
        if candidates.empty:
            return 0
        return upsert_dataframe(
            conn,
            "event_candidates",
            candidates[EVENT_CANDIDATE_COLUMNS],
            ["event_id"],
        )


def load_daily_prices_for_events(conn) -> pd.DataFrame:
    """Load daily price fields needed for event generation."""

    return conn.execute(
        f"""
        SELECT {", ".join(DAILY_PRICE_EVENT_COLUMNS)}
        FROM daily_prices
        ORDER BY market, symbol, trade_date
        """
    ).fetch_df()


def delete_event_candidates(
    conn,
    *,
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
    markets: Sequence[str] | None = None,
) -> None:
    """Delete existing event candidates for a rebuild window."""

    market_values = normalize_markets(markets)
    placeholders = ", ".join(["?"] * len(market_values))
    conn.execute(
        f"""
        DELETE FROM event_candidates
        WHERE trade_date >= ?
          AND trade_date <= ?
          AND market IN ({placeholders})
        """,
        [pd.Timestamp(start).date(), pd.Timestamp(end).date(), *market_values],
    )


def summarize_event_candidates(
    conn,
    *,
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
    markets: Sequence[str] | None = None,
) -> dict[str, pd.DataFrame]:
    """Summarize stored event candidates for a date range."""

    market_values = normalize_markets(markets)
    placeholders = ", ".join(["?"] * len(market_values))
    params: list[Any] = [pd.Timestamp(start).date(), pd.Timestamp(end).date(), *market_values]
    where_sql = f"""
        WHERE trade_date >= ?
          AND trade_date <= ?
          AND market IN ({placeholders})
    """

    by_event_type = conn.execute(
        f"""
        SELECT
            event_type,
            COUNT(*) AS event_count,
            AVG(day0_return) AS avg_day0_return,
            MEDIAN(day0_turnover_twd) AS median_turnover_twd
        FROM event_candidates
        {where_sql}
        GROUP BY event_type
        ORDER BY event_count DESC, event_type
        """,
        params,
    ).fetch_df()
    top_symbols = conn.execute(
        f"""
        SELECT
            market,
            symbol,
            COUNT(*) AS event_count,
            MIN(trade_date) AS first_event_date,
            MAX(trade_date) AS last_event_date
        FROM event_candidates
        {where_sql}
        GROUP BY market, symbol
        ORDER BY event_count DESC, market, symbol
        LIMIT 20
        """,
        params,
    ).fetch_df()
    return {
        "by_event_type": by_event_type,
        "top_20_symbols": top_symbols,
    }


def print_event_summary(
    *,
    db_path: Path | str,
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
    markets: Sequence[str] | None = None,
) -> None:
    """Print event summary tables requested by the CLI."""

    with get_connection(db_path, read_only=True) as conn:
        summary = summarize_event_candidates(
            conn,
            start=start,
            end=end,
            markets=markets,
        )

    print("\ncount / average return / median turnover by event_type")
    print(_format_summary_frame(summary["by_event_type"]))
    print("\ntop 20 most frequent symbols")
    print(_format_summary_frame(summary["top_20_symbols"]))


def run_daily_event_study(
    *,
    db_path: Path | str,
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
    markets: Sequence[str] | None = None,
    output_dir: Path | str | None = None,
    tax_bps: float = DEFAULT_TAX_BPS,
    commission_bps: float = DEFAULT_COMMISSION_BPS,
    slippage_bps: float = DEFAULT_SLIPPAGE_BPS,
    rebuild_events: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Run the daily next-day event study and write CSV reports."""

    init_db(db_path)
    market_values = normalize_markets(markets)
    if rebuild_events:
        build_event_candidates(
            db_path=db_path,
            start=start,
            end=end,
            markets=market_values,
        )

    with get_connection(db_path) as conn:
        events = load_event_candidates_for_daily_study(
            conn,
            start=start,
            end=end,
            markets=market_values,
        )
        daily_prices = load_day1_prices_for_daily_study(conn)

    study = build_daily_study_frame(
        events,
        daily_prices,
        tax_bps=tax_bps,
        commission_bps=commission_bps,
        slippage_bps=slippage_bps,
    )
    summary = summarize_daily_study(study)
    feature_summary = summarize_feature_performance_by_bucket(study)

    report_dir = Path(output_dir) if output_dir is not None else default_report_dir()
    report_dir.mkdir(parents=True, exist_ok=True)
    daily_path = report_dir / "event_study_daily.csv"
    summary_path = report_dir / "event_study_summary.csv"
    feature_path = report_dir / "feature_performance_by_bucket.csv"
    study.to_csv(daily_path, index=False)
    summary.to_csv(summary_path, index=False)
    feature_summary.to_csv(feature_path, index=False)
    return study, summary


def load_event_candidates_for_daily_study(
    conn,
    *,
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
    markets: Sequence[str] | None = None,
) -> pd.DataFrame:
    """Load stored event candidates for daily study."""

    market_values = normalize_markets(markets)
    placeholders = ", ".join(["?"] * len(market_values))
    return conn.execute(
        f"""
        SELECT {", ".join(EVENT_CANDIDATE_COLUMNS)}
        FROM event_candidates
        WHERE trade_date >= ?
          AND trade_date <= ?
          AND market IN ({placeholders})
        ORDER BY market, symbol, trade_date, event_type
        """,
        [pd.Timestamp(start).date(), pd.Timestamp(end).date(), *market_values],
    ).fetch_df()


def load_day1_prices_for_daily_study(conn) -> pd.DataFrame:
    """Load daily price fields needed for Day 1 outcome metrics."""

    return conn.execute(
        """
        SELECT
            symbol,
            market,
            trade_date AS day1_trade_date,
            open AS day1_open,
            high AS day1_high,
            low AS day1_low,
            close AS day1_close,
            volume_shares AS day1_volume_shares,
            turnover_twd AS day1_turnover_twd
        FROM daily_prices
        ORDER BY market, symbol, trade_date
        """
    ).fetch_df()


def build_daily_study_frame(
    events: pd.DataFrame,
    daily_prices: pd.DataFrame,
    *,
    tax_bps: float = DEFAULT_TAX_BPS,
    commission_bps: float = DEFAULT_COMMISSION_BPS,
    slippage_bps: float = DEFAULT_SLIPPAGE_BPS,
) -> pd.DataFrame:
    """Join event candidates to Day 1 daily bars and compute outcome metrics."""

    if events.empty:
        return empty_daily_study_frame()

    missing_events = sorted(set(EVENT_CANDIDATE_COLUMNS) - set(events.columns))
    if missing_events:
        raise ValueError(f"events is missing required columns: {missing_events}")

    day1_columns = {
        "symbol",
        "market",
        "day1_trade_date",
        "day1_open",
        "day1_high",
        "day1_low",
        "day1_close",
        "day1_volume_shares",
        "day1_turnover_twd",
    }
    missing_day1 = sorted(day1_columns - set(daily_prices.columns))
    if missing_day1:
        raise ValueError(f"daily_prices is missing required Day 1 columns: {missing_day1}")

    frame = events.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.normalize()
    frame["next_trade_date"] = pd.to_datetime(frame["next_trade_date"]).dt.normalize()
    frame["market"] = frame["market"].astype("string").str.upper().str.strip()
    frame["symbol"] = frame["symbol"].astype("string").str.strip()

    day1 = daily_prices.copy()
    day1["day1_trade_date"] = pd.to_datetime(day1["day1_trade_date"]).dt.normalize()
    day1["market"] = day1["market"].astype("string").str.upper().str.strip()
    day1["symbol"] = day1["symbol"].astype("string").str.strip()

    frame = frame.merge(
        day1,
        left_on=["market", "symbol", "next_trade_date"],
        right_on=["market", "symbol", "day1_trade_date"],
        how="left",
    )

    numeric_columns = [
        "day0_close",
        "day1_open",
        "day1_high",
        "day1_low",
        "day1_close",
        "day0_turnover_twd",
        "volume_ratio_20d",
        "close_location",
    ]
    for column in numeric_columns:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")

    frame["next_open_return"] = frame["day1_open"] / frame["day0_close"] - 1.0
    frame["next_high_return"] = frame["day1_high"] / frame["day0_close"] - 1.0
    frame["next_low_return"] = frame["day1_low"] / frame["day0_close"] - 1.0
    frame["next_close_return"] = frame["day1_close"] / frame["day0_close"] - 1.0
    frame["open_to_close_return"] = frame["day1_close"] / frame["day1_open"] - 1.0
    frame["open_to_high_return"] = frame["day1_high"] / frame["day1_open"] - 1.0
    frame["open_to_low_return"] = frame["day1_low"] / frame["day1_open"] - 1.0

    frame["win_next_open"] = frame["next_open_return"] > 0
    frame["win_next_close"] = frame["next_close_return"] > 0
    frame["hit_plus_2_intraday"] = frame["open_to_high_return"] >= 0.02
    frame["hit_minus_2_intraday"] = frame["open_to_low_return"] <= -0.02

    approx_cost_bps = tax_bps + commission_bps + slippage_bps
    cost_rate = approx_cost_bps / 10_000.0
    frame["approx_daytrade_cost_bps"] = approx_cost_bps
    frame["approx_net_next_open_return"] = frame["next_open_return"] - cost_rate
    frame["approx_net_next_close_return"] = frame["next_close_return"] - cost_rate
    frame["approx_net_open_to_close_return"] = frame["open_to_close_return"] - cost_rate
    frame["win_net_next_open"] = frame["approx_net_next_open_return"] > 0
    frame["win_net_next_close"] = frame["approx_net_next_close_return"] > 0

    frame["year"] = frame["trade_date"].dt.year
    frame["month"] = frame["trade_date"].dt.strftime("%Y-%m")
    frame["turnover_bucket"] = bucket_turnover(frame["day0_turnover_twd"])
    frame["volume_ratio_20d_bucket"] = bucket_volume_ratio(frame["volume_ratio_20d"])
    frame["close_location_bucket"] = bucket_close_location(frame["close_location"])

    return frame[daily_study_columns()].reset_index(drop=True)


def summarize_daily_study(study: pd.DataFrame) -> pd.DataFrame:
    """Create grouped daily event-study summary rows."""

    if study.empty:
        return empty_daily_study_summary()

    group_specs = [
        ("by_event_type", ["event_type"]),
        ("by_event_type_market", ["event_type", "market"]),
        ("by_event_type_year", ["event_type", "year"]),
        ("by_event_type_month", ["event_type", "month"]),
        ("by_event_type_turnover_bucket", ["event_type", "turnover_bucket"]),
        ("by_event_type_volume_ratio_20d_bucket", ["event_type", "volume_ratio_20d_bucket"]),
        ("by_event_type_close_location_bucket", ["event_type", "close_location_bucket"]),
        (
            "full_cross_section",
            [
                "event_type",
                "market",
                "year",
                "month",
                "turnover_bucket",
                "volume_ratio_20d_bucket",
                "close_location_bucket",
            ],
        ),
    ]

    summary_frames = [
        summarize_daily_study_group(study, summary_level, group_columns)
        for summary_level, group_columns in group_specs
    ]
    return pd.concat(summary_frames, ignore_index=True)


def summarize_feature_performance_by_bucket(study: pd.DataFrame) -> pd.DataFrame:
    """Summarize next-day performance by institutional/margin feature buckets."""

    if study.empty:
        return empty_feature_performance_summary()

    frame = study.copy()
    feature_bucket_specs = [
        ("foreign_net_buy_to_turnover", bucket_signed_ratio),
        ("investment_trust_net_buy_to_turnover", bucket_signed_ratio),
        ("dealer_net_buy_to_turnover", bucket_signed_ratio),
        ("total_institutional_net_buy_to_turnover", bucket_signed_ratio),
        ("margin_balance_change_1d", bucket_balance_change),
        ("short_balance_change_1d", bucket_balance_change),
        ("short_squeeze_proxy", bucket_proxy),
        ("margin_crowding_proxy", bucket_margin_crowding),
    ]
    rows: list[dict[str, Any]] = []
    for feature_name, bucket_func in feature_bucket_specs:
        if feature_name not in frame.columns:
            continue
        bucket_column = f"{feature_name}_bucket"
        frame[bucket_column] = bucket_func(pd.to_numeric(frame[feature_name], errors="coerce"))
        for (event_type, bucket), group in frame.groupby(["event_type", bucket_column], dropna=False):
            rows.append(
                {
                    "feature_name": feature_name,
                    "feature_bucket": bucket,
                    "event_type": event_type,
                    "event_count": int(len(group)),
                    "mean_next_open_return": _series_mean(group["next_open_return"]),
                    "mean_next_close_return": _series_mean(group["next_close_return"]),
                    "mean_open_to_close_return": _series_mean(group["open_to_close_return"]),
                    "mean_approx_net_open_to_close_return": _series_mean(group["approx_net_open_to_close_return"]),
                    "win_next_close_rate": _series_mean(group["win_next_close"]),
                    "hit_plus_2_intraday_rate": _series_mean(group["hit_plus_2_intraday"]),
                }
            )
    if not rows:
        return empty_feature_performance_summary()
    return pd.DataFrame(rows, columns=feature_performance_summary_columns())


def bucket_signed_ratio(values: pd.Series) -> pd.Series:
    buckets = pd.cut(
        values,
        bins=[-np.inf, -0.10, -0.03, 0.0, 0.03, 0.10, np.inf],
        labels=["<=-10%", "-10%_-3%", "-3%_0", "0_3%", "3%_10%", ">10%"],
    )
    return buckets.astype("string").fillna("missing")


def bucket_balance_change(values: pd.Series) -> pd.Series:
    buckets = pd.cut(
        values,
        bins=[-np.inf, -10_000, -1, 0, 1, 10_000, np.inf],
        labels=["large_down", "down", "flat_zero", "up_small", "up_large", "very_large_up"],
    )
    return buckets.astype("string").fillna("missing")


def bucket_proxy(values: pd.Series) -> pd.Series:
    buckets = pd.cut(
        values,
        bins=[-np.inf, -0.25, 0.0, 0.25, 1.0, np.inf],
        labels=["negative", "flat_or_small_down", "0_25%", "25_100%", ">100%"],
    )
    return buckets.astype("string").fillna("missing")


def bucket_margin_crowding(values: pd.Series) -> pd.Series:
    buckets = pd.cut(
        values,
        bins=[-np.inf, 0.5, 1.0, 2.0, 5.0, np.inf],
        labels=["<=0.5x", "0.5_1x", "1_2x", "2_5x", ">5x"],
    )
    return buckets.astype("string").fillna("missing")


def summarize_daily_study_group(
    study: pd.DataFrame,
    summary_level: str,
    group_columns: list[str],
) -> pd.DataFrame:
    """Summarize one grouping level."""

    rows: list[dict[str, Any]] = []
    grouped = study.groupby(group_columns, dropna=False, observed=False)
    for group_key, group in grouped:
        key_values = group_key if isinstance(group_key, tuple) else (group_key,)
        row: dict[str, Any] = {
            "summary_level": summary_level,
            "event_type": None,
            "market": None,
            "year": None,
            "month": None,
            "turnover_bucket": None,
            "volume_ratio_20d_bucket": None,
            "close_location_bucket": None,
            "event_count": int(len(group)),
            "day1_observation_count": int(group["next_close_return"].notna().sum()),
            "avg_day0_return": _series_mean(group["day0_return"]),
            "median_day0_turnover_twd": _series_median(group["day0_turnover_twd"]),
            "avg_approx_daytrade_cost_bps": _series_mean(group["approx_daytrade_cost_bps"]),
        }
        for column, value in zip(group_columns, key_values):
            row[column] = value

        for return_column in DAILY_STUDY_RETURN_COLUMNS:
            values = pd.to_numeric(group[return_column], errors="coerce").dropna()
            row[f"mean_{return_column}"] = _series_mean(values)
            row[f"median_{return_column}"] = _series_median(values)
            row[f"t_stat_{return_column}"] = one_sample_t_stat(values)
            row[f"p_value_{return_column}_gt_0"] = one_sided_positive_p_value(values)

        for rate_column in DAILY_STUDY_RATE_COLUMNS:
            row[f"{rate_column}_rate"] = _series_mean(group[rate_column])

        rows.append(row)

    return pd.DataFrame(rows, columns=daily_study_summary_columns())


def bucket_turnover(values: pd.Series) -> pd.Series:
    buckets = pd.cut(
        values,
        bins=[-np.inf, 10_000_000, 50_000_000, 200_000_000, 1_000_000_000, np.inf],
        labels=["<10m", "10m_50m", "50m_200m", "200m_1b", ">=1b"],
    )
    return buckets.astype("string").fillna("unknown")


def bucket_volume_ratio(values: pd.Series) -> pd.Series:
    buckets = pd.cut(
        values,
        bins=[-np.inf, 0.5, 1.0, 2.0, 5.0, np.inf],
        labels=["<0.5", "0.5_1", "1_2", "2_5", ">=5"],
    )
    return buckets.astype("string").fillna("unknown")


def bucket_close_location(values: pd.Series) -> pd.Series:
    buckets = pd.cut(
        values,
        bins=[-np.inf, 0.2, 0.4, 0.6, 0.8, np.inf],
        labels=["<=0.2", "0.2_0.4", "0.4_0.6", "0.6_0.8", ">0.8"],
    )
    return buckets.astype("string").fillna("unknown")


def one_sample_t_stat(values: pd.Series) -> float:
    clean = pd.to_numeric(values, errors="coerce").dropna()
    if len(clean) < 2:
        return np.nan
    std = clean.std(ddof=1)
    if std == 0 or pd.isna(std):
        return np.nan
    return float(clean.mean() / (std / math.sqrt(len(clean))))


def one_sided_positive_p_value(values: pd.Series) -> float:
    """Normal-approx one-sided p-value for mean > 0."""

    t_stat = one_sample_t_stat(values)
    if pd.isna(t_stat):
        return np.nan
    return float(0.5 * math.erfc(t_stat / math.sqrt(2.0)))


def daily_study_columns() -> list[str]:
    return EVENT_CANDIDATE_COLUMNS + [
        "day1_trade_date",
        "day1_open",
        "day1_high",
        "day1_low",
        "day1_close",
        "day1_volume_shares",
        "day1_turnover_twd",
        "next_open_return",
        "next_high_return",
        "next_low_return",
        "next_close_return",
        "open_to_close_return",
        "open_to_high_return",
        "open_to_low_return",
        "win_next_open",
        "win_next_close",
        "hit_plus_2_intraday",
        "hit_minus_2_intraday",
        "approx_daytrade_cost_bps",
        "approx_net_next_open_return",
        "approx_net_next_close_return",
        "approx_net_open_to_close_return",
        "win_net_next_open",
        "win_net_next_close",
        "year",
        "month",
        "turnover_bucket",
        "volume_ratio_20d_bucket",
        "close_location_bucket",
    ]


def daily_study_summary_columns() -> list[str]:
    base_columns = [
        "summary_level",
        "event_type",
        "market",
        "year",
        "month",
        "turnover_bucket",
        "volume_ratio_20d_bucket",
        "close_location_bucket",
        "event_count",
        "day1_observation_count",
        "avg_day0_return",
        "median_day0_turnover_twd",
        "avg_approx_daytrade_cost_bps",
    ]
    return_columns: list[str] = []
    for return_column in DAILY_STUDY_RETURN_COLUMNS:
        return_columns.extend(
            [
                f"mean_{return_column}",
                f"median_{return_column}",
                f"t_stat_{return_column}",
                f"p_value_{return_column}_gt_0",
            ]
        )
    rate_columns = [f"{column}_rate" for column in DAILY_STUDY_RATE_COLUMNS]
    return base_columns + return_columns + rate_columns


def empty_daily_study_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=daily_study_columns())


def empty_daily_study_summary() -> pd.DataFrame:
    return pd.DataFrame(columns=daily_study_summary_columns())


def feature_performance_summary_columns() -> list[str]:
    return [
        "feature_name",
        "feature_bucket",
        "event_type",
        "event_count",
        "mean_next_open_return",
        "mean_next_close_return",
        "mean_open_to_close_return",
        "mean_approx_net_open_to_close_return",
        "win_next_close_rate",
        "hit_plus_2_intraday_rate",
    ]


def empty_feature_performance_summary() -> pd.DataFrame:
    return pd.DataFrame(columns=feature_performance_summary_columns())


def default_report_dir() -> Path:
    return get_settings().project_root / "reports"


def _series_mean(values: pd.Series) -> float:
    clean = pd.to_numeric(values, errors="coerce").dropna()
    if clean.empty:
        return np.nan
    return float(clean.mean())


def _series_median(values: pd.Series) -> float:
    clean = pd.to_numeric(values, errors="coerce").dropna()
    if clean.empty:
        return np.nan
    return float(clean.median())


def make_event_id(
    *,
    market: str,
    symbol: str,
    trade_date: pd.Timestamp,
    event_type: str,
) -> str:
    """Create a deterministic event id."""

    raw = f"{market.upper()}|{symbol}|{trade_date.strftime('%Y%m%d')}|{event_type}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"{market.upper()}:{symbol}:{trade_date.strftime('%Y%m%d')}:{event_type}:{digest}"


def normalize_markets(markets: Sequence[str] | None) -> list[str]:
    """Normalize market selectors for event queries."""

    if markets is None:
        return ["TWSE", "TPEX"]
    normalized: list[str] = []
    for market in markets:
        value = str(market).upper().strip()
        if value == "BOTH":
            normalized.extend(["TWSE", "TPEX"])
        elif value in {"TWSE", "TPEX"}:
            normalized.append(value)
        else:
            raise ValueError("markets must contain only TWSE, TPEX, or BOTH")
    return sorted(set(normalized))


def empty_event_candidates() -> pd.DataFrame:
    return pd.DataFrame(columns=EVENT_CANDIDATE_COLUMNS)


def _candidate_rows(frame: pd.DataFrame, event_type: str, mask: pd.Series) -> pd.DataFrame:
    selected = frame[mask.fillna(False)].copy()
    if selected.empty:
        return empty_event_candidates()

    selected["event_id"] = ""
    selected["event_type"] = event_type
    selected["day0_return"] = selected["daily_return"]
    selected["day0_open"] = selected["open"]
    selected["day0_high"] = selected["high"]
    selected["day0_low"] = selected["low"]
    selected["day0_close"] = selected["close"]
    selected["day0_volume_shares"] = selected["volume_shares"].astype("Int64")
    selected["day0_turnover_twd"] = selected["turnover_twd"]
    selected = ensure_flow_feature_columns(selected)
    return selected[EVENT_CANDIDATE_COLUMNS]


def _format_summary_frame(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "(no rows)"
    return frame.to_string(index=False)


def attach_next_session_prices(
    daily_prices: pd.DataFrame,
    entry_col: str = "open",
    exit_col: str = "close",
) -> pd.DataFrame:
    """Attach next-session entry and exit prices to each daily row."""

    frame = daily_prices.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"])
    symbol_col = "symbol" if "symbol" in frame.columns else "stock_id"
    frame = frame.sort_values(["market", symbol_col, "trade_date"]).reset_index(drop=True)
    keys = ["market", symbol_col]
    frame["entry_date"] = frame.groupby(keys)["trade_date"].shift(-1)
    frame["entry_price"] = frame.groupby(keys)[entry_col].shift(-1)
    frame["exit_date"] = frame.groupby(keys)["trade_date"].shift(-1)
    frame["exit_price"] = frame.groupby(keys)[exit_col].shift(-1)
    frame["gross_return"] = frame["exit_price"] / frame["entry_price"] - 1.0
    return frame


def event_study_next_day(
    daily_with_features: pd.DataFrame,
    event_column: str,
    cost_model: TaiwanCostModel | None = None,
    min_dollar_volume: float | None = None,
) -> pd.DataFrame:
    """Build next-day open-to-close trades for rows where `event_column` is True."""

    if event_column not in daily_with_features.columns:
        raise ValueError(f"Event column not found: {event_column}")

    symbol_col = "symbol" if "symbol" in daily_with_features.columns else "stock_id"
    frame = attach_next_session_prices(daily_with_features)
    events = frame[frame[event_column].fillna(False)].copy()
    liquidity_col = "turnover_twd" if "turnover_twd" in events.columns else "dollar_volume"
    if min_dollar_volume is not None and liquidity_col in events.columns:
        events = events[events[liquidity_col] >= min_dollar_volume].copy()

    events = events.dropna(subset=["entry_price", "exit_price"])
    events["event_name"] = event_column
    events["net_return"] = events["gross_return"].map(
        lambda value: estimate_net_return(value, model=cost_model)
    )
    return events[
        [
            "trade_date",
            symbol_col,
            "market",
            "event_name",
            "entry_date",
            "entry_price",
            "exit_date",
            "exit_price",
            "gross_return",
            "net_return",
        ]
    ].rename(columns={"trade_date": "event_date", symbol_col: "symbol"})


def main() -> None:
    parser = argparse.ArgumentParser(description="Event-study utilities")
    subparsers = parser.add_subparsers(dest="command")

    build_parser = subparsers.add_parser(
        "build-events",
        help="Build event_candidates from daily_prices",
    )
    build_parser.add_argument("--db", type=Path, default=get_settings().db_path, help="DuckDB path")
    build_parser.add_argument("--start", required=True, help="Start date, YYYY-MM-DD")
    build_parser.add_argument("--end", required=True, help="End date, YYYY-MM-DD")
    build_parser.add_argument(
        "--market",
        choices=["TWSE", "TPEX", "BOTH"],
        default="BOTH",
        help="Market selector",
    )

    daily_parser = subparsers.add_parser(
        "run-daily-study",
        help="Run next-day daily event study and write CSV reports",
    )
    daily_parser.add_argument("--db", type=Path, default=get_settings().db_path, help="DuckDB path")
    daily_parser.add_argument("--start", required=True, help="Start date, YYYY-MM-DD")
    daily_parser.add_argument("--end", required=True, help="End date, YYYY-MM-DD")
    daily_parser.add_argument(
        "--market",
        choices=["TWSE", "TPEX", "BOTH"],
        default="BOTH",
        help="Market selector",
    )
    daily_parser.add_argument(
        "--output-dir",
        type=Path,
        default=default_report_dir(),
        help="Directory for event_study_daily.csv and event_study_summary.csv",
    )
    daily_parser.add_argument("--tax-bps", type=float, default=DEFAULT_TAX_BPS)
    daily_parser.add_argument("--commission-bps", type=float, default=DEFAULT_COMMISSION_BPS)
    daily_parser.add_argument("--slippage-bps", type=float, default=DEFAULT_SLIPPAGE_BPS)
    daily_parser.add_argument(
        "--skip-build-events",
        action="store_true",
        help="Use existing event_candidates instead of rebuilding for the window",
    )

    args = parser.parse_args()
    if args.command == "build-events":
        markets = normalize_markets([args.market])
        inserted = build_event_candidates(
            db_path=args.db,
            start=args.start,
            end=args.end,
            markets=markets,
        )
        print(f"Built {inserted} event candidate rows")
        print_event_summary(
            db_path=args.db,
            start=args.start,
            end=args.end,
            markets=markets,
        )
    elif args.command == "run-daily-study":
        markets = normalize_markets([args.market])
        study, summary = run_daily_event_study(
            db_path=args.db,
            start=args.start,
            end=args.end,
            markets=markets,
            output_dir=args.output_dir,
            tax_bps=args.tax_bps,
            commission_bps=args.commission_bps,
            slippage_bps=args.slippage_bps,
            rebuild_events=not args.skip_build_events,
        )
        print(f"Wrote {len(study)} event rows to {args.output_dir / 'event_study_daily.csv'}")
        print(f"Wrote {len(summary)} summary rows to {args.output_dir / 'event_study_summary.csv'}")

        near_limit = summary[
            (summary["summary_level"] == "by_event_type")
            & (summary["event_type"] == "near_limit_8_9")
        ]
        if near_limit.empty:
            print("\nnear_limit_8_9 after-cost question: no events in this window")
        else:
            row = near_limit.iloc[0]
            print("\nnear_limit_8_9 after-cost question")
            print(
                "mean approx_net_next_open_return: "
                f"{row['mean_approx_net_next_open_return']:.6f}, "
                "p_value_gt_0: "
                f"{row['p_value_approx_net_next_open_return_gt_0']:.6f}"
            )
            print(
                "mean approx_net_next_close_return: "
                f"{row['mean_approx_net_next_close_return']:.6f}, "
                "p_value_gt_0: "
                f"{row['p_value_approx_net_next_close_return_gt_0']:.6f}"
            )
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
