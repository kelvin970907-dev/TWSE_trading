"""Simple daily limit-momentum strategy backtest."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from config.settings import get_settings
from src.backtests.costs import TaiwanCostModel, calculate_trade_costs
from src.backtests.event_study import (
    EVENT_TYPES,
    build_event_candidates,
    event_study_next_day,
    normalize_markets,
)
from src.db import get_connection, init_db, upsert_dataframe
from src.features.flow_features import apply_flow_feature_filters
from src.features.limit_features import add_limit_features
from src.features.regime_filters import (
    MARKET_REGIME_FILTERS,
    SECTOR_FILTERS,
    apply_event_context_filters,
    normalize_market_regime_filter,
    normalize_sector_filter,
)


STRATEGY_NAME = "strategy_limit_momentum_daily"
BOARD_LOT_SIZE = 1000
DEFAULT_EVENT_TYPES = ["near_limit_8_9"]
PATH_ASSUMPTIONS = ["optimistic", "pessimistic", "close_only"]

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
    "day0_turnover_twd",
    "volume_ratio_20d",
    "close_location",
    "fixed_notional_twd",
    "buy_notional",
    "sell_notional",
    "buy_commission",
    "sell_commission",
    "sell_tax",
    "slippage_cost",
    "total_cost",
    "day1_open",
    "day1_high",
    "day1_low",
    "day1_close",
    "take_profit_pct",
    "stop_loss_pct",
    "path_assumption",
    "take_profit_price",
    "stop_loss_price",
]

SUMMARY_COLUMNS = [
    "summary_level",
    "path_assumption",
    "event_type",
    "year",
    "market",
    "number_of_trades",
    "win_rate",
    "average_gross_return",
    "average_net_return",
    "median_net_return",
    "total_net_pnl",
    "profit_factor",
    "max_drawdown",
    "average_turnover",
]


def run_strategy_limit_momentum(
    *,
    db_path: Path | str,
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
    event_types: Sequence[str] | None = None,
    fixed_notional_twd: float = 100_000.0,
    min_turnover_twd: float | None = None,
    min_volume_ratio_20d: float | None = None,
    min_close_location: float | None = None,
    markets: Sequence[str] | None = None,
    commission_rate: float = 0.001425,
    commission_discount: float = 0.28,
    sell_tax_rate: float = 0.0015,
    slippage_bps_per_side: float = 5.0,
    minimum_commission_twd: float = 20.0,
    is_day_trade: bool = True,
    take_profit_pct: float = 0.03,
    stop_loss_pct: float = 0.02,
    path_assumption: str = "pessimistic",
    market_regime: str = "none",
    sector_filter: str = "none",
    require_foreign_not_selling_heavily: bool = False,
    require_investment_trust_buying: bool = False,
    avoid_margin_overcrowded: bool = False,
    prefer_short_balance_rising_before_limit_up: bool = False,
    foreign_heavy_sell_threshold: float = -0.05,
    margin_crowding_threshold: float = 5.0,
    output_dir: Path | str | None = None,
    rebuild_events: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Run the first crude daily limit-momentum strategy."""

    validate_strategy_inputs(
        event_types=event_types or DEFAULT_EVENT_TYPES,
        fixed_notional_twd=fixed_notional_twd,
        min_turnover_twd=min_turnover_twd,
        min_volume_ratio_20d=min_volume_ratio_20d,
        min_close_location=min_close_location,
        take_profit_pct=take_profit_pct,
        stop_loss_pct=stop_loss_pct,
        path_assumption=path_assumption,
        market_regime=market_regime,
        sector_filter=sector_filter,
        foreign_heavy_sell_threshold=foreign_heavy_sell_threshold,
        margin_crowding_threshold=margin_crowding_threshold,
    )
    init_db(db_path)
    market_values = normalize_markets(markets)
    selected_event_types = normalize_event_types(event_types)
    selected_path_assumption = normalize_path_assumption(path_assumption)
    market_regime_value = normalize_market_regime_filter(market_regime)
    sector_filter_value = normalize_sector_filter(sector_filter)

    if rebuild_events:
        build_event_candidates(
            db_path=db_path,
            start=start,
            end=end,
            markets=market_values,
        )

    with get_connection(db_path) as conn:
        events = load_strategy_events(
            conn,
            start=start,
            end=end,
            event_types=selected_event_types,
            markets=market_values,
            min_turnover_twd=min_turnover_twd,
            min_volume_ratio_20d=min_volume_ratio_20d,
            min_close_location=min_close_location,
        )
        events = apply_event_context_filters(
            conn,
            events,
            market_regime=market_regime_value,
            sector_filter=sector_filter_value,
        )
        events = apply_flow_feature_filters(
            events,
            require_foreign_not_selling_heavily=require_foreign_not_selling_heavily,
            require_investment_trust_buying=require_investment_trust_buying,
            avoid_margin_overcrowded=avoid_margin_overcrowded,
            prefer_short_balance_rising_before_limit_up=prefer_short_balance_rising_before_limit_up,
            foreign_heavy_sell_threshold=foreign_heavy_sell_threshold,
            margin_crowding_threshold=margin_crowding_threshold,
        )
        day1_prices = load_day1_daily_prices(conn)
        trades_by_path = {
            assumption: build_strategy_trades(
                events,
                day1_prices,
                fixed_notional_twd=fixed_notional_twd,
                commission_rate=commission_rate,
                commission_discount=commission_discount,
                sell_tax_rate=sell_tax_rate,
                slippage_bps_per_side=slippage_bps_per_side,
                minimum_commission_twd=minimum_commission_twd,
                is_day_trade=is_day_trade,
                take_profit_pct=take_profit_pct,
                stop_loss_pct=stop_loss_pct,
                path_assumption=assumption,
                market_regime=market_regime_value,
                sector_filter=sector_filter_value,
                require_foreign_not_selling_heavily=require_foreign_not_selling_heavily,
                require_investment_trust_buying=require_investment_trust_buying,
                avoid_margin_overcrowded=avoid_margin_overcrowded,
                prefer_short_balance_rising_before_limit_up=prefer_short_balance_rising_before_limit_up,
                foreign_heavy_sell_threshold=foreign_heavy_sell_threshold,
                margin_crowding_threshold=margin_crowding_threshold,
            )
            for assumption in PATH_ASSUMPTIONS
        }
        trades = trades_by_path[selected_path_assumption]
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

    comparison_trades = concatenate_trade_frames(trades_by_path.values())
    summary = summarize_strategy_trades(comparison_trades)
    report_dir = Path(output_dir) if output_dir is not None else default_report_dir()
    report_dir.mkdir(parents=True, exist_ok=True)
    trades.to_csv(report_dir / "strategy_limit_momentum_trades.csv", index=False)
    summary.to_csv(report_dir / "strategy_limit_momentum_summary.csv", index=False)
    return trades, summary


