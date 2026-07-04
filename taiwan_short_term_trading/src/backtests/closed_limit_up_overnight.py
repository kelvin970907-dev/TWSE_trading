"""Closed-limit-up overnight gap-capture strategy backtest."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from config.settings import get_settings
from src.backtests.costs import calculate_trade_costs
from src.backtests.event_study import build_event_candidates, normalize_markets
from src.backtests.limit_up_gap_capture import (
    EVENT_TYPE,
    apply_limit_up_gap_filters,
    bucket_turnover,
    build_limit_up_gap_frame,
    load_closed_limit_up_events,
    load_daily_prices_for_gap_capture,
)
from src.backtests.strategy_limit_momentum import BOARD_LOT_SIZE, calculate_board_lot_shares
from src.db import get_connection, init_db, upsert_dataframe


STRATEGY_NAME = "closed_limit_up_overnight"

BACKTEST_TRADE_COLUMNS = [
    "trade_id",
    "strategy_name",
    "symbol",
    "market",
    "signal_date",
    "entry_date",
    "entry_time",
    "exit_date",
    "exit_time",
    "side",
    "entry_price",
    "exit_price",
    "shares",
    "gross_pnl",
    "fees",
    "tax",
    "slippage",
    "net_pnl",
    "gross_return",
    "net_return",
    "holding_minutes",
    "exit_reason",
    "metadata_json",
]

TRADE_REPORT_COLUMNS = BACKTEST_TRADE_COLUMNS + [
    "event_id",
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
    "day1_open_gap",
    "day1_open_to_close",
    "fixed_notional_twd",
    "buy_notional",
    "sell_notional",
    "buy_commission",
    "sell_commission",
    "sell_tax",
    "slippage_cost",
    "total_cost",
    "turnover_bucket",
    "day0_price_bucket",
    "day0_volume_shock_bucket",
    "prior_5d_return_bucket",
    "prior_20d_return_bucket",
]

SUMMARY_COLUMNS = [
    "summary_level",
    "year",
    "market",
    "turnover_bucket",
    "consecutive_limit_up_count",
    "symbol",
    "trades",
    "win_rate",
    "average_gross_return",
    "median_gross_return",
    "average_net_return",
    "median_net_return",
    "total_net_pnl",
    "profit_factor",
    "max_drawdown",
    "worst_trade",
    "best_trade",
    "average_turnover_twd",
    "average_volume_ratio_20d",
    "average_entry_price",
    "average_shares",
    "total_buy_notional",
    "trade_share",
    "abs_net_pnl_share",
]


def run_closed_limit_up_overnight(
    *,
    db_path: Path | str,
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
    markets: Sequence[str] | None = None,
    fixed_notional_twd: float = 100_000.0,
    min_turnover_twd: float | None = None,
    min_volume_ratio_20d: float | None = None,
    min_close_location: float | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    max_consecutive_limit_ups: int | None = None,
    only_first_limit_up: bool = False,
    exclude_if_prior_5d_return_above: float | None = None,
    exclude_if_prior_20d_return_above: float | None = None,
    commission_rate: float = 0.001425,
    commission_discount: float = 0.28,
    sell_tax_rate: float = 0.003,
    slippage_bps_per_side: float = 5.0,
    minimum_commission_twd: float = 20.0,
    output_dir: Path | str | None = None,
    rebuild_events: bool = True,
    store_trades: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Run the Day0-close to Day1-open closed-limit-up strategy."""

    validate_strategy_inputs(
        start=start,
        end=end,
        fixed_notional_twd=fixed_notional_twd,
        min_turnover_twd=min_turnover_twd,
        min_volume_ratio_20d=min_volume_ratio_20d,
        min_close_location=min_close_location,
        min_price=min_price,
        max_price=max_price,
        max_consecutive_limit_ups=max_consecutive_limit_ups,
        exclude_if_prior_5d_return_above=exclude_if_prior_5d_return_above,
        exclude_if_prior_20d_return_above=exclude_if_prior_20d_return_above,
        commission_rate=commission_rate,
        commission_discount=commission_discount,
        sell_tax_rate=sell_tax_rate,
        slippage_bps_per_side=slippage_bps_per_side,
        minimum_commission_twd=minimum_commission_twd,
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
            normal_sell_tax_rate=sell_tax_rate,
            slippage_bps_per_side=slippage_bps_per_side,
            minimum_commission_twd=minimum_commission_twd,
        )
        filtered = apply_strategy_filters(
            study,
            min_turnover_twd=min_turnover_twd,
            min_volume_ratio_20d=min_volume_ratio_20d,
            min_close_location=min_close_location,
            min_price=min_price,
            max_price=max_price,
            max_consecutive_limit_ups=max_consecutive_limit_ups,
            only_first_limit_up=only_first_limit_up,
            exclude_if_prior_5d_return_above=exclude_if_prior_5d_return_above,
            exclude_if_prior_20d_return_above=exclude_if_prior_20d_return_above,
        )
        trades = build_overnight_trades(
            filtered,
            fixed_notional_twd=fixed_notional_twd,
            commission_rate=commission_rate,
            commission_discount=commission_discount,
            sell_tax_rate=sell_tax_rate,
            slippage_bps_per_side=slippage_bps_per_side,
            minimum_commission_twd=minimum_commission_twd,
            min_turnover_twd=min_turnover_twd,
            min_volume_ratio_20d=min_volume_ratio_20d,
            min_close_location=min_close_location,
            min_price=min_price,
            max_price=max_price,
            max_consecutive_limit_ups=max_consecutive_limit_ups,
            only_first_limit_up=only_first_limit_up,
            exclude_if_prior_5d_return_above=exclude_if_prior_5d_return_above,
            exclude_if_prior_20d_return_above=exclude_if_prior_20d_return_above,
        )
        if store_trades:
            delete_strategy_trades(
                conn,
                strategy_name=STRATEGY_NAME,
                start=start,
                end=end,
                markets=market_values,
            )
            if not trades.empty:
                upsert_dataframe(
                    conn,
                    "backtest_trades",
                    trades[BACKTEST_TRADE_COLUMNS],
                    ["trade_id"],
                )

    summary = summarize_overnight_trades(trades)
    report_dir = Path(output_dir) if output_dir is not None else default_report_dir()
    report_dir.mkdir(parents=True, exist_ok=True)
    trades.to_csv(report_dir / "closed_limit_up_overnight_trades.csv", index=False)
    summary.to_csv(report_dir / "closed_limit_up_overnight_summary.csv", index=False)
    return trades, summary


