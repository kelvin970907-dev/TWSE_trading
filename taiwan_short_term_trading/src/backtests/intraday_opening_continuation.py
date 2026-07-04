"""Intraday Day 1 opening-continuation strategy."""

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
from src.backtests.event_study import EVENT_CANDIDATE_COLUMNS, EVENT_TYPES, normalize_markets
from src.backtests.strategy_limit_momentum import BACKTEST_TRADE_COLUMNS, BOARD_LOT_SIZE
from src.db import get_connection, init_db, upsert_dataframe
from src.features.flow_features import apply_flow_feature_filters
from src.features.regime_filters import (
    MARKET_REGIME_FILTERS,
    SECTOR_FILTERS,
    apply_event_context_filters,
    normalize_market_regime_filter,
    normalize_sector_filter,
)


STRATEGY_NAME = "intraday_opening_continuation"
DEFAULT_EVENT_TYPES = ["closed_limit_up", "near_limit_9_10"]
DEFAULT_TIME_EXIT = "13:20"
TAIWAN_SESSION_OPEN = "09:00"
TAIWAN_SESSION_CLOSE = "13:30"
TAIEX_SYMBOLS = ["TAIEX", "TWII", "^TWII", "IX0001", "0000"]

TRADE_REPORT_COLUMNS = BACKTEST_TRADE_COLUMNS + [
    "event_id",
    "event_type",
    "day0_return",
    "day0_close",
    "day0_turnover_twd",
    "volume_ratio_20d",
    "close_location",
    "next_trade_date",
    "day1_open",
    "open_gap_pct",
    "entry_vwap",
    "entry_window_minutes",
    "opening_window_low",
    "open_drawdown_pct",
    "opening_window_volume",
    "normal_opening_window_volume",
    "open_volume_ratio",
    "take_profit_price",
    "stop_loss_price",
    "time_exit",
    "vwap_fail_exit",
    "hold_locked_limit_up",
]

SUMMARY_COLUMNS = [
    "summary_level",
    "event_type",
    "market",
    "year",
    "number_of_trades",
    "win_rate",
    "average_gross_return",
    "average_net_return",
    "median_net_return",
    "total_net_pnl",
    "profit_factor",
    "max_drawdown",
    "average_holding_minutes",
    "average_open_gap_pct",
    "average_open_volume_ratio",
]