def load_strategy_events(
    conn,
    *,
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
    event_types: Sequence[str],
    markets: Sequence[str],
    min_turnover_twd: float | None,
    min_volume_ratio_20d: float | None,
    min_close_location: float | None,
) -> pd.DataFrame:
    """Load and filter event candidates for the strategy."""

    event_placeholders = ", ".join(["?"] * len(event_types))
    market_placeholders = ", ".join(["?"] * len(markets))
    filters = [
        "trade_date >= ?",
        "trade_date <= ?",
        f"event_type IN ({event_placeholders})",
        f"market IN ({market_placeholders})",
    ]
    params: list[Any] = [
        pd.Timestamp(start).date(),
        pd.Timestamp(end).date(),
        *event_types,
        *markets,
    ]
    if min_turnover_twd is not None:
        filters.append("day0_turnover_twd >= ?")
        params.append(min_turnover_twd)
    if min_volume_ratio_20d is not None:
        filters.append("volume_ratio_20d >= ?")
        params.append(min_volume_ratio_20d)
    if min_close_location is not None:
        filters.append("close_location >= ?")
        params.append(min_close_location)

    return conn.execute(
        f"""
        SELECT *
        FROM event_candidates
        WHERE {" AND ".join(filters)}
        ORDER BY trade_date, market, symbol, event_type
        """,
        params,
    ).fetch_df()


def load_day1_daily_prices(conn) -> pd.DataFrame:
    """Load Day 1 daily bars needed for the strategy."""

    return conn.execute(
        """
        SELECT
            symbol,
            market,
            trade_date AS day1_trade_date,
            open AS day1_open,
            high AS day1_high,
            low AS day1_low,
            close AS day1_close
        FROM daily_prices
        ORDER BY market, symbol, trade_date
        """
    ).fetch_df()