def apply_strategy_filters(
    study: pd.DataFrame,
    *,
    min_turnover_twd: float | None = None,
    min_volume_ratio_20d: float | None = None,
    min_close_location: float | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    max_consecutive_limit_ups: int | None = None,
    only_first_limit_up: bool = False,
    exclude_if_prior_5d_return_above: float | None = None,
    exclude_if_prior_20d_return_above: float | None = None,
) -> pd.DataFrame:
    """Apply closed-limit-up overnight strategy filters."""

    filtered = apply_limit_up_gap_filters(
        study,
        min_turnover_twd=min_turnover_twd,
        min_volume_ratio_20d=min_volume_ratio_20d,
        min_close_location=min_close_location,
        min_price=min_price,
        max_price=max_price,
        max_consecutive_limit_ups=max_consecutive_limit_ups,
        first_limit_up_only=only_first_limit_up,
    )
    if filtered.empty:
        return filtered

    mask = pd.Series(True, index=filtered.index)
    if exclude_if_prior_5d_return_above is not None:
        prior_5d = pd.to_numeric(filtered["prior_5d_return"], errors="coerce")
        mask &= prior_5d.isna() | (prior_5d <= exclude_if_prior_5d_return_above)
    if exclude_if_prior_20d_return_above is not None:
        prior_20d = pd.to_numeric(filtered["prior_20d_return"], errors="coerce")
        mask &= prior_20d.isna() | (prior_20d <= exclude_if_prior_20d_return_above)
    return filtered[mask.fillna(False)].reset_index(drop=True)


