"""Closed-limit-up overnight gap-capture event study.

This module studies the hypothesis that stocks closing limit-up on Day 0 have
unresolved demand that is expressed in the Day 1 opening auction/gap. It is a
pure event-study report: it does not write simulated trades to backtest_trades.
"""

from __future__ import annotations

import argparse
import math
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from config.settings import get_settings
from src.backtests.event_study import (
    EVENT_CANDIDATE_COLUMNS,
    build_event_candidates,
    normalize_markets,
)
from src.db import get_connection, init_db


EVENT_TYPE = "closed_limit_up"
DEFAULT_COST_SHARES = 1000

GAP_RETURN_COLUMNS = [
    "day1_open_gap",
    "day1_high_gap",
    "day1_low_gap",
    "day1_close_gap",
    "day1_open_to_close",
    "day1_open_to_low",
    "day1_open_to_high",
    "overnight_net_return",
    "daytrade_net_open_to_close_return",
]

RATE_COLUMNS = [
    "win_day1_open_gap",
    "win_overnight_net",
    "win_day1_open_to_close",
    "win_daytrade_net_open_to_close",
]

GROUP_VALUE_COLUMNS = [
    "market",
    "year",
    "month",
    "turnover_bucket",
    "volume_ratio_20d_bucket",
    "consecutive_limit_up_count",
    "day0_turnover_bucket",
    "day0_price_bucket",
    "day0_volume_shock_bucket",
    "prior_5d_return_bucket",
    "prior_20d_return_bucket",
]