def build_strategy_trades(
    events: pd.DataFrame,
    day1_prices: pd.DataFrame,
    *,
    fixed_notional_twd: float,
    commission_rate: float,
    commission_discount: float,
    sell_tax_rate: float,
    slippage_bps_per_side: float,
    minimum_commission_twd: float,
    is_day_trade: bool,
    take_profit_pct: float,
    stop_loss_pct: float,
    path_assumption: str,
    market_regime: str,
    sector_filter: str,
    require_foreign_not_selling_heavily: bool,
    require_investment_trust_buying: bool,
    avoid_margin_overcrowded: bool,
    prefer_short_balance_rising_before_limit_up: bool,
    foreign_heavy_sell_threshold: float,
    margin_crowding_threshold: float,
) -> pd.DataFrame:
    """Build trade rows from filtered event candidates."""

    normalized_path = normalize_path_assumption(path_assumption)
    if events.empty:
        return empty_trade_report_frame()

    frame = events.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.normalize()
    frame["next_trade_date"] = pd.to_datetime(frame["next_trade_date"]).dt.normalize()
    frame["symbol"] = frame["symbol"].astype("string").str.strip()
    frame["market"] = frame["market"].astype("string").str.upper().str.strip()

    day1 = day1_prices.copy()
    day1["day1_trade_date"] = pd.to_datetime(day1["day1_trade_date"]).dt.normalize()
    day1["symbol"] = day1["symbol"].astype("string").str.strip()
    day1["market"] = day1["market"].astype("string").str.upper().str.strip()
    frame = frame.merge(
        day1,
        left_on=["market", "symbol", "next_trade_date"],
        right_on=["market", "symbol", "day1_trade_date"],
        how="left",
    )
    frame = frame.dropna(subset=["day1_open", "day1_high", "day1_low", "day1_close"]).copy()
    if frame.empty:
        return empty_trade_report_frame()

    rows: list[dict[str, Any]] = []
    for row in frame.to_dict("records"):
        entry_price = float(row["day1_open"])
        exit_plan = simulate_daily_ohlc_exit(
            entry_price=entry_price,
            day_high=float(row["day1_high"]),
            day_low=float(row["day1_low"]),
            day_close=float(row["day1_close"]),
            take_profit_pct=take_profit_pct,
            stop_loss_pct=stop_loss_pct,
            path_assumption=normalized_path,
        )
        exit_price = float(exit_plan["exit_price"])
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
            is_day_trade=is_day_trade,
        )
        signal_date = pd.Timestamp(row["trade_date"])
        entry_date = pd.Timestamp(row["day1_trade_date"])
        trade_id = make_trade_id(
            strategy_name=STRATEGY_NAME,
            event_id=str(row["event_id"]),
            fixed_notional_twd=fixed_notional_twd,
            entry_date=entry_date,
            path_assumption=normalized_path,
            take_profit_pct=take_profit_pct,
            stop_loss_pct=stop_loss_pct,
        )
        metadata = {
            "event_id": row["event_id"],
            "event_type": row["event_type"],
            "day0_return": row["day0_return"],
            "day0_turnover_twd": row["day0_turnover_twd"],
            "volume_ratio_20d": row["volume_ratio_20d"],
            "close_location": row["close_location"],
            "fixed_notional_twd": fixed_notional_twd,
            "board_lot_size": BOARD_LOT_SIZE,
            "commission_rate": commission_rate,
            "commission_discount": commission_discount,
            "sell_tax_rate": sell_tax_rate,
            "slippage_bps_per_side": slippage_bps_per_side,
            "minimum_commission_twd": minimum_commission_twd,
            "is_day_trade": is_day_trade,
            "take_profit_pct": take_profit_pct,
            "stop_loss_pct": stop_loss_pct,
            "path_assumption": normalized_path,
            "market_regime": market_regime,
            "sector_filter": sector_filter,
            "require_foreign_not_selling_heavily": require_foreign_not_selling_heavily,
            "require_investment_trust_buying": require_investment_trust_buying,
            "avoid_margin_overcrowded": avoid_margin_overcrowded,
            "prefer_short_balance_rising_before_limit_up": prefer_short_balance_rising_before_limit_up,
            "foreign_heavy_sell_threshold": foreign_heavy_sell_threshold,
            "margin_crowding_threshold": margin_crowding_threshold,
            "take_profit_price": exit_plan["take_profit_price"],
            "stop_loss_price": exit_plan["stop_loss_price"],
        }
        rows.append(
            {
                "trade_id": trade_id,
                "strategy_name": STRATEGY_NAME,
                "symbol": row["symbol"],
                "market": row["market"],
                "signal_date": signal_date,
                "entry_date": entry_date,
                "entry_time": pd.NaT,
                "exit_date": entry_date,
                "exit_time": pd.NaT,
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
                "holding_minutes": 270.0,
                "exit_reason": exit_plan["exit_reason"],
                "metadata_json": json.dumps(metadata, ensure_ascii=False, default=_json_default),
                "event_id": row["event_id"],
                "event_type": row["event_type"],
                "day0_return": row["day0_return"],
                "day0_turnover_twd": row["day0_turnover_twd"],
                "volume_ratio_20d": row["volume_ratio_20d"],
                "close_location": row["close_location"],
                "fixed_notional_twd": fixed_notional_twd,
                "buy_notional": costs["buy_notional"],
                "sell_notional": costs["sell_notional"],
                "buy_commission": costs["buy_commission"],
                "sell_commission": costs["sell_commission"],
                "sell_tax": costs["sell_tax"],
                "slippage_cost": costs["slippage_cost"],
                "total_cost": costs["total_cost"],
                "day1_open": entry_price,
                "day1_high": row["day1_high"],
                "day1_low": row["day1_low"],
                "day1_close": row["day1_close"],
                "take_profit_pct": take_profit_pct,
                "stop_loss_pct": stop_loss_pct,
                "path_assumption": normalized_path,
                "take_profit_price": exit_plan["take_profit_price"],
                "stop_loss_price": exit_plan["stop_loss_price"],
            }
        )

    if not rows:
        return empty_trade_report_frame()
    return pd.DataFrame(rows, columns=TRADE_REPORT_COLUMNS)