def build_overnight_trades(
    study: pd.DataFrame,
    *,
    fixed_notional_twd: float,
    commission_rate: float,
    commission_discount: float,
    sell_tax_rate: float,
    slippage_bps_per_side: float,
    minimum_commission_twd: float,
    min_turnover_twd: float | None = None,
    min_volume_ratio_20d: float | None = None,
    min_close_location: float | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    max_consecutive_limit_ups: int | None = None,
    only_first_limit_up: bool = False,
    exclude_if_prior_5d_return_above: float | None = None,
    exclude_if_prior_20d_return_above: float | None = None,
) -> pd.DataFrame:
    """Build report trade rows from filtered closed-limit-up events."""

    if study.empty:
        return empty_trade_report_frame()

    frame = study.copy()
    frame = frame.dropna(subset=["day0_close", "day1_open", "trade_date", "next_trade_date"]).copy()
    frame = frame[(frame["day0_close"] > 0) & (frame["day1_open"] > 0)].copy()
    if frame.empty:
        return empty_trade_report_frame()

    rows: list[dict[str, Any]] = []
    for row in frame.to_dict("records"):
        entry_price = float(row["day0_close"])
        exit_price = float(row["day1_open"])
        shares = calculate_board_lot_shares(
            fixed_notional_twd=fixed_notional_twd,
            entry_price=entry_price,
        )
        if shares < BOARD_LOT_SIZE:
            continue

        costs = calculate_trade_costs(
            side="long",
            entry_price=entry_price,
            exit_price=exit_price,
            shares=shares,
            commission_rate=commission_rate,
            commission_discount=commission_discount,
            sell_tax_rate=sell_tax_rate,
            slippage_bps_per_side=slippage_bps_per_side,
            minimum_commission_twd=minimum_commission_twd,
            is_day_trade=False,
            normal_sell_tax_rate=sell_tax_rate,
        )
        signal_date = pd.Timestamp(row["trade_date"]).normalize()
        exit_date = pd.Timestamp(row["next_trade_date"]).normalize()
        entry_time = signal_date + pd.Timedelta(hours=13, minutes=30)
        exit_time = exit_date + pd.Timedelta(hours=9)
        holding_minutes = (exit_time - entry_time).total_seconds() / 60.0
        trade_id = make_trade_id(
            event_id=str(row["event_id"]),
            fixed_notional_twd=fixed_notional_twd,
            shares=shares,
            entry_date=signal_date,
            exit_date=exit_date,
        )
        metadata = {
            "event_id": row["event_id"],
            "event_type": row["event_type"],
            "fixed_notional_twd": fixed_notional_twd,
            "board_lot_size": BOARD_LOT_SIZE,
            "commission_rate": commission_rate,
            "commission_discount": commission_discount,
            "sell_tax_rate": sell_tax_rate,
            "slippage_bps_per_side": slippage_bps_per_side,
            "minimum_commission_twd": minimum_commission_twd,
            "is_day_trade": False,
            "min_turnover_twd": min_turnover_twd,
            "min_volume_ratio_20d": min_volume_ratio_20d,
            "min_close_location": min_close_location,
            "min_price": min_price,
            "max_price": max_price,
            "max_consecutive_limit_ups": max_consecutive_limit_ups,
            "only_first_limit_up": only_first_limit_up,
            "exclude_if_prior_5d_return_above": exclude_if_prior_5d_return_above,
            "exclude_if_prior_20d_return_above": exclude_if_prior_20d_return_above,
        }
        rows.append(
            {
                "trade_id": trade_id,
                "strategy_name": STRATEGY_NAME,
                "symbol": row["symbol"],
                "market": row["market"],
                "signal_date": signal_date,
                "entry_date": signal_date,
                "entry_time": entry_time,
                "exit_date": exit_date,
                "exit_time": exit_time,
                "side": "long",
                "entry_price": entry_price,
                "exit_price": exit_price,
                "shares": shares,
                "gross_pnl": costs["gross_pnl"],
                "fees": costs["buy_commission"] + costs["sell_commission"],
                "tax": costs["sell_tax"],
                "slippage": costs["slippage_cost"],
                "net_pnl": costs["net_pnl"],
                "gross_return": costs["gross_return"],
                "net_return": costs["net_return"],
                "holding_minutes": holding_minutes,
                "exit_reason": "day1_open_exit",
                "metadata_json": json.dumps(metadata, ensure_ascii=False, default=json_default),
                "event_id": row["event_id"],
                "event_type": row["event_type"],
                "day0_return": row["day0_return"],
                "day0_open": row["day0_open"],
                "day0_high": row["day0_high"],
                "day0_low": row["day0_low"],
                "day0_close": row["day0_close"],
                "day0_volume_shares": row["day0_volume_shares"],
                "day0_turnover_twd": row["day0_turnover_twd"],
                "close_location": row["close_location"],
                "volume_ratio_20d": row["volume_ratio_20d"],
                "consecutive_limit_up_count": row["consecutive_limit_up_count"],
                "prior_consecutive_limit_up_count": row["prior_consecutive_limit_up_count"],
                "first_limit_up_in_sequence": row["first_limit_up_in_sequence"],
                "day_minus1_return": row["day_minus1_return"],
                "day_minus1_return_positive": row["day_minus1_return_positive"],
                "prior_5d_return": row["prior_5d_return"],
                "prior_20d_return": row["prior_20d_return"],
                "day1_trade_date": row["day1_trade_date"],
                "day1_open": row["day1_open"],
                "day1_high": row["day1_high"],
                "day1_low": row["day1_low"],
                "day1_close": row["day1_close"],
                "day1_open_gap": row["day1_open_gap"],
                "day1_open_to_close": row["day1_open_to_close"],
                "fixed_notional_twd": fixed_notional_twd,
                "buy_notional": costs["buy_notional"],
                "sell_notional": costs["sell_notional"],
                "buy_commission": costs["buy_commission"],
                "sell_commission": costs["sell_commission"],
                "sell_tax": costs["sell_tax"],
                "slippage_cost": costs["slippage_cost"],
                "total_cost": costs["total_cost"],
                "turnover_bucket": row["turnover_bucket"],
                "day0_price_bucket": row["day0_price_bucket"],
                "day0_volume_shock_bucket": row["day0_volume_shock_bucket"],
                "prior_5d_return_bucket": row["prior_5d_return_bucket"],
                "prior_20d_return_bucket": row["prior_20d_return_bucket"],
            }
        )

    if not rows:
        return empty_trade_report_frame()
    return pd.DataFrame(rows, columns=TRADE_REPORT_COLUMNS)


