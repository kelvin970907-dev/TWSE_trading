"""Grid search for the daily limit-momentum strategy."""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from tqdm.auto import tqdm

from config.settings import get_settings
from src.backtests.event_study import (
    EVENT_CANDIDATE_COLUMNS,
    build_event_candidates,
    normalize_markets,
)
from src.backtests.metrics import sharpe_ratio
from src.backtests.strategy_limit_momentum import BOARD_LOT_SIZE, PATH_ASSUMPTIONS, load_day1_daily_prices
from src.db import get_connection, init_db
from src.features.flow_features import apply_flow_feature_filters


GRID_EVENT_TYPES = [
    "near_limit_8_9",
    "near_limit_9_10",
    "closed_limit_up",
    "touched_limit_not_closed",
]
GRID_MIN_TURNOVER_TWD = [50_000_000.0, 100_000_000.0, 200_000_000.0, 500_000_000.0]
GRID_MIN_VOLUME_RATIO_20D = [1.5, 2.0, 3.0, 5.0]
GRID_MIN_CLOSE_LOCATION = [0.7, 0.8, 0.85, 0.9]
GRID_TAKE_PROFIT_PCT = [0.015, 0.02, 0.03, 0.04]
GRID_STOP_LOSS_PCT = [0.01, 0.015, 0.02, 0.03]
GRID_PATH_ASSUMPTIONS = ["pessimistic", "close_only"]
GRID_MARKETS = ["TWSE", "TPEX", "BOTH"]

DEFAULT_TRAIN_START = "2023-01-01"
DEFAULT_TRAIN_END = "2024-12-31"
DEFAULT_TEST_START = "2025-01-01"
DEFAULT_TEST_END = "2026-06-22"
DEFAULT_FIXED_NOTIONAL_TWD = 100_000.0
DEFAULT_CAPACITY_PARTICIPATION_RATE = 0.05

METRIC_COLUMNS = [
    "trades",
    "net_pnl",
    "avg_net_return",
    "median_net_return",
    "win_rate",
    "profit_factor",
    "max_drawdown",
    "daily_sharpe",
    "avg_holding_minutes",
    "avg_liquidity_twd",
    "annualized_turnover_twd",
    "capacity_proxy_twd",
]

PARAMETER_COLUMNS = [
    "config_hash",
    "event_types",
    "event_type_count",
    "min_turnover_twd",
    "min_volume_ratio_20d",
    "min_close_location",
    "take_profit_pct",
    "stop_loss_pct",
    "path_assumption",
    "market",
    "require_foreign_not_selling_heavily",
    "require_investment_trust_buying",
    "avoid_margin_overcrowded",
    "prefer_short_balance_rising_before_limit_up",
    "foreign_heavy_sell_threshold",
    "margin_crowding_threshold",
    "fixed_notional_twd",
    "commission_rate",
    "commission_discount",
    "sell_tax_rate",
    "slippage_bps_per_side",
    "minimum_commission_twd",
    "capacity_participation_rate",
]

RESULT_COLUMNS = (
    PARAMETER_COLUMNS
    + ["train_start", "train_end", "test_start", "test_end", "train_rank_eligible", "train_rank_score"]
    + [f"train_{column}" for column in METRIC_COLUMNS]
    + [f"test_{column}" for column in METRIC_COLUMNS]
)


@dataclass(frozen=True)
class GridParameters:
    event_types: tuple[str, ...]
    min_turnover_twd: float
    min_volume_ratio_20d: float
    min_close_location: float
    take_profit_pct: float
    stop_loss_pct: float
    path_assumption: str
    market: str
    require_foreign_not_selling_heavily: bool = False
    require_investment_trust_buying: bool = False
    avoid_margin_overcrowded: bool = False
    prefer_short_balance_rising_before_limit_up: bool = False
    foreign_heavy_sell_threshold: float = -0.05
    margin_crowding_threshold: float = 5.0

    @property
    def event_types_label(self) -> str:
        return "+".join(self.event_types)

    def payload(self) -> dict[str, Any]:
        return {
            "event_types": list(self.event_types),
            "min_turnover_twd": self.min_turnover_twd,
            "min_volume_ratio_20d": self.min_volume_ratio_20d,
            "min_close_location": self.min_close_location,
            "take_profit_pct": self.take_profit_pct,
            "stop_loss_pct": self.stop_loss_pct,
            "path_assumption": self.path_assumption,
            "market": self.market,
            "require_foreign_not_selling_heavily": self.require_foreign_not_selling_heavily,
            "require_investment_trust_buying": self.require_investment_trust_buying,
            "avoid_margin_overcrowded": self.avoid_margin_overcrowded,
            "prefer_short_balance_rising_before_limit_up": self.prefer_short_balance_rising_before_limit_up,
            "foreign_heavy_sell_threshold": self.foreign_heavy_sell_threshold,
            "margin_crowding_threshold": self.margin_crowding_threshold,
        }