def summarize_strategy_trades(trades: pd.DataFrame) -> pd.DataFrame:
    """Summarize strategy trades overall and by requested dimensions."""

    if trades.empty:
        return empty_strategy_summary_frame()

    frame = trades.copy()
    frame["entry_date"] = pd.to_datetime(frame["entry_date"])
    frame["year"] = frame["entry_date"].dt.year
    if "path_assumption" not in frame.columns:
        frame["path_assumption"] = "unspecified"
    summary_specs = [
        ("overall", ["path_assumption"]),
        ("by_event_type", ["path_assumption", "event_type"]),
        ("by_year", ["path_assumption", "year"]),
        ("by_market", ["path_assumption", "market"]),
    ]
    parts = [
        summarize_trade_group(frame, summary_level=summary_level, group_columns=group_columns)
        for summary_level, group_columns in summary_specs
    ]
    return pd.concat(parts, ignore_index=True)[SUMMARY_COLUMNS]


def summarize_trade_group(
    trades: pd.DataFrame,
    *,
    summary_level: str,
    group_columns: list[str],
) -> pd.DataFrame:
    if group_columns:
        grouped = trades.groupby(group_columns, dropna=False)
    else:
        grouped = [((), trades)]

    rows: list[dict[str, Any]] = []
    for group_key, group in grouped:
        key_values = group_key if isinstance(group_key, tuple) else (group_key,)
        row: dict[str, Any] = {
            "summary_level": summary_level,
            "path_assumption": None,
            "event_type": None,
            "year": None,
            "market": None,
            **calculate_strategy_metrics(group),
        }
        for column, value in zip(group_columns, key_values):
            row[column] = value
        rows.append(row)
    return pd.DataFrame(rows, columns=SUMMARY_COLUMNS)