def summarize_overnight_trades(trades: pd.DataFrame) -> pd.DataFrame:
    """Summarize overnight strategy performance and concentration."""

    if trades.empty:
        return empty_summary_frame()

    frame = trades.copy()
    frame["entry_date"] = pd.to_datetime(frame["entry_date"])
    frame["year"] = frame["entry_date"].dt.year
    if "turnover_bucket" not in frame.columns:
        frame["turnover_bucket"] = bucket_turnover(frame["day0_turnover_twd"])
    total_trades = len(frame)
    total_abs_net_pnl = pd.to_numeric(frame["net_pnl"], errors="coerce").abs().sum()

    specs = [
        ("overall", []),
        ("by_year", ["year"]),
        ("by_market", ["market"]),
        ("by_turnover_bucket", ["turnover_bucket"]),
        ("by_consecutive_limit_up_count", ["consecutive_limit_up_count"]),
        ("by_symbol", ["market", "symbol"]),
    ]
    parts = [
        summarize_trade_group(
            frame,
            summary_level=level,
            group_columns=columns,
            total_trades=total_trades,
            total_abs_net_pnl=total_abs_net_pnl,
        )
        for level, columns in specs
    ]
    output = pd.concat(parts, ignore_index=True)
    return output[SUMMARY_COLUMNS]