def run_intraday_opening_continuation(
    *,
    db_path: Path | str,
    event_types: Sequence[str] | None = None,
    start: str | pd.Timestamp | None = None,
    end: str | pd.Timestamp | None = None,
    markets: Sequence[str] | None = None,
    entry_window_minutes: int = 3,
    min_open_gap_pct: float = 0.005,
    max_open_gap_pct: float = 0.05,
    allowed_open_drawdown_pct: float = 0.01,
    min_open_volume_ratio: float = 1.0,
    take_profit_pct: float = 0.03,
    stop_loss_pct: float = 0.015,
    vwap_fail_exit: bool = True,
    time_exit: str = DEFAULT_TIME_EXIT,
    hold_locked_limit_up: bool = False,
    locked_limit_up_pct: float = 0.095,
    fixed_notional_twd: float = 100_000.0,
    commission_rate: float = 0.001425,
    commission_discount: float = 0.28,
    sell_tax_rate: float = 0.0015,
    slippage_bps_per_side: float = 5.0,
    minimum_commission_twd: float = 20.0,
    taiex_regime_filter: bool = False,
    market_regime: str = "none",
    sector_filter: str = "none",
    require_foreign_not_selling_heavily: bool = False,
    require_investment_trust_buying: bool = False,
    avoid_margin_overcrowded: bool = False,
    prefer_short_balance_rising_before_limit_up: bool = False,
    foreign_heavy_sell_threshold: float = -0.05,
    margin_crowding_threshold: float = 5.0,
    output_dir: Path | str | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Run the intraday opening-continuation strategy and write CSV reports."""

    validate_strategy_inputs(
        event_types=event_types or DEFAULT_EVENT_TYPES,
        entry_window_minutes=entry_window_minutes,
        min_open_gap_pct=min_open_gap_pct,
        max_open_gap_pct=max_open_gap_pct,
        allowed_open_drawdown_pct=allowed_open_drawdown_pct,
        min_open_volume_ratio=min_open_volume_ratio,
        take_profit_pct=take_profit_pct,
        stop_loss_pct=stop_loss_pct,
        fixed_notional_twd=fixed_notional_twd,
        locked_limit_up_pct=locked_limit_up_pct,
        time_exit=time_exit,
        market_regime=market_regime,
        sector_filter=sector_filter,
        margin_crowding_threshold=margin_crowding_threshold,
    )

    init_db(db_path)
    market_values = normalize_markets(markets)
    selected_event_types = normalize_event_types(event_types)
    market_regime_value = normalize_market_regime_filter(market_regime)
    sector_filter_value = normalize_sector_filter(sector_filter)

    with get_connection(db_path) as conn:
        events = load_event_candidates(
            conn,
            event_types=selected_event_types,
            markets=market_values,
            start=start,
            end=end,
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
        intraday = load_intraday_for_events(conn, events)
        taiex_regime = load_taiex_regime(conn) if taiex_regime_filter else pd.DataFrame()

        trades = build_intraday_trades(
            events,
            intraday,
            taiex_regime=taiex_regime,
            entry_window_minutes=entry_window_minutes,
            min_open_gap_pct=min_open_gap_pct,
            max_open_gap_pct=max_open_gap_pct,
            allowed_open_drawdown_pct=allowed_open_drawdown_pct,
            min_open_volume_ratio=min_open_volume_ratio,
            take_profit_pct=take_profit_pct,
            stop_loss_pct=stop_loss_pct,
            vwap_fail_exit=vwap_fail_exit,
            time_exit=time_exit,
            hold_locked_limit_up=hold_locked_limit_up,
            locked_limit_up_pct=locked_limit_up_pct,
            fixed_notional_twd=fixed_notional_twd,
            commission_rate=commission_rate,
            commission_discount=commission_discount,
            sell_tax_rate=sell_tax_rate,
            slippage_bps_per_side=slippage_bps_per_side,
            minimum_commission_twd=minimum_commission_twd,
            taiex_regime_filter=taiex_regime_filter,
            market_regime=market_regime_value,
            sector_filter=sector_filter_value,
            require_foreign_not_selling_heavily=require_foreign_not_selling_heavily,
            require_investment_trust_buying=require_investment_trust_buying,
            avoid_margin_overcrowded=avoid_margin_overcrowded,
            prefer_short_balance_rising_before_limit_up=prefer_short_balance_rising_before_limit_up,
            foreign_heavy_sell_threshold=foreign_heavy_sell_threshold,
            margin_crowding_threshold=margin_crowding_threshold,
        )

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

    summary = summarize_intraday_trades(trades)
    report_dir = Path(output_dir) if output_dir is not None else default_report_dir()
    report_dir.mkdir(parents=True, exist_ok=True)
    trades.to_csv(report_dir / "intraday_opening_trades.csv", index=False)
    summary.to_csv(report_dir / "intraday_opening_summary.csv", index=False)
    return trades, summary


def load_event_candidates(
    conn,
    *,
    event_types: Sequence[str],
    markets: Sequence[str],
    start: str | pd.Timestamp | None,
    end: str | pd.Timestamp | None,
) -> pd.DataFrame:
    event_placeholders = ", ".join(["?"] * len(event_types))
    market_placeholders = ", ".join(["?"] * len(markets))
    filters = [
        f"event_type IN ({event_placeholders})",
        f"market IN ({market_placeholders})",
        "next_trade_date IS NOT NULL",
    ]
    params: list[Any] = [*event_types, *markets]
    if start is not None:
        filters.append("trade_date >= ?")
        params.append(pd.Timestamp(start).date())
    if end is not None:
        filters.append("trade_date <= ?")
        params.append(pd.Timestamp(end).date())

    return conn.execute(
        f"""
        SELECT {", ".join(EVENT_CANDIDATE_COLUMNS)}
        FROM event_candidates
        WHERE {" AND ".join(filters)}
        ORDER BY trade_date, market, symbol, event_type
        """,
        params,
    ).fetch_df()


def load_intraday_for_events(conn, events: pd.DataFrame) -> pd.DataFrame:
    if events.empty:
        return empty_intraday_frame()

    symbols = sorted(events["symbol"].dropna().astype(str).unique().tolist())
    markets = sorted(events["market"].dropna().astype(str).str.upper().unique().tolist())
    start_date = (pd.Timestamp(events["next_trade_date"].min()) - pd.Timedelta(days=60)).date()
    end_date = pd.Timestamp(events["next_trade_date"].max()).date()
    symbol_placeholders = ", ".join(["?"] * len(symbols))
    market_placeholders = ", ".join(["?"] * len(markets))

    return conn.execute(
        f"""
        SELECT
            symbol,
            trade_date,
            market,
            bar_time,
            open,
            high,
            low,
            close,
            volume_shares,
            turnover_twd,
            source
        FROM intraday_bars
        WHERE symbol IN ({symbol_placeholders})
          AND market IN ({market_placeholders})
          AND trade_date >= ?
          AND trade_date <= ?
        ORDER BY market, symbol, trade_date, bar_time
        """,
        [*symbols, *markets, start_date, end_date],
    ).fetch_df()


def load_taiex_regime(conn) -> pd.DataFrame:
    placeholders = ", ".join(["?"] * len(TAIEX_SYMBOLS))
    return conn.execute(
        f"""
        SELECT trade_date, daily_return
        FROM daily_prices
        WHERE symbol IN ({placeholders})
        ORDER BY trade_date
        """,
        TAIEX_SYMBOLS,
    ).fetch_df()


def build_intraday_trades(
    events: pd.DataFrame,
    intraday_bars: pd.DataFrame,
    *,
    taiex_regime: pd.DataFrame | None = None,
    entry_window_minutes: int = 3,
    min_open_gap_pct: float = 0.005,
    max_open_gap_pct: float = 0.05,
    allowed_open_drawdown_pct: float = 0.01,
    min_open_volume_ratio: float = 1.0,
    take_profit_pct: float = 0.03,
    stop_loss_pct: float = 0.015,
    vwap_fail_exit: bool = True,
    time_exit: str = DEFAULT_TIME_EXIT,
    hold_locked_limit_up: bool = False,
    locked_limit_up_pct: float = 0.095,
    fixed_notional_twd: float = 100_000.0,
    commission_rate: float = 0.001425,
    commission_discount: float = 0.28,
    sell_tax_rate: float = 0.0015,
    slippage_bps_per_side: float = 5.0,
    minimum_commission_twd: float = 20.0,
    taiex_regime_filter: bool = False,
    market_regime: str = "none",
    sector_filter: str = "none",
    require_foreign_not_selling_heavily: bool = False,
    require_investment_trust_buying: bool = False,
    avoid_margin_overcrowded: bool = False,
    prefer_short_balance_rising_before_limit_up: bool = False,
    foreign_heavy_sell_threshold: float = -0.05,
    margin_crowding_threshold: float = 5.0,
) -> pd.DataFrame:
    if events.empty or intraday_bars.empty:
        return empty_trade_report_frame()

    event_frame = normalize_events_for_strategy(events)
    bars = add_intraday_vwap(normalize_intraday_bars(intraday_bars))
    opening_volume = compute_normal_opening_volume(
        bars,
        entry_window_minutes=entry_window_minutes,
    )
    taiex_map = build_taiex_regime_map(taiex_regime) if taiex_regime_filter else {}

    grouped_bars = {
        key: group.sort_values("bar_time").reset_index(drop=True)
        for key, group in bars.groupby(["market", "symbol", "trade_date"], dropna=False)
    }
    opening_volume_map = {
        (row["market"], row["symbol"], row["trade_date"]): row
        for row in opening_volume.to_dict("records")
    }

    rows: list[dict[str, Any]] = []
    for event in event_frame.to_dict("records"):
        if should_skip_for_taiex(event, taiex_map, taiex_regime_filter):
            continue

        key = (event["market"], event["symbol"], pd.Timestamp(event["next_trade_date"]).normalize())
        day_bars = grouped_bars.get(key)
        if day_bars is None or len(day_bars) < entry_window_minutes:
            continue

        normal_row = opening_volume_map.get(key, {})
        entry = evaluate_entry_signal(
            event=event,
            day_bars=day_bars,
            normal_opening_window_volume=normal_row.get("normal_opening_window_volume", np.nan),
            entry_window_minutes=entry_window_minutes,
            min_open_gap_pct=min_open_gap_pct,
            max_open_gap_pct=max_open_gap_pct,
            allowed_open_drawdown_pct=allowed_open_drawdown_pct,
            min_open_volume_ratio=min_open_volume_ratio,
        )
        if not entry["passed"]:
            continue

        shares = calculate_board_lot_shares(
            fixed_notional_twd=fixed_notional_twd,
            entry_price=float(entry["entry_price"]),
        )
        if shares < BOARD_LOT_SIZE:
            continue

        exit_plan = simulate_intraday_exit(
            day_bars=day_bars,
            entry_time=pd.Timestamp(entry["entry_time"]),
            entry_price=float(entry["entry_price"]),
            day0_close=float(event["day0_close"]),
            take_profit_pct=take_profit_pct,
            stop_loss_pct=stop_loss_pct,
            vwap_fail_exit=vwap_fail_exit,
            time_exit=time_exit,
            hold_locked_limit_up=hold_locked_limit_up,
            locked_limit_up_pct=locked_limit_up_pct,
        )
        costs = calculate_trade_costs(
            side="long",
            entry_price=float(entry["entry_price"]),
            exit_price=float(exit_plan["exit_price"]),
            shares=shares,
            commission_rate=commission_rate,
            commission_discount=commission_discount,
            sell_tax_rate=sell_tax_rate,
            slippage_bps_per_side=slippage_bps_per_side,
            minimum_commission_twd=minimum_commission_twd,
            is_day_trade=True,
        )
        trade_id = make_trade_id(
            event_id=str(event["event_id"]),
            entry_time=pd.Timestamp(entry["entry_time"]),
            entry_price=float(entry["entry_price"]),
            take_profit_pct=take_profit_pct,
            stop_loss_pct=stop_loss_pct,
            vwap_fail_exit=vwap_fail_exit,
            time_exit=time_exit,
        )
        holding_minutes = (
            pd.Timestamp(exit_plan["exit_time"]) - pd.Timestamp(entry["entry_time"])
        ).total_seconds() / 60.0
        metadata = {
            "event_id": event["event_id"],
            "event_type": event["event_type"],
            "entry_window_minutes": entry_window_minutes,
            "min_open_gap_pct": min_open_gap_pct,
            "max_open_gap_pct": max_open_gap_pct,
            "allowed_open_drawdown_pct": allowed_open_drawdown_pct,
            "min_open_volume_ratio": min_open_volume_ratio,
            "take_profit_pct": take_profit_pct,
            "stop_loss_pct": stop_loss_pct,
            "vwap_fail_exit": vwap_fail_exit,
            "time_exit": time_exit,
            "hold_locked_limit_up": hold_locked_limit_up,
            "locked_limit_up_pct": locked_limit_up_pct,
            "taiex_regime_filter": taiex_regime_filter,
            "market_regime": market_regime,
            "sector_filter": sector_filter,
            "require_foreign_not_selling_heavily": require_foreign_not_selling_heavily,
            "require_investment_trust_buying": require_investment_trust_buying,
            "avoid_margin_overcrowded": avoid_margin_overcrowded,
            "prefer_short_balance_rising_before_limit_up": prefer_short_balance_rising_before_limit_up,
            "foreign_heavy_sell_threshold": foreign_heavy_sell_threshold,
            "margin_crowding_threshold": margin_crowding_threshold,
            "day1_open": entry["day1_open"],
            "open_gap_pct": entry["open_gap_pct"],
            "entry_vwap": entry["entry_vwap"],
            "opening_window_volume": entry["opening_window_volume"],
            "normal_opening_window_volume": entry["normal_opening_window_volume"],
            "open_volume_ratio": entry["open_volume_ratio"],
        }
        rows.append(
            {
                "trade_id": trade_id,
                "strategy_name": STRATEGY_NAME,
                "symbol": event["symbol"],
                "market": event["market"],
                "signal_date": pd.Timestamp(event["trade_date"]).date(),
                "entry_date": pd.Timestamp(entry["entry_time"]).date(),
                "entry_time": pd.Timestamp(entry["entry_time"]),
                "exit_date": pd.Timestamp(exit_plan["exit_time"]).date(),
                "exit_time": pd.Timestamp(exit_plan["exit_time"]),
                "side": "long",
                "entry_price": float(entry["entry_price"]),
                "exit_price": float(exit_plan["exit_price"]),
                "shares": shares,
                "gross_pnl": costs["gross_pnl"],
                "fees": costs["buy_commission"] + costs["sell_commission"],
                "tax": costs["sell_tax"],
                "slippage": costs["slippage_cost"],
                "net_pnl": costs["net_pnl"],
                "gross_return": costs["gross_return"],
                "net_return": costs["net_return"],
                "holding_minutes": holding_minutes,
                "exit_reason": exit_plan["exit_reason"],
                "metadata_json": json.dumps(metadata, ensure_ascii=False, default=_json_default),
                "event_id": event["event_id"],
                "event_type": event["event_type"],
                "day0_return": event["day0_return"],
                "day0_close": event["day0_close"],
                "day0_turnover_twd": event["day0_turnover_twd"],
                "volume_ratio_20d": event["volume_ratio_20d"],
                "close_location": event["close_location"],
                "next_trade_date": event["next_trade_date"],
                "day1_open": entry["day1_open"],
                "open_gap_pct": entry["open_gap_pct"],
                "entry_vwap": entry["entry_vwap"],
                "entry_window_minutes": entry_window_minutes,
                "opening_window_low": entry["opening_window_low"],
                "open_drawdown_pct": entry["open_drawdown_pct"],
                "opening_window_volume": entry["opening_window_volume"],
                "normal_opening_window_volume": entry["normal_opening_window_volume"],
                "open_volume_ratio": entry["open_volume_ratio"],
                "take_profit_price": exit_plan["take_profit_price"],
                "stop_loss_price": exit_plan["stop_loss_price"],
                "time_exit": time_exit,
                "vwap_fail_exit": vwap_fail_exit,
                "hold_locked_limit_up": hold_locked_limit_up,
            }
        )

    if not rows:
        return empty_trade_report_frame()
    return pd.DataFrame(rows, columns=TRADE_REPORT_COLUMNS)


def normalize_events_for_strategy(events: pd.DataFrame) -> pd.DataFrame:
    frame = events.copy()
    frame["symbol"] = frame["symbol"].astype("string").str.strip()
    frame["market"] = frame["market"].astype("string").str.upper().str.strip()
    for column in ["trade_date", "next_trade_date"]:
        frame[column] = pd.to_datetime(frame[column]).dt.normalize()
    numeric_columns = ["day0_close", "day0_return", "day0_turnover_twd", "volume_ratio_20d", "close_location"]
    for column in numeric_columns:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.dropna(subset=["symbol", "market", "trade_date", "next_trade_date", "day0_close"])


def normalize_intraday_bars(intraday_bars: pd.DataFrame) -> pd.DataFrame:
    frame = intraday_bars.copy()
    frame["symbol"] = frame["symbol"].astype("string").str.strip()
    frame["market"] = frame["market"].astype("string").str.upper().str.strip()
    frame["bar_time"] = pd.to_datetime(frame["bar_time"])
    frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.normalize()
    for column in ["open", "high", "low", "close", "volume_shares", "turnover_twd"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["symbol", "market", "bar_time", "trade_date", "open", "high", "low", "close"])
    return frame.sort_values(["market", "symbol", "trade_date", "bar_time"]).reset_index(drop=True)


def add_intraday_vwap(intraday_bars: pd.DataFrame) -> pd.DataFrame:
    """Add cumulative Day1 VWAP using turnover when available."""

    frame = intraday_bars.copy()
    frame["volume_shares"] = frame["volume_shares"].fillna(0.0)
    turnover = frame["turnover_twd"].where(frame["turnover_twd"].fillna(0.0) > 0)
    fallback_turnover = frame["close"] * frame["volume_shares"]
    frame["_vwap_turnover"] = turnover.fillna(fallback_turnover).fillna(0.0)
    group_keys = ["market", "symbol", "trade_date"]
    frame["_cum_volume"] = frame.groupby(group_keys)["volume_shares"].cumsum()
    frame["_cum_turnover"] = frame.groupby(group_keys)["_vwap_turnover"].cumsum()
    frame["vwap"] = np.where(frame["_cum_volume"] > 0, frame["_cum_turnover"] / frame["_cum_volume"], np.nan)
    frame["vwap"] = frame.groupby(group_keys)["vwap"].ffill()
    frame["vwap"] = frame["vwap"].fillna(frame["close"])
    return frame.drop(columns=["_vwap_turnover", "_cum_volume", "_cum_turnover"])


def compute_normal_opening_volume(
    intraday_bars: pd.DataFrame,
    *,
    entry_window_minutes: int,
    lookback_days: int = 20,
) -> pd.DataFrame:
    first_window = (
        intraday_bars.sort_values(["market", "symbol", "trade_date", "bar_time"])
        .groupby(["market", "symbol", "trade_date"], dropna=False)
        .head(entry_window_minutes)
    )
    daily = (
        first_window.groupby(["market", "symbol", "trade_date"], dropna=False)["volume_shares"]
        .sum()
        .reset_index(name="opening_window_volume")
        .sort_values(["market", "symbol", "trade_date"])
    )
    daily["normal_opening_window_volume"] = daily.groupby(["market", "symbol"])[
        "opening_window_volume"
    ].transform(lambda values: values.shift(1).rolling(window=lookback_days, min_periods=1).mean())
    return daily


def evaluate_entry_signal(
    *,
    event: dict[str, Any],
    day_bars: pd.DataFrame,
    normal_opening_window_volume: float | None,
    entry_window_minutes: int,
    min_open_gap_pct: float,
    max_open_gap_pct: float,
    allowed_open_drawdown_pct: float,
    min_open_volume_ratio: float,
) -> dict[str, Any]:
    opening = day_bars.head(entry_window_minutes)
    entry_bar = opening.iloc[-1]
    day1_open = float(opening.iloc[0]["open"])
    day0_close = float(event["day0_close"])
    entry_price = float(entry_bar["close"])
    entry_vwap = float(entry_bar["vwap"])
    opening_low = float(opening["low"].min())
    opening_volume = float(opening["volume_shares"].fillna(0.0).sum())
    open_gap_pct = day1_open / day0_close - 1.0 if day0_close > 0 else np.nan
    open_drawdown_pct = opening_low / day1_open - 1.0 if day1_open > 0 else np.nan
    normal_volume = float(normal_opening_window_volume) if pd.notna(normal_opening_window_volume) else np.nan
    open_volume_ratio = opening_volume / normal_volume if normal_volume > 0 else np.nan

    passed = (
        pd.notna(open_gap_pct)
        and min_open_gap_pct <= open_gap_pct <= max_open_gap_pct
        and entry_price > entry_vwap
        and pd.notna(open_drawdown_pct)
        and open_drawdown_pct >= -allowed_open_drawdown_pct
        and (pd.isna(open_volume_ratio) or open_volume_ratio >= min_open_volume_ratio)
    )
    return {
        "passed": bool(passed),
        "entry_time": entry_bar["bar_time"],
        "entry_price": entry_price,
        "day1_open": day1_open,
        "open_gap_pct": open_gap_pct,
        "entry_vwap": entry_vwap,
        "opening_window_low": opening_low,
        "open_drawdown_pct": open_drawdown_pct,
        "opening_window_volume": opening_volume,
        "normal_opening_window_volume": normal_volume,
        "open_volume_ratio": open_volume_ratio,
    }


def simulate_intraday_exit(
    *,
    day_bars: pd.DataFrame,
    entry_time: pd.Timestamp,
    entry_price: float,
    day0_close: float,
    take_profit_pct: float,
    stop_loss_pct: float,
    vwap_fail_exit: bool,
    time_exit: str,
    hold_locked_limit_up: bool,
    locked_limit_up_pct: float,
) -> dict[str, Any]:
    take_profit_price = entry_price * (1.0 + take_profit_pct)
    stop_loss_price = entry_price * (1.0 - stop_loss_pct)
    locked_limit_up_price = day0_close * (1.0 + locked_limit_up_pct)
    exit_time_value = parse_time_exit(time_exit)
    post_entry = day_bars[day_bars["bar_time"] > entry_time].copy()
    if post_entry.empty:
        return {
            "exit_time": entry_time,
            "exit_price": entry_price,
            "exit_reason": "close_exit",
            "take_profit_price": take_profit_price,
            "stop_loss_price": stop_loss_price,
        }

    last_bar = post_entry.iloc[-1]
    for _, bar in post_entry.iterrows():
        bar_time = pd.Timestamp(bar["bar_time"])
        if hold_locked_limit_up and bar["low"] >= locked_limit_up_price and bar["close"] >= locked_limit_up_price:
            return {
                "exit_time": pd.Timestamp(last_bar["bar_time"]),
                "exit_price": float(last_bar["close"]),
                "exit_reason": "locked_limit_up_close",
                "take_profit_price": take_profit_price,
                "stop_loss_price": stop_loss_price,
            }
        if float(bar["low"]) <= stop_loss_price:
            return {
                "exit_time": bar_time,
                "exit_price": stop_loss_price,
                "exit_reason": "stop_loss",
                "take_profit_price": take_profit_price,
                "stop_loss_price": stop_loss_price,
            }
        if float(bar["high"]) >= take_profit_price:
            return {
                "exit_time": bar_time,
                "exit_price": take_profit_price,
                "exit_reason": "take_profit",
                "take_profit_price": take_profit_price,
                "stop_loss_price": stop_loss_price,
            }
        if vwap_fail_exit and float(bar["close"]) < float(bar["vwap"]):
            return {
                "exit_time": bar_time,
                "exit_price": float(bar["close"]),
                "exit_reason": "vwap_fail",
                "take_profit_price": take_profit_price,
                "stop_loss_price": stop_loss_price,
            }
        if bar_time.time() >= exit_time_value:
            return {
                "exit_time": bar_time,
                "exit_price": float(bar["close"]),
                "exit_reason": "time_exit",
                "take_profit_price": take_profit_price,
                "stop_loss_price": stop_loss_price,
            }

    return {
        "exit_time": pd.Timestamp(last_bar["bar_time"]),
        "exit_price": float(last_bar["close"]),
        "exit_reason": "close_exit",
        "take_profit_price": take_profit_price,
        "stop_loss_price": stop_loss_price,
    }


def summarize_intraday_trades(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return empty_summary_frame()

    frame = trades.copy()
    frame["entry_date"] = pd.to_datetime(frame["entry_date"])
    frame["year"] = frame["entry_date"].dt.year
    specs = [
        ("overall", []),
        ("by_event_type", ["event_type"]),
        ("by_market", ["market"]),
        ("by_year", ["year"]),
    ]
    parts = [summarize_trade_group(frame, summary_level=level, group_columns=columns) for level, columns in specs]
    return pd.concat(parts, ignore_index=True)[SUMMARY_COLUMNS]


def summarize_trade_group(trades: pd.DataFrame, *, summary_level: str, group_columns: list[str]) -> pd.DataFrame:
    grouped = trades.groupby(group_columns, dropna=False) if group_columns else [((), trades)]
    rows: list[dict[str, Any]] = []
    for group_key, group in grouped:
        key_values = group_key if isinstance(group_key, tuple) else (group_key,)
        net_pnl = pd.to_numeric(group["net_pnl"], errors="coerce").fillna(0.0)
        losses = abs(net_pnl[net_pnl < 0].sum())
        gains = net_pnl[net_pnl > 0].sum()
        profit_factor = float("inf") if losses == 0 and gains > 0 else (float(gains / losses) if losses else 0.0)
        row: dict[str, Any] = {
            "summary_level": summary_level,
            "event_type": None,
            "market": None,
            "year": None,
            "number_of_trades": int(len(group)),
            "win_rate": float((net_pnl > 0).mean()) if len(group) else 0.0,
            "average_gross_return": _mean(group["gross_return"]),
            "average_net_return": _mean(group["net_return"]),
            "median_net_return": _median(group["net_return"]),
            "total_net_pnl": float(net_pnl.sum()),
            "profit_factor": profit_factor,
            "max_drawdown": max_drawdown_from_pnl(net_pnl),
            "average_holding_minutes": _mean(group["holding_minutes"]),
            "average_open_gap_pct": _mean(group["open_gap_pct"]),
            "average_open_volume_ratio": _mean(group["open_volume_ratio"]),
        }
        for column, value in zip(group_columns, key_values):
            row[column] = value
        rows.append(row)
    return pd.DataFrame(rows, columns=SUMMARY_COLUMNS)


def delete_strategy_trades(
    conn,
    *,
    strategy_name: str,
    start: str | pd.Timestamp | None,
    end: str | pd.Timestamp | None,
    markets: Sequence[str],
) -> None:
    placeholders = ", ".join(["?"] * len(markets))
    filters = ["strategy_name = ?", f"market IN ({placeholders})"]
    params: list[Any] = [strategy_name, *markets]
    if start is not None:
        filters.append("signal_date >= ?")
        params.append(pd.Timestamp(start).date())
    if end is not None:
        filters.append("signal_date <= ?")
        params.append(pd.Timestamp(end).date())
    conn.execute(f"DELETE FROM backtest_trades WHERE {' AND '.join(filters)}", params)


def calculate_board_lot_shares(*, fixed_notional_twd: float, entry_price: float) -> int:
    if entry_price <= 0:
        return 0
    raw_shares = int(fixed_notional_twd // entry_price)
    return (raw_shares // BOARD_LOT_SIZE) * BOARD_LOT_SIZE


def make_trade_id(
    *,
    event_id: str,
    entry_time: pd.Timestamp,
    entry_price: float,
    take_profit_pct: float,
    stop_loss_pct: float,
    vwap_fail_exit: bool,
    time_exit: str,
) -> str:
    raw = (
        f"{STRATEGY_NAME}|{event_id}|{entry_time.isoformat()}|{entry_price:.4f}|"
        f"{take_profit_pct:.6f}|{stop_loss_pct:.6f}|{vwap_fail_exit}|{time_exit}"
    )
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
    return f"{STRATEGY_NAME}:{digest}"


def should_skip_for_taiex(event: dict[str, Any], taiex_map: dict[Any, float], enabled: bool) -> bool:
    if not enabled or not taiex_map:
        return False
    value = taiex_map.get(pd.Timestamp(event["trade_date"]).date())
    return bool(pd.notna(value) and value <= 0)


def build_taiex_regime_map(taiex_regime: pd.DataFrame | None) -> dict[Any, float]:
    if taiex_regime is None or taiex_regime.empty:
        return {}
    frame = taiex_regime.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.date
    frame["daily_return"] = pd.to_numeric(frame["daily_return"], errors="coerce")
    return dict(zip(frame["trade_date"], frame["daily_return"]))


def normalize_event_types(event_types: Sequence[str] | None) -> list[str]:
    values = list(event_types or DEFAULT_EVENT_TYPES)
    invalid = sorted(set(values) - set(EVENT_TYPES))
    if invalid:
        raise ValueError(f"Unsupported event type(s): {invalid}. Valid values are {EVENT_TYPES}")
    return values


def validate_strategy_inputs(
    *,
    event_types: Sequence[str],
    entry_window_minutes: int,
    min_open_gap_pct: float,
    max_open_gap_pct: float,
    allowed_open_drawdown_pct: float,
    min_open_volume_ratio: float,
    take_profit_pct: float,
    stop_loss_pct: float,
    fixed_notional_twd: float,
    locked_limit_up_pct: float,
    time_exit: str,
    market_regime: str,
    sector_filter: str,
    margin_crowding_threshold: float,
) -> None:
    normalize_event_types(event_types)
    normalize_market_regime_filter(market_regime)
    normalize_sector_filter(sector_filter)
    if entry_window_minutes <= 0:
        raise ValueError("entry_window_minutes must be positive")
    if max_open_gap_pct < min_open_gap_pct:
        raise ValueError("max_open_gap_pct must be greater than or equal to min_open_gap_pct")
    for name, value in {
        "allowed_open_drawdown_pct": allowed_open_drawdown_pct,
        "min_open_volume_ratio": min_open_volume_ratio,
        "take_profit_pct": take_profit_pct,
        "stop_loss_pct": stop_loss_pct,
        "fixed_notional_twd": fixed_notional_twd,
        "locked_limit_up_pct": locked_limit_up_pct,
    }.items():
        if value < 0:
            raise ValueError(f"{name} must be non-negative")
    if take_profit_pct == 0:
        raise ValueError("take_profit_pct must be positive")
    if stop_loss_pct == 0:
        raise ValueError("stop_loss_pct must be positive")
    if margin_crowding_threshold < 0:
        raise ValueError("margin_crowding_threshold must be non-negative")
    parse_time_exit(time_exit)


def parse_time_exit(value: str):
    parsed = pd.to_datetime(value, format="%H:%M", errors="coerce")
    if pd.isna(parsed):
        raise ValueError(f"time_exit must use HH:MM format, got {value!r}")
    return parsed.time()


def parse_bool(value: str | bool) -> bool:
    if isinstance(value, bool):
        return value
    normalized = value.lower().strip()
    if normalized in {"true", "1", "yes", "y"}:
        return True
    if normalized in {"false", "0", "no", "n"}:
        return False
    raise argparse.ArgumentTypeError(f"Expected true/false, got {value!r}")


def empty_intraday_frame() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "symbol",
            "trade_date",
            "market",
            "bar_time",
            "open",
            "high",
            "low",
            "close",
            "volume_shares",
            "turnover_twd",
            "source",
        ]
    )


def empty_trade_report_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=TRADE_REPORT_COLUMNS)


def empty_summary_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=SUMMARY_COLUMNS)


def max_drawdown_from_pnl(pnl: pd.Series) -> float:
    values = pd.to_numeric(pnl, errors="coerce").fillna(0.0)
    if values.empty:
        return 0.0
    equity = pd.concat([pd.Series([0.0]), values], ignore_index=True).cumsum()
    return float((equity - equity.cummax()).min())


def _mean(values: pd.Series) -> float:
    clean = pd.to_numeric(values, errors="coerce").dropna()
    return float(clean.mean()) if not clean.empty else 0.0


def _median(values: pd.Series) -> float:
    clean = pd.to_numeric(values, errors="coerce").dropna()
    return float(clean.median()) if not clean.empty else 0.0


def _json_default(value: Any) -> Any:
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, np.generic):
        return value.item()
    if pd.isna(value):
        return None
    return value


def default_report_dir() -> Path:
    return get_settings().project_root / "reports"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the intraday Day1 opening-continuation strategy")
    parser.add_argument("--db", type=Path, default=get_settings().db_path, help="DuckDB path")
    parser.add_argument("--start", help="Optional Day0 start date, YYYY-MM-DD")
    parser.add_argument("--end", help="Optional Day0 end date, YYYY-MM-DD")
    parser.add_argument("--market", choices=["TWSE", "TPEX", "BOTH"], default="BOTH")
    parser.add_argument("--event-types", nargs="+", default=DEFAULT_EVENT_TYPES, choices=EVENT_TYPES)
    parser.add_argument("--entry-window-minutes", type=int, default=3)
    parser.add_argument("--min-open-gap-pct", type=float, default=0.005)
    parser.add_argument("--max-open-gap-pct", type=float, default=0.05)
    parser.add_argument("--allowed-open-drawdown-pct", type=float, default=0.01)
    parser.add_argument("--min-open-volume-ratio", type=float, default=1.0)
    parser.add_argument("--take-profit-pct", type=float, default=0.03)
    parser.add_argument("--stop-loss-pct", type=float, default=0.015)
    parser.add_argument("--vwap-fail-exit", type=parse_bool, default=True)
    parser.add_argument("--time-exit", default=DEFAULT_TIME_EXIT)
    parser.add_argument("--hold-locked-limit-up", type=parse_bool, default=False)
    parser.add_argument("--locked-limit-up-pct", type=float, default=0.095)
    parser.add_argument("--fixed-notional-twd", type=float, default=100_000.0)
    parser.add_argument("--commission-rate", type=float, default=0.001425)
    parser.add_argument("--commission-discount", type=float, default=0.28)
    parser.add_argument("--sell-tax-rate", type=float, default=0.0015)
    parser.add_argument("--slippage-bps-per-side", type=float, default=5.0)
    parser.add_argument("--minimum-commission-twd", type=float, default=20.0)
    parser.add_argument("--taiex-regime-filter", type=parse_bool, default=False)
    parser.add_argument("--market-regime", choices=MARKET_REGIME_FILTERS, default="none")
    parser.add_argument("--sector-filter", choices=SECTOR_FILTERS, default="none")
    parser.add_argument("--require-foreign-not-selling-heavily", type=parse_bool, default=False)
    parser.add_argument("--require-investment-trust-buying", type=parse_bool, default=False)
    parser.add_argument("--avoid-margin-overcrowded", type=parse_bool, default=False)
    parser.add_argument("--prefer-short-balance-rising-before-limit-up", type=parse_bool, default=False)
    parser.add_argument("--foreign-heavy-sell-threshold", type=float, default=-0.05)
    parser.add_argument("--margin-crowding-threshold", type=float, default=5.0)
    parser.add_argument("--output-dir", type=Path, default=default_report_dir())
    args = parser.parse_args()

    trades, summary = run_intraday_opening_continuation(
        db_path=args.db,
        event_types=args.event_types,
        start=args.start,
        end=args.end,
        markets=normalize_markets([args.market]),
        entry_window_minutes=args.entry_window_minutes,
        min_open_gap_pct=args.min_open_gap_pct,
        max_open_gap_pct=args.max_open_gap_pct,
        allowed_open_drawdown_pct=args.allowed_open_drawdown_pct,
        min_open_volume_ratio=args.min_open_volume_ratio,
        take_profit_pct=args.take_profit_pct,
        stop_loss_pct=args.stop_loss_pct,
        vwap_fail_exit=args.vwap_fail_exit,
        time_exit=args.time_exit,
        hold_locked_limit_up=args.hold_locked_limit_up,
        locked_limit_up_pct=args.locked_limit_up_pct,
        fixed_notional_twd=args.fixed_notional_twd,
        commission_rate=args.commission_rate,
        commission_discount=args.commission_discount,
        sell_tax_rate=args.sell_tax_rate,
        slippage_bps_per_side=args.slippage_bps_per_side,
        minimum_commission_twd=args.minimum_commission_twd,
        taiex_regime_filter=args.taiex_regime_filter,
        market_regime=args.market_regime,
        sector_filter=args.sector_filter,
        require_foreign_not_selling_heavily=args.require_foreign_not_selling_heavily,
        require_investment_trust_buying=args.require_investment_trust_buying,
        avoid_margin_overcrowded=args.avoid_margin_overcrowded,
        prefer_short_balance_rising_before_limit_up=args.prefer_short_balance_rising_before_limit_up,
        foreign_heavy_sell_threshold=args.foreign_heavy_sell_threshold,
        margin_crowding_threshold=args.margin_crowding_threshold,
        output_dir=args.output_dir,
    )

    trades_path = args.output_dir / "intraday_opening_trades.csv"
    summary_path = args.output_dir / "intraday_opening_summary.csv"
    print(f"Wrote {len(trades)} trades to {trades_path}")
    print(f"Wrote {len(summary)} summary rows to {summary_path}")
    if not summary.empty:
        print("\noverall")
        print(summary[summary["summary_level"] == "overall"].to_string(index=False))
        print("\nby event_type")
        print(summary[summary["summary_level"] == "by_event_type"].to_string(index=False))


if __name__ == "__main__":
    main()