def calculate_strategy_metrics(trades: pd.DataFrame) -> dict[str, float]:
    net_returns = pd.to_numeric(trades["net_return"], errors="coerce").dropna()
    gross_returns = pd.to_numeric(trades["gross_return"], errors="coerce").dropna()
    net_pnl = pd.to_numeric(trades["net_pnl"], errors="coerce").fillna(0.0)
    gross_pnl = pd.to_numeric(trades["gross_pnl"], errors="coerce").fillna(0.0)
    losses = abs(net_pnl[net_pnl < 0].sum())
    gains = net_pnl[net_pnl > 0].sum()
    profit_factor = float("inf") if losses == 0 and gains > 0 else (float(gains / losses) if losses else 0.0)
    average_turnover = pd.to_numeric(trades["day0_turnover_twd"], errors="coerce").mean()
    return {
        "number_of_trades": int(len(trades)),
        "win_rate": float((net_pnl > 0).mean()) if len(trades) else 0.0,
        "average_gross_return": float(gross_returns.mean()) if not gross_returns.empty else 0.0,
        "average_net_return": float(net_returns.mean()) if not net_returns.empty else 0.0,
        "median_net_return": float(net_returns.median()) if not net_returns.empty else 0.0,
        "total_net_pnl": float(net_pnl.sum()),
        "profit_factor": profit_factor,
        "max_drawdown": max_drawdown_from_pnl(net_pnl),
        "average_turnover": float(average_turnover) if not pd.isna(average_turnover) else 0.0,
    }


def max_drawdown_from_pnl(pnl: pd.Series) -> float:
    if pnl.empty:
        return 0.0
    equity = pnl.cumsum()
    running_peak = equity.cummax()
    drawdown = equity - running_peak
    return float(drawdown.min())


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


def calculate_board_lot_shares(*, fixed_notional_twd: float, entry_price: float) -> int:
    if entry_price <= 0:
        return 0
    raw_shares = int(fixed_notional_twd // entry_price)
    return (raw_shares // BOARD_LOT_SIZE) * BOARD_LOT_SIZE


def simulate_daily_ohlc_exit(
    *,
    entry_price: float,
    day_high: float,
    day_low: float,
    day_close: float,
    take_profit_pct: float,
    stop_loss_pct: float,
    path_assumption: str,
) -> dict[str, float | str]:
    """Simulate an intraday exit from daily OHLC under a path assumption."""

    normalized_path = normalize_path_assumption(path_assumption)
    if take_profit_pct <= 0:
        raise ValueError("take_profit_pct must be positive")
    if stop_loss_pct <= 0:
        raise ValueError("stop_loss_pct must be positive")
    for name, value in {
        "entry_price": entry_price,
        "day_high": day_high,
        "day_low": day_low,
        "day_close": day_close,
    }.items():
        if not np.isfinite(value) or value <= 0:
            raise ValueError(f"{name} must be a positive finite number")

    take_profit_price = float(entry_price * (1.0 + take_profit_pct))
    stop_loss_price = float(entry_price * (1.0 - stop_loss_pct))

    if normalized_path == "close_only":
        exit_price = float(day_close)
        exit_reason = "close_exit"
    elif normalized_path == "optimistic":
        if day_high >= take_profit_price:
            exit_price = take_profit_price
            exit_reason = "take_profit"
        elif day_low <= stop_loss_price:
            exit_price = stop_loss_price
            exit_reason = "stop_loss"
        else:
            exit_price = float(day_close)
            exit_reason = "close_exit"
    else:
        if day_low <= stop_loss_price:
            exit_price = stop_loss_price
            exit_reason = "stop_loss"
        elif day_high >= take_profit_price:
            exit_price = take_profit_price
            exit_reason = "take_profit"
        else:
            exit_price = float(day_close)
            exit_reason = "close_exit"

    return {
        "exit_price": exit_price,
        "exit_reason": exit_reason,
        "take_profit_price": take_profit_price,
        "stop_loss_price": stop_loss_price,
    }


def make_trade_id(
    *,
    strategy_name: str,
    event_id: str,
    fixed_notional_twd: float,
    entry_date: pd.Timestamp,
    path_assumption: str,
    take_profit_pct: float,
    stop_loss_pct: float,
) -> str:
    raw = (
        f"{strategy_name}|{event_id}|{fixed_notional_twd:.2f}|"
        f"{entry_date.strftime('%Y%m%d')}|{path_assumption}|{take_profit_pct:.6f}|{stop_loss_pct:.6f}"
    )
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
    return f"{strategy_name}:{digest}"


def normalize_event_types(event_types: Sequence[str] | None) -> list[str]:
    values = list(event_types or DEFAULT_EVENT_TYPES)
    invalid = sorted(set(values) - set(EVENT_TYPES))
    if invalid:
        raise ValueError(f"Unsupported event type(s): {invalid}. Valid values are {EVENT_TYPES}")
    return values


def normalize_path_assumption(path_assumption: str) -> str:
    value = path_assumption.lower().strip()
    if value not in PATH_ASSUMPTIONS:
        raise ValueError(f"Unsupported path_assumption: {path_assumption}. Valid values are {PATH_ASSUMPTIONS}")
    return value


def validate_strategy_inputs(
    *,
    event_types: Sequence[str],
    fixed_notional_twd: float,
    min_turnover_twd: float | None,
    min_volume_ratio_20d: float | None,
    min_close_location: float | None,
    take_profit_pct: float,
    stop_loss_pct: float,
    path_assumption: str,
    market_regime: str,
    sector_filter: str,
    foreign_heavy_sell_threshold: float,
    margin_crowding_threshold: float,
) -> None:
    normalize_event_types(event_types)
    normalize_path_assumption(path_assumption)
    normalize_market_regime_filter(market_regime)
    normalize_sector_filter(sector_filter)
    if fixed_notional_twd <= 0:
        raise ValueError("fixed_notional_twd must be positive")
    if margin_crowding_threshold < 0:
        raise ValueError("margin_crowding_threshold must be non-negative")
    if take_profit_pct <= 0:
        raise ValueError("take_profit_pct must be positive")
    if stop_loss_pct <= 0:
        raise ValueError("stop_loss_pct must be positive")
    for name, value in {
        "min_turnover_twd": min_turnover_twd,
        "min_volume_ratio_20d": min_volume_ratio_20d,
        "min_close_location": min_close_location,
    }.items():
        if value is not None and value < 0:
            raise ValueError(f"{name} must be non-negative")


def empty_trade_report_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=TRADE_REPORT_COLUMNS)


def empty_strategy_summary_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=SUMMARY_COLUMNS)