def summarize_trade_group(
    trades: pd.DataFrame,
    *,
    summary_level: str,
    group_columns: list[str],
    total_trades: int,
    total_abs_net_pnl: float,
) -> pd.DataFrame:
    grouped = trades.groupby(group_columns, dropna=False, observed=False) if group_columns else [((), trades)]
    rows: list[dict[str, Any]] = []
    for group_key, group in grouped:
        key_values = group_key if isinstance(group_key, tuple) else (group_key,)
        row: dict[str, Any] = {
            "summary_level": summary_level,
            "year": None,
            "market": None,
            "turnover_bucket": None,
            "consecutive_limit_up_count": None,
            "symbol": None,
            **calculate_metrics(group),
        }
        for column, value in zip(group_columns, key_values):
            row[column] = value
        row["trade_share"] = len(group) / total_trades if summary_level == "by_symbol" and total_trades else np.nan
        group_abs_net = pd.to_numeric(group["net_pnl"], errors="coerce").abs().sum()
        row["abs_net_pnl_share"] = (
            group_abs_net / total_abs_net_pnl
            if summary_level == "by_symbol" and total_abs_net_pnl > 0
            else np.nan
        )
        rows.append(row)
    return pd.DataFrame(rows, columns=SUMMARY_COLUMNS)


def calculate_metrics(trades: pd.DataFrame) -> dict[str, float]:
    net_pnl = pd.to_numeric(trades["net_pnl"], errors="coerce").fillna(0.0)
    gross_returns = pd.to_numeric(trades["gross_return"], errors="coerce").dropna()
    net_returns = pd.to_numeric(trades["net_return"], errors="coerce").dropna()
    losses = abs(net_pnl[net_pnl < 0].sum())
    gains = net_pnl[net_pnl > 0].sum()
    profit_factor = float("inf") if losses == 0 and gains > 0 else (float(gains / losses) if losses else 0.0)
    average_turnover = pd.to_numeric(trades["day0_turnover_twd"], errors="coerce").mean()
    average_volume_ratio = pd.to_numeric(trades["volume_ratio_20d"], errors="coerce").mean()
    average_entry = pd.to_numeric(trades["entry_price"], errors="coerce").mean()
    average_shares = pd.to_numeric(trades["shares"], errors="coerce").mean()
    total_buy_notional = pd.to_numeric(trades["buy_notional"], errors="coerce").sum()
    return {
        "trades": int(len(trades)),
        "win_rate": float((net_pnl > 0).mean()) if len(trades) else 0.0,
        "average_gross_return": float(gross_returns.mean()) if not gross_returns.empty else 0.0,
        "median_gross_return": float(gross_returns.median()) if not gross_returns.empty else 0.0,
        "average_net_return": float(net_returns.mean()) if not net_returns.empty else 0.0,
        "median_net_return": float(net_returns.median()) if not net_returns.empty else 0.0,
        "total_net_pnl": float(net_pnl.sum()),
        "profit_factor": profit_factor,
        "max_drawdown": max_drawdown_from_pnl(net_pnl),
        "worst_trade": float(net_pnl.min()) if len(trades) else 0.0,
        "best_trade": float(net_pnl.max()) if len(trades) else 0.0,
        "average_turnover_twd": float(average_turnover) if not pd.isna(average_turnover) else 0.0,
        "average_volume_ratio_20d": float(average_volume_ratio) if not pd.isna(average_volume_ratio) else 0.0,
        "average_entry_price": float(average_entry) if not pd.isna(average_entry) else 0.0,
        "average_shares": float(average_shares) if not pd.isna(average_shares) else 0.0,
        "total_buy_notional": float(total_buy_notional),
    }


def max_drawdown_from_pnl(pnl: pd.Series) -> float:
    values = pd.to_numeric(pnl, errors="coerce").fillna(0.0)
    if values.empty:
        return 0.0
    equity = pd.concat([pd.Series([0.0]), values.reset_index(drop=True)], ignore_index=True).cumsum()
    drawdown = equity - equity.cummax()
    return float(drawdown.min())


def make_trade_id(
    *,
    event_id: str,
    fixed_notional_twd: float,
    shares: int,
    entry_date: pd.Timestamp,
    exit_date: pd.Timestamp,
) -> str:
    raw = (
        f"{STRATEGY_NAME}|{event_id}|{fixed_notional_twd:.2f}|"
        f"{shares}|{entry_date.strftime('%Y%m%d')}|{exit_date.strftime('%Y%m%d')}"
    )
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
    return f"{STRATEGY_NAME}:{digest}"