def run_limit_up_gap_capture(
    *,
    db_path: Path | str,
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
    markets: Sequence[str] | None = None,
    min_turnover_twd: float | None = None,
    min_volume_ratio_20d: float | None = None,
    min_close_location: float | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    max_consecutive_limit_ups: int | None = None,
    max_prior_consecutive_limit_ups: int | None = None,
    first_limit_up_only: bool = False,
    require_day_minus1_positive: bool = False,
    commission_rate: float = 0.001425,
    commission_discount: float = 0.28,
    normal_sell_tax_rate: float = 0.003,
    day_trade_sell_tax_rate: float = 0.0015,
    slippage_bps_per_side: float = 5.0,
    minimum_commission_twd: float = 20.0,
    cost_shares: int = DEFAULT_COST_SHARES,
    output_dir: Path | str | None = None,
    rebuild_events: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Run the closed-limit-up overnight gap-capture event study."""

    validate_inputs(
        start=start,
        end=end,
        min_turnover_twd=min_turnover_twd,
        min_volume_ratio_20d=min_volume_ratio_20d,
        min_close_location=min_close_location,
        min_price=min_price,
        max_price=max_price,
        max_consecutive_limit_ups=max_consecutive_limit_ups,
        max_prior_consecutive_limit_ups=max_prior_consecutive_limit_ups,
        commission_rate=commission_rate,
        commission_discount=commission_discount,
        normal_sell_tax_rate=normal_sell_tax_rate,
        day_trade_sell_tax_rate=day_trade_sell_tax_rate,
        slippage_bps_per_side=slippage_bps_per_side,
        minimum_commission_twd=minimum_commission_twd,
        cost_shares=cost_shares,
    )
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
        events = load_closed_limit_up_events(
            conn,
            start=start,
            end=end,
            markets=market_values,
        )
        daily_prices = load_daily_prices_for_gap_capture(conn, markets=market_values)

    study = build_limit_up_gap_frame(
        events,
        daily_prices,
        commission_rate=commission_rate,
        commission_discount=commission_discount,
        normal_sell_tax_rate=normal_sell_tax_rate,
        day_trade_sell_tax_rate=day_trade_sell_tax_rate,
        slippage_bps_per_side=slippage_bps_per_side,
        minimum_commission_twd=minimum_commission_twd,
        cost_shares=cost_shares,
    )
    study = apply_limit_up_gap_filters(
        study,
        min_turnover_twd=min_turnover_twd,
        min_volume_ratio_20d=min_volume_ratio_20d,
        min_close_location=min_close_location,
        min_price=min_price,
        max_price=max_price,
        max_consecutive_limit_ups=max_consecutive_limit_ups,
        max_prior_consecutive_limit_ups=max_prior_consecutive_limit_ups,
        first_limit_up_only=first_limit_up_only,
        require_day_minus1_positive=require_day_minus1_positive,
    )
    summary = summarize_limit_up_gap_study(study)

    report_dir = Path(output_dir) if output_dir is not None else default_report_dir()
    report_dir.mkdir(parents=True, exist_ok=True)
    study.to_csv(report_dir / "limit_up_gap_capture_event_study.csv", index=False)
    summary.to_csv(report_dir / "limit_up_gap_capture_summary.csv", index=False)
    return study, summary


def load_closed_limit_up_events(
    conn,
    *,
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
    markets: Sequence[str] | None = None,
) -> pd.DataFrame:
    """Load stored closed-limit-up events from event_candidates."""

    market_values = normalize_markets(markets)
    placeholders = ", ".join(["?"] * len(market_values))
    return conn.execute(
        f"""
        SELECT {", ".join(EVENT_CANDIDATE_COLUMNS)}
        FROM event_candidates
        WHERE trade_date >= ?
          AND trade_date <= ?
          AND market IN ({placeholders})
          AND event_type = ?
        ORDER BY market, symbol, trade_date
        """,
        [pd.Timestamp(start).date(), pd.Timestamp(end).date(), *market_values, EVENT_TYPE],
    ).fetch_df()


def load_daily_prices_for_gap_capture(
    conn,
    *,
    markets: Sequence[str] | None = None,
) -> pd.DataFrame:
    """Load daily prices needed for limit-up sequence and Day 1 gap metrics."""

    market_values = normalize_markets(markets)
    placeholders = ", ".join(["?"] * len(market_values))
    return conn.execute(
        f"""
        SELECT
            symbol,
            trade_date,
            market,
            open,
            high,
            low,
            close,
            volume_shares,
            turnover_twd,
            daily_return,
            closed_limit_up
        FROM daily_prices
        WHERE market IN ({placeholders})
        ORDER BY market, symbol, trade_date
        """,
        market_values,
    ).fetch_df()


def build_limit_up_gap_frame(
    events: pd.DataFrame,
    daily_prices: pd.DataFrame,
    *,
    commission_rate: float = 0.001425,
    commission_discount: float = 0.28,
    normal_sell_tax_rate: float = 0.003,
    day_trade_sell_tax_rate: float = 0.0015,
    slippage_bps_per_side: float = 5.0,
    minimum_commission_twd: float = 20.0,
    cost_shares: int = DEFAULT_COST_SHARES,
) -> pd.DataFrame:
    """Join closed-limit-up events to Day 1 bars and compute gap metrics."""

    if events.empty:
        return empty_gap_study_frame()

    missing_events = sorted(set(EVENT_CANDIDATE_COLUMNS) - set(events.columns))
    if missing_events:
        raise ValueError(f"events is missing required columns: {missing_events}")

    required_daily = {
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
        "closed_limit_up",
    }
    missing_daily = sorted(required_daily - set(daily_prices.columns))
    if missing_daily:
        raise ValueError(f"daily_prices is missing required columns: {missing_daily}")

    frame = events[events["event_type"].eq(EVENT_TYPE)].copy()
    if frame.empty:
        return empty_gap_study_frame()

    frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.normalize()
    frame["next_trade_date"] = pd.to_datetime(frame["next_trade_date"]).dt.normalize()
    frame["market"] = frame["market"].astype("string").str.upper().str.strip()
    frame["symbol"] = frame["symbol"].astype("string").str.strip()

    daily = add_limit_up_context_features(daily_prices)
    day0_context_columns = [
        "symbol",
        "market",
        "trade_date",
        "consecutive_limit_up_count",
        "prior_consecutive_limit_up_count",
        "first_limit_up_in_sequence",
        "day_minus1_return",
        "day_minus1_return_positive",
        "prior_5d_return",
        "prior_20d_return",
    ]
    frame = frame.merge(
        daily[day0_context_columns],
        on=["market", "symbol", "trade_date"],
        how="left",
    )

    day1 = daily[
        [
            "symbol",
            "market",
            "trade_date",
            "open",
            "high",
            "low",
            "close",
            "volume_shares",
            "turnover_twd",
        ]
    ].rename(
        columns={
            "trade_date": "day1_trade_date",
            "open": "day1_open",
            "high": "day1_high",
            "low": "day1_low",
            "close": "day1_close",
            "volume_shares": "day1_volume_shares",
            "turnover_twd": "day1_turnover_twd",
        }
    )
    frame = frame.merge(
        day1,
        left_on=["market", "symbol", "next_trade_date"],
        right_on=["market", "symbol", "day1_trade_date"],
        how="left",
    )

    numeric_columns = [
        "day0_open",
        "day0_high",
        "day0_low",
        "day0_close",
        "day0_return",
        "day0_volume_shares",
        "day0_turnover_twd",
        "close_location",
        "volume_ratio_20d",
        "day_minus1_return",
        "prior_5d_return",
        "prior_20d_return",
        "day1_open",
        "day1_high",
        "day1_low",
        "day1_close",
        "day1_volume_shares",
        "day1_turnover_twd",
    ]
    for column in numeric_columns:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    for column in ["consecutive_limit_up_count", "prior_consecutive_limit_up_count"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0).astype("int64")
    frame["first_limit_up_in_sequence"] = frame["first_limit_up_in_sequence"].fillna(False).astype(bool)
    frame["day_minus1_return_positive"] = frame["day_minus1_return_positive"].fillna(False).astype(bool)

    frame["day1_open_gap"] = safe_return(frame["day1_open"], frame["day0_close"])
    frame["day1_high_gap"] = safe_return(frame["day1_high"], frame["day0_close"])
    frame["day1_low_gap"] = safe_return(frame["day1_low"], frame["day0_close"])
    frame["day1_close_gap"] = safe_return(frame["day1_close"], frame["day0_close"])
    frame["day1_open_to_close"] = safe_return(frame["day1_close"], frame["day1_open"])
    frame["day1_open_to_low"] = safe_return(frame["day1_low"], frame["day1_open"])
    frame["day1_open_to_high"] = safe_return(frame["day1_high"], frame["day1_open"])

    add_vectorized_costs(
        frame,
        entry_col="day0_close",
        exit_col="day1_open",
        prefix="overnight",
        is_day_trade=False,
        commission_rate=commission_rate,
        commission_discount=commission_discount,
        sell_tax_rate=normal_sell_tax_rate,
        slippage_bps_per_side=slippage_bps_per_side,
        minimum_commission_twd=minimum_commission_twd,
        cost_shares=cost_shares,
    )
    add_vectorized_costs(
        frame,
        entry_col="day1_open",
        exit_col="day1_close",
        prefix="daytrade_open_to_close",
        is_day_trade=True,
        commission_rate=commission_rate,
        commission_discount=commission_discount,
        sell_tax_rate=day_trade_sell_tax_rate,
        slippage_bps_per_side=slippage_bps_per_side,
        minimum_commission_twd=minimum_commission_twd,
        cost_shares=cost_shares,
    )
    frame["daytrade_net_open_to_close_return"] = frame["daytrade_open_to_close_net_return"]

    frame["win_day1_open_gap"] = frame["day1_open_gap"] > 0
    frame["win_overnight_net"] = frame["overnight_net_return"] > 0
    frame["win_day1_open_to_close"] = frame["day1_open_to_close"] > 0
    frame["win_daytrade_net_open_to_close"] = frame["daytrade_net_open_to_close_return"] > 0

    frame["year"] = frame["trade_date"].dt.year
    frame["month"] = frame["trade_date"].dt.strftime("%Y-%m")
    frame["turnover_bucket"] = bucket_turnover(frame["day0_turnover_twd"])
    frame["day0_turnover_bucket"] = frame["turnover_bucket"]
    frame["volume_ratio_20d_bucket"] = bucket_volume_ratio(frame["volume_ratio_20d"])
    frame["day0_volume_shock_bucket"] = bucket_volume_shock(frame["volume_ratio_20d"])
    frame["day0_price_bucket"] = bucket_price(frame["day0_close"])
    frame["prior_5d_return_bucket"] = bucket_prior_return(frame["prior_5d_return"])
    frame["prior_20d_return_bucket"] = bucket_prior_return(frame["prior_20d_return"])

    return frame[gap_study_columns()].reset_index(drop=True)


def add_limit_up_context_features(daily_prices: pd.DataFrame) -> pd.DataFrame:
    """Add prior-return and consecutive closed-limit-up features."""

    frame = daily_prices.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.normalize()
    frame["market"] = frame["market"].astype("string").str.upper().str.strip()
    frame["symbol"] = frame["symbol"].astype("string").str.strip()
    numeric_columns = ["open", "high", "low", "close", "volume_shares", "turnover_twd", "daily_return"]
    for column in numeric_columns:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["closed_limit_up"] = frame["closed_limit_up"].fillna(False).astype(bool)
    frame = frame.sort_values(["market", "symbol", "trade_date"]).reset_index(drop=True)
    group_keys = ["market", "symbol"]

    closed_int = frame["closed_limit_up"].astype("int64")
    streak_group = (~frame["closed_limit_up"]).groupby([frame["market"], frame["symbol"]]).cumsum()
    frame["consecutive_limit_up_count"] = closed_int.groupby(
        [frame["market"], frame["symbol"], streak_group],
        sort=False,
    ).cumsum()
    frame["prior_consecutive_limit_up_count"] = (
        frame.groupby(group_keys)["consecutive_limit_up_count"].shift(1).fillna(0).astype("int64")
    )
    frame["first_limit_up_in_sequence"] = frame["closed_limit_up"] & (
        frame["prior_consecutive_limit_up_count"] == 0
    )
    frame["day_minus1_return"] = frame.groupby(group_keys)["daily_return"].shift(1)
    frame["day_minus1_return_positive"] = frame["day_minus1_return"] > 0
    prior_close = frame.groupby(group_keys)["close"].shift(1)
    close_5_days_before = frame.groupby(group_keys)["close"].shift(6)
    close_20_days_before = frame.groupby(group_keys)["close"].shift(21)
    frame["prior_5d_return"] = safe_return(prior_close, close_5_days_before)
    frame["prior_20d_return"] = safe_return(prior_close, close_20_days_before)
    return frame


def apply_limit_up_gap_filters(
    study: pd.DataFrame,
    *,
    min_turnover_twd: float | None = None,
    min_volume_ratio_20d: float | None = None,
    min_close_location: float | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    max_consecutive_limit_ups: int | None = None,
    max_prior_consecutive_limit_ups: int | None = None,
    first_limit_up_only: bool = False,
    require_day_minus1_positive: bool = False,
) -> pd.DataFrame:
    """Apply research filters to the limit-up gap study frame."""

    if study.empty:
        return study.copy()

    mask = pd.Series(True, index=study.index)
    if min_turnover_twd is not None:
        mask &= study["day0_turnover_twd"] >= min_turnover_twd
    if min_volume_ratio_20d is not None:
        mask &= study["volume_ratio_20d"] >= min_volume_ratio_20d
    if min_close_location is not None:
        mask &= study["close_location"] >= min_close_location
    if min_price is not None:
        mask &= study["day0_close"] >= min_price
    if max_price is not None:
        mask &= study["day0_close"] <= max_price
    if max_consecutive_limit_ups is not None:
        mask &= study["consecutive_limit_up_count"] <= max_consecutive_limit_ups
    if max_prior_consecutive_limit_ups is not None:
        mask &= study["prior_consecutive_limit_up_count"] <= max_prior_consecutive_limit_ups
    if first_limit_up_only:
        mask &= study["first_limit_up_in_sequence"]
    if require_day_minus1_positive:
        mask &= study["day_minus1_return_positive"]
    return study[mask.fillna(False)].reset_index(drop=True)


def summarize_limit_up_gap_study(study: pd.DataFrame) -> pd.DataFrame:
    """Create grouped summaries for the limit-up gap event study."""

    if study.empty:
        return empty_gap_summary_frame()

    group_specs = [
        ("overall", []),
        ("by_market", ["market"]),
        ("by_year", ["year"]),
        ("by_month", ["month"]),
        ("by_turnover_bucket", ["turnover_bucket"]),
        ("by_volume_ratio_20d_bucket", ["volume_ratio_20d_bucket"]),
        ("by_consecutive_limit_up_count", ["consecutive_limit_up_count"]),
        ("by_day0_turnover_bucket", ["day0_turnover_bucket"]),
        ("by_day0_price_bucket", ["day0_price_bucket"]),
        ("by_day0_volume_shock_bucket", ["day0_volume_shock_bucket"]),
        ("by_prior_5d_return_bucket", ["prior_5d_return_bucket"]),
        ("by_prior_20d_return_bucket", ["prior_20d_return_bucket"]),
        (
            "full_cross_section",
            [
                "market",
                "year",
                "month",
                "turnover_bucket",
                "volume_ratio_20d_bucket",
                "consecutive_limit_up_count",
                "day0_price_bucket",
                "day0_volume_shock_bucket",
            ],
        ),
    ]
    frames = [
        summarize_limit_up_gap_group(study, summary_level=level, group_columns=columns)
        for level, columns in group_specs
    ]
    return pd.concat(frames, ignore_index=True)


def summarize_limit_up_gap_group(
    study: pd.DataFrame,
    *,
    summary_level: str,
    group_columns: list[str],
) -> pd.DataFrame:
    """Summarize one group level."""

    grouped = study.groupby(group_columns, dropna=False, observed=False) if group_columns else [((), study)]
    rows: list[dict[str, Any]] = []
    for group_key, group in grouped:
        key_values = group_key if isinstance(group_key, tuple) else (group_key,)
        row: dict[str, Any] = {
            "summary_level": summary_level,
            **{column: None for column in GROUP_VALUE_COLUMNS},
            "event_count": int(len(group)),
            "day1_observation_count": int(group["day1_open"].notna().sum()),
            "avg_day0_return": series_mean(group["day0_return"]),
            "median_day0_turnover_twd": series_median(group["day0_turnover_twd"]),
            "median_day0_close": series_median(group["day0_close"]),
            "avg_consecutive_limit_up_count": series_mean(group["consecutive_limit_up_count"]),
            "avg_prior_consecutive_limit_up_count": series_mean(group["prior_consecutive_limit_up_count"]),
            "first_limit_up_rate": series_mean(group["first_limit_up_in_sequence"]),
            "day_minus1_positive_rate": series_mean(group["day_minus1_return_positive"]),
            "avg_overnight_cost_bps": series_mean(group["overnight_cost_bps"]),
            "avg_daytrade_open_to_close_cost_bps": series_mean(group["daytrade_open_to_close_cost_bps"]),
            "total_overnight_net_pnl": float(pd.to_numeric(group["overnight_net_pnl"], errors="coerce").fillna(0).sum()),
            "overnight_profit_factor": profit_factor(group["overnight_net_pnl"]),
            "overnight_max_drawdown": max_drawdown_from_pnl(group.sort_values("trade_date")["overnight_net_pnl"]),
            "total_daytrade_open_to_close_net_pnl": float(
                pd.to_numeric(group["daytrade_open_to_close_net_pnl"], errors="coerce").fillna(0).sum()
            ),
            "daytrade_open_to_close_profit_factor": profit_factor(group["daytrade_open_to_close_net_pnl"]),
            "daytrade_open_to_close_max_drawdown": max_drawdown_from_pnl(
                group.sort_values("trade_date")["daytrade_open_to_close_net_pnl"]
            ),
        }
        for column, value in zip(group_columns, key_values):
            row[column] = value

        for column in GAP_RETURN_COLUMNS:
            values = pd.to_numeric(group[column], errors="coerce").dropna()
            row[f"mean_{column}"] = series_mean(values)
            row[f"median_{column}"] = series_median(values)
            row[f"t_stat_{column}"] = one_sample_t_stat(values)
            row[f"p_value_{column}_gt_0"] = one_sided_positive_p_value(values)

        for column in RATE_COLUMNS:
            row[f"{column}_rate"] = series_mean(group[column])
        rows.append(row)

    return pd.DataFrame(rows, columns=gap_summary_columns())


def add_vectorized_costs(
    frame: pd.DataFrame,
    *,
    entry_col: str,
    exit_col: str,
    prefix: str,
    is_day_trade: bool,
    commission_rate: float,
    commission_discount: float,
    sell_tax_rate: float,
    slippage_bps_per_side: float,
    minimum_commission_twd: float,
    cost_shares: int,
) -> None:
    """Add vectorized long round-trip cost and PnL columns in-place."""

    entry = pd.to_numeric(frame[entry_col], errors="coerce")
    exit_ = pd.to_numeric(frame[exit_col], errors="coerce")
    shares = float(cost_shares)
    buy_notional = entry * shares
    sell_notional = exit_ * shares
    effective_commission_rate = commission_rate * commission_discount

    buy_commission = np.maximum(buy_notional * effective_commission_rate, minimum_commission_twd)
    sell_commission = np.maximum(sell_notional * effective_commission_rate, minimum_commission_twd)
    sell_tax = sell_notional * sell_tax_rate
    slippage_cost = (buy_notional + sell_notional) * slippage_bps_per_side / 10_000.0
    gross_pnl = sell_notional - buy_notional
    total_cost = buy_commission + sell_commission + sell_tax + slippage_cost
    net_pnl = gross_pnl - total_cost

    frame[f"{prefix}_buy_notional"] = buy_notional
    frame[f"{prefix}_sell_notional"] = sell_notional
    frame[f"{prefix}_buy_commission"] = buy_commission
    frame[f"{prefix}_sell_commission"] = sell_commission
    frame[f"{prefix}_sell_tax"] = sell_tax
    frame[f"{prefix}_slippage_cost"] = slippage_cost
    frame[f"{prefix}_total_cost"] = total_cost
    frame[f"{prefix}_gross_pnl"] = gross_pnl
    frame[f"{prefix}_net_pnl"] = net_pnl
    frame[f"{prefix}_gross_return"] = np.where(buy_notional > 0, gross_pnl / buy_notional, np.nan)
    frame[f"{prefix}_net_return"] = np.where(buy_notional > 0, net_pnl / buy_notional, np.nan)
    frame[f"{prefix}_cost_bps"] = (frame[f"{prefix}_gross_return"] - frame[f"{prefix}_net_return"]) * 10_000.0
    frame[f"{prefix}_is_day_trade"] = bool(is_day_trade)


def safe_return(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    numerator_values = pd.to_numeric(numerator, errors="coerce")
    denominator_values = pd.to_numeric(denominator, errors="coerce")
    return pd.Series(
        np.where(denominator_values > 0, numerator_values / denominator_values - 1.0, np.nan),
        index=numerator_values.index,
    )


def bucket_turnover(values: pd.Series) -> pd.Series:
    buckets = pd.cut(
        pd.to_numeric(values, errors="coerce"),
        bins=[-np.inf, 50_000_000, 100_000_000, 200_000_000, 500_000_000, 1_000_000_000, np.inf],
        labels=["<50m", "50m_100m", "100m_200m", "200m_500m", "500m_1b", ">=1b"],
    )
    return buckets.astype("string").fillna("unknown")


def bucket_volume_ratio(values: pd.Series) -> pd.Series:
    buckets = pd.cut(
        pd.to_numeric(values, errors="coerce"),
        bins=[-np.inf, 1.0, 2.0, 3.0, 5.0, 10.0, np.inf],
        labels=["<1", "1_2", "2_3", "3_5", "5_10", ">=10"],
    )
    return buckets.astype("string").fillna("unknown")


def bucket_volume_shock(values: pd.Series) -> pd.Series:
    buckets = pd.cut(
        pd.to_numeric(values, errors="coerce"),
        bins=[-np.inf, 1.0, 2.0, 5.0, 10.0, np.inf],
        labels=["<1x", "1_2x", "2_5x", "5_10x", ">=10x"],
    )
    return buckets.astype("string").fillna("unknown")


def bucket_price(values: pd.Series) -> pd.Series:
    buckets = pd.cut(
        pd.to_numeric(values, errors="coerce"),
        bins=[-np.inf, 10.0, 20.0, 50.0, 100.0, 200.0, 500.0, np.inf],
        labels=["<10", "10_20", "20_50", "50_100", "100_200", "200_500", ">=500"],
    )
    return buckets.astype("string").fillna("unknown")


def bucket_prior_return(values: pd.Series) -> pd.Series:
    buckets = pd.cut(
        pd.to_numeric(values, errors="coerce"),
        bins=[-np.inf, -0.10, -0.05, 0.0, 0.05, 0.10, 0.20, np.inf],
        labels=["<=-10%", "-10%_-5%", "-5%_0", "0_5%", "5_10%", "10_20%", ">20%"],
    )
    return buckets.astype("string").fillna("unknown")


def profit_factor(pnl: pd.Series) -> float:
    values = pd.to_numeric(pnl, errors="coerce").fillna(0.0)
    losses = abs(values[values < 0].sum())
    gains = values[values > 0].sum()
    if losses == 0 and gains > 0:
        return float("inf")
    if losses == 0:
        return 0.0
    return float(gains / losses)


def max_drawdown_from_pnl(pnl: pd.Series) -> float:
    values = pd.to_numeric(pnl, errors="coerce").fillna(0.0)
    if values.empty:
        return 0.0
    equity = pd.concat([pd.Series([0.0]), values.reset_index(drop=True)], ignore_index=True).cumsum()
    drawdown = equity - equity.cummax()
    return float(drawdown.min())


def series_mean(values: pd.Series) -> float:
    clean = pd.to_numeric(values, errors="coerce").dropna()
    if clean.empty:
        return np.nan
    return float(clean.mean())


def series_median(values: pd.Series) -> float:
    clean = pd.to_numeric(values, errors="coerce").dropna()
    if clean.empty:
        return np.nan
    return float(clean.median())


def one_sample_t_stat(values: pd.Series) -> float:
    clean = pd.to_numeric(values, errors="coerce").dropna()
    if len(clean) < 2:
        return np.nan
    std = clean.std(ddof=1)
    if std == 0 or pd.isna(std):
        return np.nan
    return float(clean.mean() / (std / math.sqrt(len(clean))))


def one_sided_positive_p_value(values: pd.Series) -> float:
    t_stat = one_sample_t_stat(values)
    if pd.isna(t_stat):
        return np.nan
    return float(0.5 * math.erfc(t_stat / math.sqrt(2.0)))


def validate_inputs(
    *,
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
    min_turnover_twd: float | None,
    min_volume_ratio_20d: float | None,
    min_close_location: float | None,
    min_price: float | None,
    max_price: float | None,
    max_consecutive_limit_ups: int | None,
    max_prior_consecutive_limit_ups: int | None,
    commission_rate: float,
    commission_discount: float,
    normal_sell_tax_rate: float,
    day_trade_sell_tax_rate: float,
    slippage_bps_per_side: float,
    minimum_commission_twd: float,
    cost_shares: int,
) -> None:
    if pd.Timestamp(end) < pd.Timestamp(start):
        raise ValueError("end must be on or after start")
    for name, value in {
        "min_turnover_twd": min_turnover_twd,
        "min_volume_ratio_20d": min_volume_ratio_20d,
        "min_close_location": min_close_location,
        "min_price": min_price,
        "max_price": max_price,
    }.items():
        if value is not None and value < 0:
            raise ValueError(f"{name} must be non-negative")
    if min_price is not None and max_price is not None and max_price < min_price:
        raise ValueError("max_price must be greater than or equal to min_price")
    for name, value in {
        "max_consecutive_limit_ups": max_consecutive_limit_ups,
        "max_prior_consecutive_limit_ups": max_prior_consecutive_limit_ups,
    }.items():
        if value is not None and value < 0:
            raise ValueError(f"{name} must be non-negative")
    for name, value in {
        "commission_rate": commission_rate,
        "commission_discount": commission_discount,
        "normal_sell_tax_rate": normal_sell_tax_rate,
        "day_trade_sell_tax_rate": day_trade_sell_tax_rate,
        "slippage_bps_per_side": slippage_bps_per_side,
        "minimum_commission_twd": minimum_commission_twd,
    }.items():
        if value < 0:
            raise ValueError(f"{name} must be non-negative")
    if cost_shares <= 0:
        raise ValueError("cost_shares must be positive")


def gap_study_columns() -> list[str]:
    return EVENT_CANDIDATE_COLUMNS + [
        "consecutive_limit_up_count",
        "prior_consecutive_limit_up_count",
        "first_limit_up_in_sequence",
        "day_minus1_return",
        "day_minus1_return_positive",
        "prior_5d_return",
        "prior_20d_return",
        "day1_trade_date",
        "day1_open",
        "day1_high",
        "day1_low",
        "day1_close",
        "day1_volume_shares",
        "day1_turnover_twd",
        "day1_open_gap",
        "day1_high_gap",
        "day1_low_gap",
        "day1_close_gap",
        "day1_open_to_close",
        "day1_open_to_low",
        "day1_open_to_high",
        "overnight_buy_notional",
        "overnight_sell_notional",
        "overnight_buy_commission",
        "overnight_sell_commission",
        "overnight_sell_tax",
        "overnight_slippage_cost",
        "overnight_total_cost",
        "overnight_gross_pnl",
        "overnight_net_pnl",
        "overnight_gross_return",
        "overnight_net_return",
        "overnight_cost_bps",
        "overnight_is_day_trade",
        "daytrade_open_to_close_buy_notional",
        "daytrade_open_to_close_sell_notional",
        "daytrade_open_to_close_buy_commission",
        "daytrade_open_to_close_sell_commission",
        "daytrade_open_to_close_sell_tax",
        "daytrade_open_to_close_slippage_cost",
        "daytrade_open_to_close_total_cost",
        "daytrade_open_to_close_gross_pnl",
        "daytrade_open_to_close_net_pnl",
        "daytrade_open_to_close_gross_return",
        "daytrade_net_open_to_close_return",
        "daytrade_open_to_close_cost_bps",
        "daytrade_open_to_close_is_day_trade",
        "win_day1_open_gap",
        "win_overnight_net",
        "win_day1_open_to_close",
        "win_daytrade_net_open_to_close",
        "year",
        "month",
        "turnover_bucket",
        "volume_ratio_20d_bucket",
        "day0_turnover_bucket",
        "day0_price_bucket",
        "day0_volume_shock_bucket",
        "prior_5d_return_bucket",
        "prior_20d_return_bucket",
    ]


def gap_summary_columns() -> list[str]:
    base_columns = [
        "summary_level",
        *GROUP_VALUE_COLUMNS,
        "event_count",
        "day1_observation_count",
        "avg_day0_return",
        "median_day0_turnover_twd",
        "median_day0_close",
        "avg_consecutive_limit_up_count",
        "avg_prior_consecutive_limit_up_count",
        "first_limit_up_rate",
        "day_minus1_positive_rate",
        "avg_overnight_cost_bps",
        "avg_daytrade_open_to_close_cost_bps",
        "total_overnight_net_pnl",
        "overnight_profit_factor",
        "overnight_max_drawdown",
        "total_daytrade_open_to_close_net_pnl",
        "daytrade_open_to_close_profit_factor",
        "daytrade_open_to_close_max_drawdown",
    ]
    return_columns: list[str] = []
    for column in GAP_RETURN_COLUMNS:
        return_columns.extend(
            [
                f"mean_{column}",
                f"median_{column}",
                f"t_stat_{column}",
                f"p_value_{column}_gt_0",
            ]
        )
    rate_columns = [f"{column}_rate" for column in RATE_COLUMNS]
    return base_columns + return_columns + rate_columns


def empty_gap_study_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=gap_study_columns())


def empty_gap_summary_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=gap_summary_columns())


def default_report_dir() -> Path:
    return get_settings().project_root / "reports"


def print_limit_up_gap_answer(study: pd.DataFrame, summary: pd.DataFrame) -> None:
    """Print a concise CLI answer to the core gap-capture question."""

    if study.empty or summary.empty:
        print("\nclosed_limit_up overnight gap question: no rows after filters")
        return

    overall = summary[summary["summary_level"].eq("overall")].iloc[0]
    print("\nclosed_limit_up overnight gap question")
    print(f"events: {int(overall['event_count'])}")
    print(
        "gross Day0 close -> Day1 open: "
        f"mean {overall['mean_day1_open_gap']:.6f}, "
        f"median {overall['median_day1_open_gap']:.6f}, "
        f"win rate {overall['win_day1_open_gap_rate']:.4f}"
    )
    print(
        "net overnight after normal-tax costs: "
        f"mean {overall['mean_overnight_net_return']:.6f}, "
        f"median {overall['median_overnight_net_return']:.6f}, "
        f"win rate {overall['win_overnight_net_rate']:.4f}, "
        f"max drawdown {overall['overnight_max_drawdown']:.2f}"
    )
    print(
        "net Day1 open -> close after day-trade costs: "
        f"mean {overall['mean_daytrade_net_open_to_close_return']:.6f}, "
        f"median {overall['median_daytrade_net_open_to_close_return']:.6f}, "
        f"win rate {overall['win_daytrade_net_open_to_close_rate']:.4f}"
    )
    verdict = "YES" if overall["mean_overnight_net_return"] > 0 and overall["win_overnight_net_rate"] > 0.5 else "NO"
    print(f"edge survives modeled overnight costs: {verdict}")

    print("\nfilter groups with strongest net overnight gap (min 30 events)")
    print(format_group_preview(summary, ascending=False))
    print("\nfilter groups with weakest net overnight gap (min 30 events)")
    print(format_group_preview(summary, ascending=True))


def format_group_preview(summary: pd.DataFrame, *, ascending: bool) -> str:
    group_rows = summary[
        summary["summary_level"].isin(
            [
                "by_market",
                "by_year",
                "by_turnover_bucket",
                "by_volume_ratio_20d_bucket",
                "by_consecutive_limit_up_count",
                "by_day0_price_bucket",
                "by_day0_volume_shock_bucket",
                "by_prior_5d_return_bucket",
                "by_prior_20d_return_bucket",
            ]
        )
        & (summary["event_count"] >= 30)
    ].copy()
    if group_rows.empty:
        return "(no groups with at least 30 events)"
    group_rows["group"] = group_rows.apply(group_label, axis=1)
    columns = [
        "summary_level",
        "group",
        "event_count",
        "mean_overnight_net_return",
        "median_overnight_net_return",
        "win_overnight_net_rate",
        "overnight_max_drawdown",
    ]
    return group_rows.sort_values("mean_overnight_net_return", ascending=ascending)[columns].head(10).to_string(
        index=False
    )


def group_label(row: pd.Series) -> str:
    for column in GROUP_VALUE_COLUMNS:
        value = row.get(column)
        if pd.notna(value):
            return f"{column}={value}"
    return "overall"


def main() -> None:
    parser = argparse.ArgumentParser(description="Closed-limit-up overnight gap-capture event study")
    parser.add_argument("--db", type=Path, default=get_settings().db_path, help="DuckDB path")
    parser.add_argument("--start", required=True, help="Start date, YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="End date, YYYY-MM-DD")
    parser.add_argument("--market", choices=["TWSE", "TPEX", "BOTH"], default="BOTH")
    parser.add_argument("--min-turnover-twd", type=float)
    parser.add_argument("--min-volume-ratio-20d", type=float)
    parser.add_argument("--min-close-location", type=float)
    parser.add_argument("--min-price", type=float)
    parser.add_argument("--max-price", type=float)
    parser.add_argument("--max-consecutive-limit-ups", type=int)
    parser.add_argument("--max-prior-consecutive-limit-ups", type=int)
    parser.add_argument("--first-limit-up-only", action="store_true")
    parser.add_argument("--require-day-minus1-positive", action="store_true")
    parser.add_argument("--commission-rate", type=float, default=0.001425)
    parser.add_argument("--commission-discount", type=float, default=0.28)
    parser.add_argument("--normal-sell-tax-rate", type=float, default=0.003)
    parser.add_argument("--day-trade-sell-tax-rate", type=float, default=0.0015)
    parser.add_argument("--slippage-bps-per-side", type=float, default=5.0)
    parser.add_argument("--minimum-commission-twd", type=float, default=20.0)
    parser.add_argument("--cost-shares", type=int, default=DEFAULT_COST_SHARES)
    parser.add_argument("--output-dir", type=Path, default=default_report_dir())
    parser.add_argument(
        "--skip-build-events",
        action="store_true",
        help="Use existing event_candidates instead of rebuilding for the window",
    )
    args = parser.parse_args()

    markets = normalize_markets([args.market])
    study, summary = run_limit_up_gap_capture(
        db_path=args.db,
        start=args.start,
        end=args.end,
        markets=markets,
        min_turnover_twd=args.min_turnover_twd,
        min_volume_ratio_20d=args.min_volume_ratio_20d,
        min_close_location=args.min_close_location,
        min_price=args.min_price,
        max_price=args.max_price,
        max_consecutive_limit_ups=args.max_consecutive_limit_ups,
        max_prior_consecutive_limit_ups=args.max_prior_consecutive_limit_ups,
        first_limit_up_only=args.first_limit_up_only,
        require_day_minus1_positive=args.require_day_minus1_positive,
        commission_rate=args.commission_rate,
        commission_discount=args.commission_discount,
        normal_sell_tax_rate=args.normal_sell_tax_rate,
        day_trade_sell_tax_rate=args.day_trade_sell_tax_rate,
        slippage_bps_per_side=args.slippage_bps_per_side,
        minimum_commission_twd=args.minimum_commission_twd,
        cost_shares=args.cost_shares,
        output_dir=args.output_dir,
        rebuild_events=not args.skip_build_events,
    )
    event_path = args.output_dir / "limit_up_gap_capture_event_study.csv"
    summary_path = args.output_dir / "limit_up_gap_capture_summary.csv"
    print(f"Wrote {len(study)} event rows to {event_path}")
    print(f"Wrote {len(summary)} summary rows to {summary_path}")
    print_limit_up_gap_answer(study, summary)


if __name__ == "__main__":
    main()