def run_grid_search(
    *,
    db_path: Path | str,
    output_dir: Path | str | None = None,
    train_start: str | pd.Timestamp = DEFAULT_TRAIN_START,
    train_end: str | pd.Timestamp = DEFAULT_TRAIN_END,
    test_start: str | pd.Timestamp = DEFAULT_TEST_START,
    test_end: str | pd.Timestamp = DEFAULT_TEST_END,
    fixed_notional_twd: float = DEFAULT_FIXED_NOTIONAL_TWD,
    commission_rate: float = 0.001425,
    commission_discount: float = 0.28,
    sell_tax_rate: float = 0.0015,
    slippage_bps_per_side: float = 5.0,
    minimum_commission_twd: float = 20.0,
    capacity_participation_rate: float = DEFAULT_CAPACITY_PARTICIPATION_RATE,
    min_train_trades_for_ranking: int = 20,
    top_n: int = 100,
    rebuild_events: bool = True,
    force: bool = False,
    show_progress: bool = True,
    max_runs: int | None = None,
    event_type_sets: Sequence[Sequence[str]] | None = None,
    min_turnover_values: Sequence[float] = GRID_MIN_TURNOVER_TWD,
    min_volume_ratio_values: Sequence[float] = GRID_MIN_VOLUME_RATIO_20D,
    min_close_location_values: Sequence[float] = GRID_MIN_CLOSE_LOCATION,
    take_profit_values: Sequence[float] = GRID_TAKE_PROFIT_PCT,
    stop_loss_values: Sequence[float] = GRID_STOP_LOSS_PCT,
    path_assumptions: Sequence[str] = GRID_PATH_ASSUMPTIONS,
    markets: Sequence[str] = GRID_MARKETS,
    require_foreign_not_selling_heavily_values: Sequence[bool] = (False,),
    require_investment_trust_buying_values: Sequence[bool] = (False,),
    avoid_margin_overcrowded_values: Sequence[bool] = (False,),
    prefer_short_balance_rising_before_limit_up_values: Sequence[bool] = (False,),
    foreign_heavy_sell_threshold: float = -0.05,
    margin_crowding_threshold: float = 5.0,
) -> pd.DataFrame:
    """Run a resumable train/test parameter grid search."""

    validate_grid_inputs(
        fixed_notional_twd=fixed_notional_twd,
        capacity_participation_rate=capacity_participation_rate,
        min_train_trades_for_ranking=min_train_trades_for_ranking,
        top_n=top_n,
        path_assumptions=path_assumptions,
        markets=markets,
        margin_crowding_threshold=margin_crowding_threshold,
    )
    output_path = Path(output_dir) if output_dir is not None else default_report_dir()
    output_path.mkdir(parents=True, exist_ok=True)
    results_path = output_path / "grid_search_results.csv"
    top_train_path = output_path / "grid_search_top_train.csv"
    top_test_path = output_path / "grid_search_top_test.csv"

    if force:
        write_empty_results(results_path)

    full_start = min(pd.Timestamp(train_start), pd.Timestamp(test_start))
    full_end = max(pd.Timestamp(train_end), pd.Timestamp(test_end))
    init_db(db_path)
    if rebuild_events:
        build_event_candidates(
            db_path=db_path,
            start=full_start,
            end=full_end,
            markets=["BOTH"],
        )

    with get_connection(db_path) as conn:
        events = load_grid_events(conn, start=full_start, end=full_end)
        day1_prices = load_day1_daily_prices(conn)

    universe = build_trade_universe(events, day1_prices, fixed_notional_twd=fixed_notional_twd)
    existing_hashes = load_existing_hashes(results_path)
    parameter_sets = list(
        iter_parameter_grid(
            event_type_sets=event_type_sets,
            min_turnover_values=min_turnover_values,
            min_volume_ratio_values=min_volume_ratio_values,
            min_close_location_values=min_close_location_values,
            take_profit_values=take_profit_values,
            stop_loss_values=stop_loss_values,
            path_assumptions=path_assumptions,
            markets=markets,
            require_foreign_not_selling_heavily_values=require_foreign_not_selling_heavily_values,
            require_investment_trust_buying_values=require_investment_trust_buying_values,
            avoid_margin_overcrowded_values=avoid_margin_overcrowded_values,
            prefer_short_balance_rising_before_limit_up_values=prefer_short_balance_rising_before_limit_up_values,
            foreign_heavy_sell_threshold=foreign_heavy_sell_threshold,
            margin_crowding_threshold=margin_crowding_threshold,
        )
    )

    written = 0
    skipped = 0
    progress = tqdm(parameter_sets, total=len(parameter_sets), disable=not show_progress, desc="grid search")
    for params in progress:
        if max_runs is not None and written >= max_runs:
            break

        config_hash = make_config_hash(
            params,
            fixed_notional_twd=fixed_notional_twd,
            commission_rate=commission_rate,
            commission_discount=commission_discount,
            sell_tax_rate=sell_tax_rate,
            slippage_bps_per_side=slippage_bps_per_side,
            minimum_commission_twd=minimum_commission_twd,
            capacity_participation_rate=capacity_participation_rate,
        )
        if config_hash in existing_hashes:
            skipped += 1
            continue

        row = evaluate_grid_parameters(
            params,
            universe=universe,
            config_hash=config_hash,
            train_start=train_start,
            train_end=train_end,
            test_start=test_start,
            test_end=test_end,
            fixed_notional_twd=fixed_notional_twd,
            commission_rate=commission_rate,
            commission_discount=commission_discount,
            sell_tax_rate=sell_tax_rate,
            slippage_bps_per_side=slippage_bps_per_side,
            minimum_commission_twd=minimum_commission_twd,
            capacity_participation_rate=capacity_participation_rate,
            min_train_trades_for_ranking=min_train_trades_for_ranking,
        )
        append_result_row(results_path, row)
        existing_hashes.add(config_hash)
        written += 1
        if show_progress:
            progress.set_postfix(written=written, skipped=skipped)

    results = read_results(results_path)
    write_top_reports(results, top_train_path=top_train_path, top_test_path=top_test_path, top_n=top_n)
    return results