def delete_strategy_trades(
    conn,
    *,
    strategy_name: str,
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
    markets: Sequence[str],
) -> None:
    placeholders = ", ".join(["?"] * len(markets))
    conn.execute(
        f"""
        DELETE FROM backtest_trades
        WHERE strategy_name = ?
          AND signal_date >= ?
          AND signal_date <= ?
          AND market IN ({placeholders})
        """,
        [strategy_name, pd.Timestamp(start).date(), pd.Timestamp(end).date(), *markets],
    )


def validate_strategy_inputs(
    *,
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
    fixed_notional_twd: float,
    min_turnover_twd: float | None,
    min_volume_ratio_20d: float | None,
    min_close_location: float | None,
    min_price: float | None,
    max_price: float | None,
    max_consecutive_limit_ups: int | None,
    exclude_if_prior_5d_return_above: float | None,
    exclude_if_prior_20d_return_above: float | None,
    commission_rate: float,
    commission_discount: float,
    sell_tax_rate: float,
    slippage_bps_per_side: float,
    minimum_commission_twd: float,
) -> None:
    if pd.Timestamp(end) < pd.Timestamp(start):
        raise ValueError("end must be on or after start")
    if fixed_notional_twd <= 0:
        raise ValueError("fixed_notional_twd must be positive")
    for name, value in {
        "min_turnover_twd": min_turnover_twd,
        "min_volume_ratio_20d": min_volume_ratio_20d,
        "min_close_location": min_close_location,
        "min_price": min_price,
        "max_price": max_price,
        "max_consecutive_limit_ups": max_consecutive_limit_ups,
        "commission_rate": commission_rate,
        "commission_discount": commission_discount,
        "sell_tax_rate": sell_tax_rate,
        "slippage_bps_per_side": slippage_bps_per_side,
        "minimum_commission_twd": minimum_commission_twd,
    }.items():
        if value is not None and value < 0:
            raise ValueError(f"{name} must be non-negative")
    if min_price is not None and max_price is not None and max_price < min_price:
        raise ValueError("max_price must be greater than or equal to min_price")
    for name, value in {
        "exclude_if_prior_5d_return_above": exclude_if_prior_5d_return_above,
        "exclude_if_prior_20d_return_above": exclude_if_prior_20d_return_above,
    }.items():
        if value is not None and not np.isfinite(value):
            raise ValueError(f"{name} must be finite")


def empty_trade_report_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=TRADE_REPORT_COLUMNS)


def empty_summary_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=SUMMARY_COLUMNS)


def default_report_dir() -> Path:
    return get_settings().project_root / "reports"


def json_default(value: Any) -> Any:
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if pd.isna(value):
        return None
    if isinstance(value, np.generic):
        return value.item()
    return value


def parse_bool(value: str | bool) -> bool:
    if isinstance(value, bool):
        return value
    normalized = value.lower().strip()
    if normalized in {"true", "1", "yes", "y"}:
        return True
    if normalized in {"false", "0", "no", "n"}:
        return False
    raise argparse.ArgumentTypeError(f"Expected true/false, got {value!r}")