def concatenate_trade_frames(frames: Iterable[pd.DataFrame]) -> pd.DataFrame:
    non_empty = [frame for frame in frames if not frame.empty]
    if not non_empty:
        return empty_trade_report_frame()
    return pd.concat(non_empty, ignore_index=True)


def default_report_dir() -> Path:
    return get_settings().project_root / "reports"


def _json_default(value: Any) -> Any:
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


def prepare_limit_momentum_features(daily_prices: pd.DataFrame) -> pd.DataFrame:
    """Compatibility helper for earlier research notebooks."""

    return add_limit_features(daily_prices)


def select_events(
    daily_prices: pd.DataFrame,
    event_column: str,
    min_dollar_volume: float | None = None,
) -> pd.DataFrame:
    """Compatibility helper for earlier DataFrame-based experiments."""

    features = prepare_limit_momentum_features(daily_prices)
    if event_column not in features.columns:
        raise ValueError(f"Unsupported event column: {event_column}")
    events = features[features[event_column].fillna(False)].copy()
    liquidity_col = "turnover_twd" if "turnover_twd" in events.columns else "dollar_volume"
    if min_dollar_volume is not None and liquidity_col in events.columns:
        events = events[events[liquidity_col] >= min_dollar_volume]
    return events


def backtest_next_day_continuation(
    daily_prices: pd.DataFrame,
    event_column: str = "is_plus_8_to_9_not_limit",
    min_dollar_volume: float | None = None,
    cost_model: TaiwanCostModel | None = None,
) -> pd.DataFrame:
    """Compatibility wrapper for the first event-study helper."""

    features = prepare_limit_momentum_features(daily_prices)
    return event_study_next_day(
        features,
        event_column=event_column,
        cost_model=cost_model,
        min_dollar_volume=min_dollar_volume,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the daily limit momentum strategy")
    parser.add_argument("--db", type=Path, default=get_settings().db_path, help="DuckDB path")
    parser.add_argument("--start", required=True, help="Start date, YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="End date, YYYY-MM-DD")
    parser.add_argument(
        "--event-types",
        nargs="+",
        default=DEFAULT_EVENT_TYPES,
        choices=EVENT_TYPES,
        help="Event types to trade",
    )
    parser.add_argument("--fixed-notional-twd", type=float, default=100_000.0)
    parser.add_argument("--min-turnover-twd", type=float)
    parser.add_argument("--min-volume-ratio-20d", type=float)
    parser.add_argument("--min-close-location", type=float)
    parser.add_argument("--market", choices=["TWSE", "TPEX", "BOTH"], default="BOTH")
    parser.add_argument("--commission-rate", type=float, default=0.001425)
    parser.add_argument("--commission-discount", type=float, default=0.28)
    parser.add_argument("--sell-tax-rate", type=float, default=0.0015)
    parser.add_argument("--slippage-bps-per-side", type=float, default=5.0)
    parser.add_argument("--minimum-commission-twd", type=float, default=20.0)
    parser.add_argument("--take-profit-pct", type=float, default=0.03)
    parser.add_argument("--stop-loss-pct", type=float, default=0.02)
    parser.add_argument(
        "--path-assumption",
        choices=PATH_ASSUMPTIONS,
        default="pessimistic",
        help="Daily OHLC path assumption used for stored trades. Summary compares all assumptions.",
    )
    parser.add_argument("--market-regime", choices=MARKET_REGIME_FILTERS, default="none")
    parser.add_argument("--sector-filter", choices=SECTOR_FILTERS, default="none")
    parser.add_argument("--require-foreign-not-selling-heavily", type=parse_bool, default=False)
    parser.add_argument("--require-investment-trust-buying", type=parse_bool, default=False)
    parser.add_argument("--avoid-margin-overcrowded", type=parse_bool, default=False)
    parser.add_argument("--prefer-short-balance-rising-before-limit-up", type=parse_bool, default=False)
    parser.add_argument("--foreign-heavy-sell-threshold", type=float, default=-0.05)
    parser.add_argument("--margin-crowding-threshold", type=float, default=5.0)
    parser.add_argument("--output-dir", type=Path, default=default_report_dir())
    parser.add_argument(
        "--skip-build-events",
        action="store_true",
        help="Use existing event_candidates instead of rebuilding for the window",
    )
    args = parser.parse_args()

    trades, summary = run_strategy_limit_momentum(
        db_path=args.db,
        start=args.start,
        end=args.end,
        event_types=args.event_types,
        fixed_notional_twd=args.fixed_notional_twd,
        min_turnover_twd=args.min_turnover_twd,
        min_volume_ratio_20d=args.min_volume_ratio_20d,
        min_close_location=args.min_close_location,
        markets=normalize_markets([args.market]),
        commission_rate=args.commission_rate,
        commission_discount=args.commission_discount,
        sell_tax_rate=args.sell_tax_rate,
        slippage_bps_per_side=args.slippage_bps_per_side,
        minimum_commission_twd=args.minimum_commission_twd,
        take_profit_pct=args.take_profit_pct,
        stop_loss_pct=args.stop_loss_pct,
        path_assumption=args.path_assumption,
        market_regime=args.market_regime,
        sector_filter=args.sector_filter,
        require_foreign_not_selling_heavily=args.require_foreign_not_selling_heavily,
        require_investment_trust_buying=args.require_investment_trust_buying,
        avoid_margin_overcrowded=args.avoid_margin_overcrowded,
        prefer_short_balance_rising_before_limit_up=args.prefer_short_balance_rising_before_limit_up,
        foreign_heavy_sell_threshold=args.foreign_heavy_sell_threshold,
        margin_crowding_threshold=args.margin_crowding_threshold,
        output_dir=args.output_dir,
        rebuild_events=not args.skip_build_events,
    )

    trades_path = args.output_dir / "strategy_limit_momentum_trades.csv"
    summary_path = args.output_dir / "strategy_limit_momentum_summary.csv"
    print(f"Wrote {len(trades)} trades to {trades_path}")
    print(f"Wrote {len(summary)} summary rows to {summary_path}")
    print(f"Stored trades use path_assumption={args.path_assumption}")
    print("\ncomparison by path_assumption")
    overall = summary[summary["summary_level"] == "overall"]
    print(overall.to_string(index=False) if not overall.empty else "(no trades)")
    print("\nperformance by event_type")
    by_event = summary[
        (summary["summary_level"] == "by_event_type") & (summary["path_assumption"] == args.path_assumption)
    ]
    print(by_event.to_string(index=False) if not by_event.empty else "(no trades)")
    print("\nperformance by year")
    by_year = summary[(summary["summary_level"] == "by_year") & (summary["path_assumption"] == args.path_assumption)]
    print(by_year.to_string(index=False) if not by_year.empty else "(no trades)")
    print("\nperformance by market")
    by_market = summary[
        (summary["summary_level"] == "by_market") & (summary["path_assumption"] == args.path_assumption)
    ]
    print(by_market.to_string(index=False) if not by_market.empty else "(no trades)")


if __name__ == "__main__":
    main()