def load_grid_events(conn, *, start: str | pd.Timestamp, end: str | pd.Timestamp) -> pd.DataFrame:
    placeholders = ", ".join(EVENT_CANDIDATE_COLUMNS)
    return conn.execute(
        f"""
        SELECT {placeholders}
        FROM event_candidates
        WHERE trade_date >= ?
          AND trade_date <= ?
        ORDER BY market, symbol, trade_date, event_type
        """,
        [pd.Timestamp(start).date(), pd.Timestamp(end).date()],
    ).fetch_df()


def build_trade_universe(
    events: pd.DataFrame,
    day1_prices: pd.DataFrame,
    *,
    fixed_notional_twd: float,
) -> pd.DataFrame:
    """Join events to Day 1 daily bars once for fast grid evaluation."""

    if events.empty:
        return empty_trade_universe()

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
    numeric_columns = [
        "day0_turnover_twd",
        "volume_ratio_20d",
        "close_location",
        "day1_open",
        "day1_high",
        "day1_low",
        "day1_close",
    ]
    for column in numeric_columns:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")

    frame = frame.dropna(subset=["day1_open", "day1_high", "day1_low", "day1_close"]).copy()
    frame = frame[frame["day1_open"] > 0].copy()
    if frame.empty:
        return empty_trade_universe()

    raw_shares = np.floor(fixed_notional_twd / frame["day1_open"]).astype("int64")
    frame["shares"] = (raw_shares // BOARD_LOT_SIZE) * BOARD_LOT_SIZE
    frame = frame[frame["shares"] >= BOARD_LOT_SIZE].copy()
    if frame.empty:
        return empty_trade_universe()

    frame["entry_date"] = frame["day1_trade_date"]
    frame["holding_minutes"] = 270.0
    return frame.reset_index(drop=True)


def evaluate_grid_parameters(
    params: GridParameters,
    *,
    universe: pd.DataFrame,
    config_hash: str,
    train_start: str | pd.Timestamp,
    train_end: str | pd.Timestamp,
    test_start: str | pd.Timestamp,
    test_end: str | pd.Timestamp,
    fixed_notional_twd: float,
    commission_rate: float,
    commission_discount: float,
    sell_tax_rate: float,
    slippage_bps_per_side: float,
    minimum_commission_twd: float,
    capacity_participation_rate: float,
    min_train_trades_for_ranking: int,
) -> dict[str, Any]:
    train_trades = filter_trade_universe(
        universe,
        params=params,
        start=train_start,
        end=train_end,
    )
    test_trades = filter_trade_universe(
        universe,
        params=params,
        start=test_start,
        end=test_end,
    )
    train_metrics = evaluate_trade_metrics(
        train_trades,
        params=params,
        start=train_start,
        end=train_end,
        commission_rate=commission_rate,
        commission_discount=commission_discount,
        sell_tax_rate=sell_tax_rate,
        slippage_bps_per_side=slippage_bps_per_side,
        minimum_commission_twd=minimum_commission_twd,
        capacity_participation_rate=capacity_participation_rate,
    )
    test_metrics = evaluate_trade_metrics(
        test_trades,
        params=params,
        start=test_start,
        end=test_end,
        commission_rate=commission_rate,
        commission_discount=commission_discount,
        sell_tax_rate=sell_tax_rate,
        slippage_bps_per_side=slippage_bps_per_side,
        minimum_commission_twd=minimum_commission_twd,
        capacity_participation_rate=capacity_participation_rate,
    )
    eligible = train_metrics["trades"] >= min_train_trades_for_ranking
    rank_score = train_metrics["net_pnl"] if eligible else np.nan

    row: dict[str, Any] = {
        "config_hash": config_hash,
        "event_types": params.event_types_label,
        "event_type_count": len(params.event_types),
        "min_turnover_twd": params.min_turnover_twd,
        "min_volume_ratio_20d": params.min_volume_ratio_20d,
        "min_close_location": params.min_close_location,
        "take_profit_pct": params.take_profit_pct,
        "stop_loss_pct": params.stop_loss_pct,
        "path_assumption": params.path_assumption,
        "market": params.market,
        "require_foreign_not_selling_heavily": params.require_foreign_not_selling_heavily,
        "require_investment_trust_buying": params.require_investment_trust_buying,
        "avoid_margin_overcrowded": params.avoid_margin_overcrowded,
        "prefer_short_balance_rising_before_limit_up": params.prefer_short_balance_rising_before_limit_up,
        "foreign_heavy_sell_threshold": params.foreign_heavy_sell_threshold,
        "margin_crowding_threshold": params.margin_crowding_threshold,
        "fixed_notional_twd": fixed_notional_twd,
        "commission_rate": commission_rate,
        "commission_discount": commission_discount,
        "sell_tax_rate": sell_tax_rate,
        "slippage_bps_per_side": slippage_bps_per_side,
        "minimum_commission_twd": minimum_commission_twd,
        "capacity_participation_rate": capacity_participation_rate,
        "train_start": pd.Timestamp(train_start).date().isoformat(),
        "train_end": pd.Timestamp(train_end).date().isoformat(),
        "test_start": pd.Timestamp(test_start).date().isoformat(),
        "test_end": pd.Timestamp(test_end).date().isoformat(),
        "train_rank_eligible": bool(eligible),
        "train_rank_score": rank_score,
    }
    row.update({f"train_{key}": value for key, value in train_metrics.items()})
    row.update({f"test_{key}": value for key, value in test_metrics.items()})
    return {column: row.get(column, np.nan) for column in RESULT_COLUMNS}


def filter_trade_universe(
    universe: pd.DataFrame,
    *,
    params: GridParameters,
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
) -> pd.DataFrame:
    if universe.empty:
        return universe

    market_values = normalize_markets([params.market])
    start_ts = pd.Timestamp(start).normalize()
    end_ts = pd.Timestamp(end).normalize()
    # Split performance by entry date so a Day 0 signal at the train boundary
    # cannot use a Day 1 test-window outcome as training performance.
    entry_dates = pd.to_datetime(universe["entry_date"], errors="coerce").dt.normalize()
    mask = (
        (entry_dates >= start_ts)
        & (entry_dates <= end_ts)
        & universe["event_type"].isin(params.event_types)
        & universe["market"].isin(market_values)
        & (universe["day0_turnover_twd"] >= params.min_turnover_twd)
        & (universe["volume_ratio_20d"] >= params.min_volume_ratio_20d)
        & (universe["close_location"] >= params.min_close_location)
    )
    selected = universe[mask].copy()
    return apply_flow_feature_filters(
        selected,
        require_foreign_not_selling_heavily=params.require_foreign_not_selling_heavily,
        require_investment_trust_buying=params.require_investment_trust_buying,
        avoid_margin_overcrowded=params.avoid_margin_overcrowded,
        prefer_short_balance_rising_before_limit_up=params.prefer_short_balance_rising_before_limit_up,
        foreign_heavy_sell_threshold=params.foreign_heavy_sell_threshold,
        margin_crowding_threshold=params.margin_crowding_threshold,
    )


def evaluate_trade_metrics(
    trades: pd.DataFrame,
    *,
    params: GridParameters,
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
    commission_rate: float,
    commission_discount: float,
    sell_tax_rate: float,
    slippage_bps_per_side: float,
    minimum_commission_twd: float,
    capacity_participation_rate: float,
) -> dict[str, float]:
    if trades.empty:
        return empty_metrics()

    priced = apply_grid_exits(trades, params=params)
    priced = apply_vectorized_costs(
        priced,
        commission_rate=commission_rate,
        commission_discount=commission_discount,
        sell_tax_rate=sell_tax_rate,
        slippage_bps_per_side=slippage_bps_per_side,
        minimum_commission_twd=minimum_commission_twd,
    )
    net_pnl = pd.to_numeric(priced["net_pnl"], errors="coerce").fillna(0.0)
    net_returns = pd.to_numeric(priced["net_return"], errors="coerce").dropna()
    daily_returns = daily_strategy_returns(priced)
    losses = abs(net_pnl[net_pnl < 0].sum())
    gains = net_pnl[net_pnl > 0].sum()
    profit_factor = float("inf") if losses == 0 and gains > 0 else (float(gains / losses) if losses else 0.0)
    avg_liquidity = pd.to_numeric(priced["day0_turnover_twd"], errors="coerce").mean()
    capacity_proxy = pd.to_numeric(priced["day0_turnover_twd"], errors="coerce").median() * capacity_participation_rate

    return {
        "trades": int(len(priced)),
        "net_pnl": float(net_pnl.sum()),
        "avg_net_return": float(net_returns.mean()) if not net_returns.empty else 0.0,
        "median_net_return": float(net_returns.median()) if not net_returns.empty else 0.0,
        "win_rate": float((net_pnl > 0).mean()) if len(priced) else 0.0,
        "profit_factor": profit_factor,
        "max_drawdown": max_drawdown_from_pnl(net_pnl),
        "daily_sharpe": sharpe_ratio(daily_returns),
        "avg_holding_minutes": float(pd.to_numeric(priced["holding_minutes"], errors="coerce").mean()),
        "avg_liquidity_twd": float(avg_liquidity) if not pd.isna(avg_liquidity) else 0.0,
        "annualized_turnover_twd": annualized_turnover(priced, start=start, end=end),
        "capacity_proxy_twd": float(capacity_proxy) if not pd.isna(capacity_proxy) else 0.0,
    }


def apply_grid_exits(trades: pd.DataFrame, *, params: GridParameters) -> pd.DataFrame:
    frame = trades.copy()
    entry = frame["day1_open"].astype("float64")
    take_profit_price = entry * (1.0 + params.take_profit_pct)
    stop_loss_price = entry * (1.0 - params.stop_loss_pct)

    if params.path_assumption == "close_only":
        frame["exit_price"] = frame["day1_close"].astype("float64")
    elif params.path_assumption == "pessimistic":
        stop_hit = frame["day1_low"] <= stop_loss_price
        take_profit_hit = frame["day1_high"] >= take_profit_price
        frame["exit_price"] = np.select(
            [stop_hit, take_profit_hit],
            [stop_loss_price, take_profit_price],
            default=frame["day1_close"],
        ).astype("float64")
    elif params.path_assumption == "optimistic":
        take_profit_hit = frame["day1_high"] >= take_profit_price
        stop_hit = frame["day1_low"] <= stop_loss_price
        frame["exit_price"] = np.select(
            [take_profit_hit, stop_hit],
            [take_profit_price, stop_loss_price],
            default=frame["day1_close"],
        ).astype("float64")
    else:
        raise ValueError(f"Unsupported path_assumption: {params.path_assumption}")

    frame["entry_price"] = entry
    return frame


def apply_vectorized_costs(
    trades: pd.DataFrame,
    *,
    commission_rate: float,
    commission_discount: float,
    sell_tax_rate: float,
    slippage_bps_per_side: float,
    minimum_commission_twd: float,
) -> pd.DataFrame:
    frame = trades.copy()
    shares = frame["shares"].astype("float64")
    buy_notional = frame["entry_price"].astype("float64") * shares
    sell_notional = frame["exit_price"].astype("float64") * shares
    effective_commission_rate = commission_rate * commission_discount

    frame["buy_notional"] = buy_notional
    frame["sell_notional"] = sell_notional
    frame["buy_commission"] = np.maximum(buy_notional * effective_commission_rate, minimum_commission_twd)
    frame["sell_commission"] = np.maximum(sell_notional * effective_commission_rate, minimum_commission_twd)
    frame["sell_tax"] = sell_notional * sell_tax_rate
    frame["slippage_cost"] = (buy_notional + sell_notional) * slippage_bps_per_side / 10_000.0
    frame["gross_pnl"] = sell_notional - buy_notional
    frame["total_cost"] = (
        frame["buy_commission"] + frame["sell_commission"] + frame["sell_tax"] + frame["slippage_cost"]
    )
    frame["net_pnl"] = frame["gross_pnl"] - frame["total_cost"]
    frame["net_return"] = np.where(buy_notional > 0, frame["net_pnl"] / buy_notional, np.nan)
    return frame


def daily_strategy_returns(trades: pd.DataFrame) -> pd.Series:
    daily = trades.groupby("entry_date", dropna=False).agg(
        net_pnl=("net_pnl", "sum"),
        buy_notional=("buy_notional", "sum"),
    )
    return (daily["net_pnl"] / daily["buy_notional"].replace(0, np.nan)).dropna()


def max_drawdown_from_pnl(pnl: pd.Series) -> float:
    values = pd.to_numeric(pnl, errors="coerce").fillna(0.0)
    if values.empty:
        return 0.0
    equity = pd.concat([pd.Series([0.0]), values], ignore_index=True).cumsum()
    drawdown = equity - equity.cummax()
    return float(drawdown.min())


def annualized_turnover(trades: pd.DataFrame, *, start: str | pd.Timestamp, end: str | pd.Timestamp) -> float:
    years = max((pd.Timestamp(end).date() - pd.Timestamp(start).date()).days + 1, 1) / 365.25
    turnover = pd.to_numeric(trades["buy_notional"] + trades["sell_notional"], errors="coerce").fillna(0.0).sum()
    return float(turnover / years)


def empty_metrics() -> dict[str, float]:
    return {
        "trades": 0,
        "net_pnl": 0.0,
        "avg_net_return": 0.0,
        "median_net_return": 0.0,
        "win_rate": 0.0,
        "profit_factor": 0.0,
        "max_drawdown": 0.0,
        "daily_sharpe": 0.0,
        "avg_holding_minutes": 0.0,
        "avg_liquidity_twd": 0.0,
        "annualized_turnover_twd": 0.0,
        "capacity_proxy_twd": 0.0,
    }


def iter_parameter_grid(
    *,
    event_type_sets: Sequence[Sequence[str]] | None = None,
    min_turnover_values: Sequence[float] = GRID_MIN_TURNOVER_TWD,
    min_volume_ratio_values: Sequence[float] = GRID_MIN_VOLUME_RATIO_20D,
    min_close_location_values: Sequence[float] = GRID_MIN_CLOSE_LOCATION,
    take_profit_values: Sequence[float] = GRID_TAKE_PROFIT_PCT,
    stop_loss_values: Sequence[float] = GRID_STOP_LOSS_PCT,
    path_assumptions: Sequence[str] = GRID_PATH_ASSUMPTIONS,
    markets: Sequence[str] = GRID_MARKETS,
    require_foreign_not_selling_heavily_values: Sequence[bool] = (False,),
    require_investment_trust_buying_values: Sequence[bool] = (False,),
    avoid_margin_overcrowded_values: Sequence[bool] = (False,),
    prefer_short_balance_rising_before_limit_up_values: Sequence[bool] = (False,),
    foreign_heavy_sell_threshold: float = -0.05,
    margin_crowding_threshold: float = 5.0,
) -> Iterator[GridParameters]:
    event_sets = [tuple(event_set) for event_set in (event_type_sets or all_event_type_sets())]
    for event_set in event_sets:
        validate_event_type_set(event_set)

    for values in itertools.product(
        event_sets,
        min_turnover_values,
        min_volume_ratio_values,
        min_close_location_values,
        take_profit_values,
        stop_loss_values,
        path_assumptions,
        markets,
        require_foreign_not_selling_heavily_values,
        require_investment_trust_buying_values,
        avoid_margin_overcrowded_values,
        prefer_short_balance_rising_before_limit_up_values,
    ):
        (
            event_types,
            turnover,
            volume_ratio,
            close_location,
            take_profit,
            stop_loss,
            path,
            market,
            require_foreign,
            require_trust,
            avoid_margin,
            prefer_short,
        ) = values
        yield GridParameters(
            event_types=tuple(event_types),
            min_turnover_twd=float(turnover),
            min_volume_ratio_20d=float(volume_ratio),
            min_close_location=float(close_location),
            take_profit_pct=float(take_profit),
            stop_loss_pct=float(stop_loss),
            path_assumption=str(path).lower().strip(),
            market=str(market).upper().strip(),
            require_foreign_not_selling_heavily=bool(require_foreign),
            require_investment_trust_buying=bool(require_trust),
            avoid_margin_overcrowded=bool(avoid_margin),
            prefer_short_balance_rising_before_limit_up=bool(prefer_short),
            foreign_heavy_sell_threshold=float(foreign_heavy_sell_threshold),
            margin_crowding_threshold=float(margin_crowding_threshold),
        )


def all_event_type_sets(event_types: Sequence[str] = GRID_EVENT_TYPES) -> list[tuple[str, ...]]:
    return [
        tuple(combo)
        for size in range(1, len(event_types) + 1)
        for combo in itertools.combinations(event_types, size)
    ]


def make_config_hash(
    params: GridParameters,
    *,
    fixed_notional_twd: float,
    commission_rate: float,
    commission_discount: float,
    sell_tax_rate: float,
    slippage_bps_per_side: float,
    minimum_commission_twd: float,
    capacity_participation_rate: float,
) -> str:
    payload = params.payload() | {
        "fixed_notional_twd": fixed_notional_twd,
        "commission_rate": commission_rate,
        "commission_discount": commission_discount,
        "sell_tax_rate": sell_tax_rate,
        "slippage_bps_per_side": slippage_bps_per_side,
        "minimum_commission_twd": minimum_commission_twd,
        "capacity_participation_rate": capacity_participation_rate,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def load_existing_hashes(path: Path) -> set[str]:
    if not path.exists() or path.stat().st_size == 0:
        return set()
    existing = pd.read_csv(path, usecols=["config_hash"])
    return set(existing["config_hash"].dropna().astype(str))


def append_result_row(path: Path, row: dict[str, Any]) -> None:
    header = not path.exists() or path.stat().st_size == 0
    pd.DataFrame([row], columns=RESULT_COLUMNS).to_csv(path, mode="a", index=False, header=header)


def read_results(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame(columns=RESULT_COLUMNS)
    return pd.read_csv(path)


def write_empty_results(path: Path) -> None:
    pd.DataFrame(columns=RESULT_COLUMNS).to_csv(path, index=False)


def write_top_reports(
    results: pd.DataFrame,
    *,
    top_train_path: Path,
    top_test_path: Path,
    top_n: int,
) -> None:
    if results.empty:
        pd.DataFrame(columns=RESULT_COLUMNS).to_csv(top_train_path, index=False)
        pd.DataFrame(columns=RESULT_COLUMNS).to_csv(top_test_path, index=False)
        return

    top_train = results.sort_values(
        ["train_rank_eligible", "train_rank_score", "train_daily_sharpe", "train_avg_net_return"],
        ascending=[False, False, False, False],
        na_position="last",
    ).head(top_n)
    top_test = results.sort_values(
        ["test_net_pnl", "test_daily_sharpe", "test_avg_net_return", "test_trades"],
        ascending=[False, False, False, False],
        na_position="last",
    ).head(top_n)
    top_train.to_csv(top_train_path, index=False)
    top_test.to_csv(top_test_path, index=False)


def validate_grid_inputs(
    *,
    fixed_notional_twd: float,
    capacity_participation_rate: float,
    min_train_trades_for_ranking: int,
    top_n: int,
    path_assumptions: Sequence[str],
    markets: Sequence[str],
    margin_crowding_threshold: float,
) -> None:
    if fixed_notional_twd <= 0:
        raise ValueError("fixed_notional_twd must be positive")
    if capacity_participation_rate <= 0:
        raise ValueError("capacity_participation_rate must be positive")
    if min_train_trades_for_ranking < 0:
        raise ValueError("min_train_trades_for_ranking must be non-negative")
    if top_n <= 0:
        raise ValueError("top_n must be positive")
    normalized_paths = {str(path).lower().strip() for path in path_assumptions}
    invalid_paths = sorted(normalized_paths - set(PATH_ASSUMPTIONS))
    if invalid_paths:
        raise ValueError(f"Unsupported path assumptions: {invalid_paths}. Valid values are {PATH_ASSUMPTIONS}")
    for market in markets:
        normalize_markets([market])
    if margin_crowding_threshold < 0:
        raise ValueError("margin_crowding_threshold must be non-negative")


def validate_event_type_set(event_types: Sequence[str]) -> None:
    invalid = sorted(set(event_types) - set(GRID_EVENT_TYPES))
    if invalid:
        raise ValueError(f"Unsupported grid event type(s): {invalid}. Valid values are {GRID_EVENT_TYPES}")
    if not event_types:
        raise ValueError("event type set cannot be empty")


def empty_trade_universe() -> pd.DataFrame:
    return pd.DataFrame(
        columns=EVENT_CANDIDATE_COLUMNS
        + [
            "day1_trade_date",
            "day1_open",
            "day1_high",
            "day1_low",
            "day1_close",
            "shares",
            "entry_date",
            "holding_minutes",
        ]
    )


def default_report_dir() -> Path:
    return get_settings().project_root / "reports"


def _parse_csv_floats(value: str | None, default: Sequence[float]) -> list[float]:
    if value is None:
        return list(default)
    return [float(part.strip()) for part in value.split(",") if part.strip()]


def _parse_csv_strings(value: str | None, default: Sequence[str], *, upper: bool = False) -> list[str]:
    if value is None:
        return list(default)
    values = [part.strip() for part in value.split(",") if part.strip()]
    return [part.upper() for part in values] if upper else [part.lower() for part in values]


def _parse_csv_bools(value: str | None, default: Sequence[bool]) -> list[bool]:
    if value is None:
        return list(default)
    parsed: list[bool] = []
    for part in value.split(","):
        normalized = part.strip().lower()
        if not normalized:
            continue
        if normalized in {"true", "1", "yes", "y"}:
            parsed.append(True)
        elif normalized in {"false", "0", "no", "n"}:
            parsed.append(False)
        else:
            raise argparse.ArgumentTypeError(f"Expected comma-separated booleans, got {value!r}")
    return parsed


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a grid search for the daily limit-momentum strategy")
    parser.add_argument("--db", type=Path, default=get_settings().db_path, help="DuckDB path")
    parser.add_argument("--output-dir", type=Path, default=default_report_dir())
    parser.add_argument("--train-start", default=DEFAULT_TRAIN_START)
    parser.add_argument("--train-end", default=DEFAULT_TRAIN_END)
    parser.add_argument("--test-start", default=DEFAULT_TEST_START)
    parser.add_argument("--test-end", default=DEFAULT_TEST_END)
    parser.add_argument("--fixed-notional-twd", type=float, default=DEFAULT_FIXED_NOTIONAL_TWD)
    parser.add_argument("--commission-rate", type=float, default=0.001425)
    parser.add_argument("--commission-discount", type=float, default=0.28)
    parser.add_argument("--sell-tax-rate", type=float, default=0.0015)
    parser.add_argument("--slippage-bps-per-side", type=float, default=5.0)
    parser.add_argument("--minimum-commission-twd", type=float, default=20.0)
    parser.add_argument("--capacity-participation-rate", type=float, default=DEFAULT_CAPACITY_PARTICIPATION_RATE)
    parser.add_argument("--min-train-trades-for-ranking", type=int, default=20)
    parser.add_argument("--top-n", type=int, default=100)
    parser.add_argument("--force", action="store_true", help="Overwrite existing grid_search_results.csv")
    parser.add_argument("--no-progress", action="store_true", help="Disable tqdm progress output")
    parser.add_argument(
        "--skip-build-events",
        action="store_true",
        help="Use existing event_candidates instead of rebuilding the train/test window",
    )
    parser.add_argument("--max-runs", type=int, help="Optional development cap after skipped rows")
    parser.add_argument("--min-turnover-values", help="Comma-separated override, e.g. 50000000,100000000")
    parser.add_argument("--min-volume-ratio-values", help="Comma-separated override, e.g. 1.5,2.0")
    parser.add_argument("--min-close-location-values", help="Comma-separated override, e.g. 0.7,0.85")
    parser.add_argument("--take-profit-values", help="Comma-separated override, e.g. 0.015,0.03")
    parser.add_argument("--stop-loss-values", help="Comma-separated override, e.g. 0.01,0.02")
    parser.add_argument("--path-assumptions", help="Comma-separated override, e.g. pessimistic,close_only")
    parser.add_argument("--markets", help="Comma-separated override, e.g. TWSE,TPEX,BOTH")
    parser.add_argument("--require-foreign-not-selling-heavily-values", help="Comma-separated booleans, e.g. false,true")
    parser.add_argument("--require-investment-trust-buying-values", help="Comma-separated booleans, e.g. false,true")
    parser.add_argument("--avoid-margin-overcrowded-values", help="Comma-separated booleans, e.g. false,true")
    parser.add_argument("--prefer-short-balance-rising-before-limit-up-values", help="Comma-separated booleans, e.g. false,true")
    parser.add_argument("--foreign-heavy-sell-threshold", type=float, default=-0.05)
    parser.add_argument("--margin-crowding-threshold", type=float, default=5.0)
    args = parser.parse_args()

    results = run_grid_search(
        db_path=args.db,
        output_dir=args.output_dir,
        train_start=args.train_start,
        train_end=args.train_end,
        test_start=args.test_start,
        test_end=args.test_end,
        fixed_notional_twd=args.fixed_notional_twd,
        commission_rate=args.commission_rate,
        commission_discount=args.commission_discount,
        sell_tax_rate=args.sell_tax_rate,
        slippage_bps_per_side=args.slippage_bps_per_side,
        minimum_commission_twd=args.minimum_commission_twd,
        capacity_participation_rate=args.capacity_participation_rate,
        min_train_trades_for_ranking=args.min_train_trades_for_ranking,
        top_n=args.top_n,
        rebuild_events=not args.skip_build_events,
        force=args.force,
        show_progress=not args.no_progress,
        max_runs=args.max_runs,
        min_turnover_values=_parse_csv_floats(args.min_turnover_values, GRID_MIN_TURNOVER_TWD),
        min_volume_ratio_values=_parse_csv_floats(args.min_volume_ratio_values, GRID_MIN_VOLUME_RATIO_20D),
        min_close_location_values=_parse_csv_floats(args.min_close_location_values, GRID_MIN_CLOSE_LOCATION),
        take_profit_values=_parse_csv_floats(args.take_profit_values, GRID_TAKE_PROFIT_PCT),
        stop_loss_values=_parse_csv_floats(args.stop_loss_values, GRID_STOP_LOSS_PCT),
        path_assumptions=_parse_csv_strings(args.path_assumptions, GRID_PATH_ASSUMPTIONS),
        markets=_parse_csv_strings(args.markets, GRID_MARKETS, upper=True),
        require_foreign_not_selling_heavily_values=_parse_csv_bools(
            args.require_foreign_not_selling_heavily_values,
            [False],
        ),
        require_investment_trust_buying_values=_parse_csv_bools(args.require_investment_trust_buying_values, [False]),
        avoid_margin_overcrowded_values=_parse_csv_bools(args.avoid_margin_overcrowded_values, [False]),
        prefer_short_balance_rising_before_limit_up_values=_parse_csv_bools(
            args.prefer_short_balance_rising_before_limit_up_values,
            [False],
        ),
        foreign_heavy_sell_threshold=args.foreign_heavy_sell_threshold,
        margin_crowding_threshold=args.margin_crowding_threshold,
    )

    results_path = args.output_dir / "grid_search_results.csv"
    top_train_path = args.output_dir / "grid_search_top_train.csv"
    top_test_path = args.output_dir / "grid_search_top_test.csv"
    print(f"Wrote {len(results)} total result rows to {results_path}")
    print(f"Wrote top train-ranked rows to {top_train_path}")
    print(f"Wrote top test-ranked rows to {top_test_path}")
    if not results.empty:
        top_train = pd.read_csv(top_train_path)
        preview_columns = [
            "event_types",
            "market",
            "path_assumption",
            "train_trades",
            "train_net_pnl",
            "train_avg_net_return",
            "test_trades",
            "test_net_pnl",
            "test_avg_net_return",
        ]
        print("\ntop train-ranked preview")
        print(top_train[preview_columns].head(10).to_string(index=False))


if __name__ == "__main__":
    main()