def print_strategy_summary(trades: pd.DataFrame, summary: pd.DataFrame) -> None:
    if trades.empty or summary.empty:
        print("\nclosed-limit-up overnight strategy: no trades after filters")
        return
    overall = summary[summary["summary_level"].eq("overall")].iloc[0]
    print("\nclosed-limit-up overnight strategy")
    print(f"trades: {int(overall['trades'])}")
    print(f"win rate: {overall['win_rate']:.4f}")
    print(
        "gross return: "
        f"mean {overall['average_gross_return']:.6f}, "
        f"median {overall['median_gross_return']:.6f}"
    )
    print(
        "net return after overnight costs: "
        f"mean {overall['average_net_return']:.6f}, "
        f"median {overall['median_net_return']:.6f}"
    )
    print(
        f"total net pnl: {overall['total_net_pnl']:.2f}, "
        f"profit factor: {overall['profit_factor']:.4f}, "
        f"max drawdown: {overall['max_drawdown']:.2f}"
    )
    print(f"worst trade: {overall['worst_trade']:.2f}, best trade: {overall['best_trade']:.2f}")

    for level, title in [("by_year", "year-by-year"), ("by_market", "market-by-market")]:
        rows = summary[summary["summary_level"].eq(level)]
        if not rows.empty:
            print(f"\n{title}")
            print(
                rows[
                    [
                        "year",
                        "market",
                        "trades",
                        "win_rate",
                        "average_net_return",
                        "median_net_return",
                        "total_net_pnl",
                        "profit_factor",
                        "max_drawdown",
                    ]
                ].to_string(index=False)
            )

    symbols = summary[summary["summary_level"].eq("by_symbol")].sort_values("trades", ascending=False).head(10)
    if not symbols.empty:
        print("\ntop symbol concentration")
        print(
            symbols[
                [
                    "market",
                    "symbol",
                    "trades",
                    "trade_share",
                    "total_net_pnl",
                    "abs_net_pnl_share",
                ]
            ].to_string(index=False)
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Closed-limit-up overnight strategy backtest")
    parser.add_argument("--db", type=Path, default=get_settings().db_path, help="DuckDB path")
    parser.add_argument("--start", required=True, help="Start date, YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="End date, YYYY-MM-DD")
    parser.add_argument("--market", choices=["TWSE", "TPEX", "BOTH"], default="BOTH")
    parser.add_argument("--fixed-notional-twd", type=float, default=100_000.0)
    parser.add_argument("--min-turnover-twd", type=float)
    parser.add_argument("--min-volume-ratio-20d", type=float)
    parser.add_argument("--min-close-location", type=float)
    parser.add_argument("--min-price", type=float)
    parser.add_argument("--max-price", type=float)
    parser.add_argument("--max-consecutive-limit-ups", type=int)
    parser.add_argument("--only-first-limit-up", nargs="?", const=True, default=False, type=parse_bool)
    parser.add_argument("--exclude-if-prior-5d-return-above", type=float)
    parser.add_argument("--exclude-if-prior-20d-return-above", type=float)
    parser.add_argument("--commission-rate", type=float, default=0.001425)
    parser.add_argument("--commission-discount", type=float, default=0.28)
    parser.add_argument(
        "--sell-tax-rate",
        type=float,
        default=0.003,
        help="Normal overnight stock sale tax. Defaults to 0.30%, not day-trade tax.",
    )
    parser.add_argument("--slippage-bps-per-side", type=float, default=5.0)
    parser.add_argument("--minimum-commission-twd", type=float, default=20.0)
    parser.add_argument("--output-dir", type=Path, default=default_report_dir())
    parser.add_argument(
        "--skip-build-events",
        action="store_true",
        help="Use existing event_candidates instead of rebuilding for the window",
    )
    parser.add_argument(
        "--no-store-trades",
        action="store_true",
        help="Write CSV reports without replacing this strategy's rows in backtest_trades",
    )
    args = parser.parse_args()

    markets = normalize_markets([args.market])
    trades, summary = run_closed_limit_up_overnight(
        db_path=args.db,
        start=args.start,
        end=args.end,
        markets=markets,
        fixed_notional_twd=args.fixed_notional_twd,
        min_turnover_twd=args.min_turnover_twd,
        min_volume_ratio_20d=args.min_volume_ratio_20d,
        min_close_location=args.min_close_location,
        min_price=args.min_price,
        max_price=args.max_price,
        max_consecutive_limit_ups=args.max_consecutive_limit_ups,
        only_first_limit_up=args.only_first_limit_up,
        exclude_if_prior_5d_return_above=args.exclude_if_prior_5d_return_above,
        exclude_if_prior_20d_return_above=args.exclude_if_prior_20d_return_above,
        commission_rate=args.commission_rate,
        commission_discount=args.commission_discount,
        sell_tax_rate=args.sell_tax_rate,
        slippage_bps_per_side=args.slippage_bps_per_side,
        minimum_commission_twd=args.minimum_commission_twd,
        output_dir=args.output_dir,
        rebuild_events=not args.skip_build_events,
        store_trades=not args.no_store_trades,
    )
    trades_path = args.output_dir / "closed_limit_up_overnight_trades.csv"
    summary_path = args.output_dir / "closed_limit_up_overnight_summary.csv"
    print(f"Wrote {len(trades)} trades to {trades_path}")
    print(f"Wrote {len(summary)} summary rows to {summary_path}")
    print_strategy_summary(trades, summary)


if __name__ == "__main__":
    main()
