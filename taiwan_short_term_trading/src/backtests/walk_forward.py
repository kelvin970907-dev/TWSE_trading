"""Walk-forward validation for the daily limit-momentum strategy."""

from __future__ import annotations

import argparse
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from tqdm.auto import tqdm

from config.settings import get_settings
from src.backtests.event_study import build_event_candidates
from src.backtests.grid_search_limit_momentum import (
    DEFAULT_CAPACITY_PARTICIPATION_RATE,
    DEFAULT_FIXED_NOTIONAL_TWD,
    GRID_MARKETS,
    GRID_MIN_CLOSE_LOCATION,
    GRID_MIN_TURNOVER_TWD,
    GRID_MIN_VOLUME_RATIO_20D,
    GRID_PATH_ASSUMPTIONS,
    GRID_STOP_LOSS_PCT,
    GRID_TAKE_PROFIT_PCT,
    GridParameters,
    apply_grid_exits,
    apply_vectorized_costs,
    build_trade_universe,
    evaluate_trade_metrics,
    filter_trade_universe,
    iter_parameter_grid,
    load_grid_events,
    make_config_hash,
)
from src.backtests.strategy_limit_momentum import load_day1_daily_prices
from src.db import get_connection, init_db


DEFAULT_INITIAL_TRAIN_START = "2023-01-01"
DEFAULT_FIRST_TEST_START = "2024-01-01"
DEFAULT_FINAL_TEST_END = "2026-06-22"
DEFAULT_TEST_MONTHS = 3
DEFAULT_TOP_N = 5
DEFAULT_DRAWDOWN_PENALTY = 0.5

STABILITY_PARAMETER_COLUMNS = [
    "event_types",
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
]

BASE_RESULT_COLUMNS = [
    "window_id",
    "train_start",
    "train_end",
    "test_start",
    "test_end",
    "selected_rank",
    "config_hash",
    "score",
    "train_rank_eligible",
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

WALK_FORWARD_RESULT_COLUMNS = (
    BASE_RESULT_COLUMNS
    + [f"train_{column}" for column in METRIC_COLUMNS]
    + [f"oos_{column}" for column in METRIC_COLUMNS]
)

SELECTED_CONFIG_COLUMNS = WALK_FORWARD_RESULT_COLUMNS + [
    f"{column}_selection_count" for column in STABILITY_PARAMETER_COLUMNS
] + [
    f"{column}_selection_share" for column in STABILITY_PARAMETER_COLUMNS
]

EQUITY_CURVE_COLUMNS = [
    "window_id",
    "selected_rank",
    "config_hash",
    "trade_date",
    "daily_trades",
    "daily_net_pnl",
    "daily_buy_notional",
    "daily_return",
    "cumulative_net_pnl",
    "overall_cumulative_net_pnl",
]


@dataclass(frozen=True)
class WalkForwardWindow:
    window_id: str
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp


def run_walk_forward(
    *,
    db_path: Path | str,
    output_dir: Path | str | None = None,
    initial_train_start: str | pd.Timestamp = DEFAULT_INITIAL_TRAIN_START,
    first_test_start: str | pd.Timestamp = DEFAULT_FIRST_TEST_START,
    final_test_end: str | pd.Timestamp = DEFAULT_FINAL_TEST_END,
    test_months: int = DEFAULT_TEST_MONTHS,
    top_n: int = DEFAULT_TOP_N,
    min_train_trades: int = 1,
    drawdown_penalty: float = DEFAULT_DRAWDOWN_PENALTY,
    fixed_notional_twd: float = DEFAULT_FIXED_NOTIONAL_TWD,
    commission_rate: float = 0.001425,
    commission_discount: float = 0.28,
    sell_tax_rate: float = 0.0015,
    slippage_bps_per_side: float = 5.0,
    minimum_commission_twd: float = 20.0,
    capacity_participation_rate: float = DEFAULT_CAPACITY_PARTICIPATION_RATE,
    rebuild_events: bool = True,
    show_progress: bool = True,
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
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Run expanding-window walk-forward validation and write reports."""

    validate_walk_forward_inputs(
        test_months=test_months,
        top_n=top_n,
        min_train_trades=min_train_trades,
        drawdown_penalty=drawdown_penalty,
        fixed_notional_twd=fixed_notional_twd,
        capacity_participation_rate=capacity_participation_rate,
        margin_crowding_threshold=margin_crowding_threshold,
    )
    windows = list(
        generate_walk_forward_windows(
            initial_train_start=initial_train_start,
            first_test_start=first_test_start,
            final_test_end=final_test_end,
            test_months=test_months,
        )
    )
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

    output_path = Path(output_dir) if output_dir is not None else default_report_dir()
    output_path.mkdir(parents=True, exist_ok=True)

    init_db(db_path)
    if rebuild_events:
        build_event_candidates(
            db_path=db_path,
            start=pd.Timestamp(initial_train_start),
            end=pd.Timestamp(final_test_end),
            markets=["BOTH"],
        )

    with get_connection(db_path) as conn:
        events = load_grid_events(conn, start=initial_train_start, end=final_test_end)
        day1_prices = load_day1_daily_prices(conn)
    universe = build_trade_universe(events, day1_prices, fixed_notional_twd=fixed_notional_twd)

    result_rows: list[dict[str, Any]] = []
    equity_frames: list[pd.DataFrame] = []
    window_iter = tqdm(windows, total=len(windows), disable=not show_progress, desc="walk-forward")
    for window in window_iter:
        ranked = rank_train_configs(
            parameter_sets,
            universe=universe,
            window=window,
            fixed_notional_twd=fixed_notional_twd,
            commission_rate=commission_rate,
            commission_discount=commission_discount,
            sell_tax_rate=sell_tax_rate,
            slippage_bps_per_side=slippage_bps_per_side,
            minimum_commission_twd=minimum_commission_twd,
            capacity_participation_rate=capacity_participation_rate,
            drawdown_penalty=drawdown_penalty,
            min_train_trades=min_train_trades,
        )
        selected = select_top_configs(ranked, top_n=top_n)
        for selected_rank, (params, train_metrics, score, eligible) in enumerate(selected, start=1):
            oos_trades = filter_trade_universe(
                universe,
                params=params,
                start=window.test_start,
                end=window.test_end,
            )
            oos_metrics = evaluate_trade_metrics(
                oos_trades,
                params=params,
                start=window.test_start,
                end=window.test_end,
                commission_rate=commission_rate,
                commission_discount=commission_discount,
                sell_tax_rate=sell_tax_rate,
                slippage_bps_per_side=slippage_bps_per_side,
                minimum_commission_twd=minimum_commission_twd,
                capacity_participation_rate=capacity_participation_rate,
            )
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
            result_rows.append(
                build_walk_forward_result_row(
                    window=window,
                    selected_rank=selected_rank,
                    params=params,
                    config_hash=config_hash,
                    score=score,
                    eligible=eligible,
                    train_metrics=train_metrics,
                    oos_metrics=oos_metrics,
                    fixed_notional_twd=fixed_notional_twd,
                    commission_rate=commission_rate,
                    commission_discount=commission_discount,
                    sell_tax_rate=sell_tax_rate,
                    slippage_bps_per_side=slippage_bps_per_side,
                    minimum_commission_twd=minimum_commission_twd,
                    capacity_participation_rate=capacity_participation_rate,
                )
            )
            priced_oos = price_oos_trades(
                oos_trades,
                params=params,
                commission_rate=commission_rate,
                commission_discount=commission_discount,
                sell_tax_rate=sell_tax_rate,
                slippage_bps_per_side=slippage_bps_per_side,
                minimum_commission_twd=minimum_commission_twd,
            )
            equity_frames.append(
                build_oos_equity_curve(
                    priced_oos,
                    window_id=window.window_id,
                    selected_rank=selected_rank,
                    config_hash=config_hash,
                )
            )

    results = pd.DataFrame(result_rows, columns=WALK_FORWARD_RESULT_COLUMNS)
    selected_configs = add_parameter_stability(results)
    equity_curve = combine_equity_curves(equity_frames)

    results.to_csv(output_path / "walk_forward_results.csv", index=False)
    selected_configs.to_csv(output_path / "walk_forward_selected_configs.csv", index=False)
    equity_curve.to_csv(output_path / "walk_forward_oos_equity_curve.csv", index=False)
    return results, selected_configs, equity_curve


def generate_walk_forward_windows(
    *,
    initial_train_start: str | pd.Timestamp,
    first_test_start: str | pd.Timestamp,
    final_test_end: str | pd.Timestamp,
    test_months: int = DEFAULT_TEST_MONTHS,
) -> Iterator[WalkForwardWindow]:
    """Generate expanding train windows followed by fixed-length OOS windows."""

    if test_months <= 0:
        raise ValueError("test_months must be positive")
    train_start = pd.Timestamp(initial_train_start).normalize()
    test_start = pd.Timestamp(first_test_start).normalize()
    final_end = pd.Timestamp(final_test_end).normalize()
    if test_start <= train_start:
        raise ValueError("first_test_start must be after initial_train_start")
    if final_end < test_start:
        raise ValueError("final_test_end must be on or after first_test_start")

    index = 1
    current_test_start = test_start
    while current_test_start <= final_end:
        test_end = min(current_test_start + pd.DateOffset(months=test_months) - pd.Timedelta(days=1), final_end)
        train_end = current_test_start - pd.Timedelta(days=1)
        yield WalkForwardWindow(
            window_id=f"WF{index:03d}",
            train_start=train_start,
            train_end=train_end,
            test_start=current_test_start,
            test_end=test_end,
        )
        current_test_start = test_end + pd.Timedelta(days=1)
        index += 1


def rank_train_configs(
    parameter_sets: Sequence[GridParameters],
    *,
    universe: pd.DataFrame,
    window: WalkForwardWindow,
    fixed_notional_twd: float,
    commission_rate: float,
    commission_discount: float,
    sell_tax_rate: float,
    slippage_bps_per_side: float,
    minimum_commission_twd: float,
    capacity_participation_rate: float,
    drawdown_penalty: float,
    min_train_trades: int,
) -> list[tuple[GridParameters, dict[str, float], float, bool]]:
    ranked: list[tuple[GridParameters, dict[str, float], float, bool]] = []
    for params in parameter_sets:
        train_trades = filter_trade_universe(
            universe,
            params=params,
            start=window.train_start,
            end=window.train_end,
        )
        metrics = evaluate_trade_metrics(
            train_trades,
            params=params,
            start=window.train_start,
            end=window.train_end,
            commission_rate=commission_rate,
            commission_discount=commission_discount,
            sell_tax_rate=sell_tax_rate,
            slippage_bps_per_side=slippage_bps_per_side,
            minimum_commission_twd=minimum_commission_twd,
            capacity_participation_rate=capacity_participation_rate,
        )
        eligible = metrics["trades"] >= min_train_trades
        score = walk_forward_score(
            avg_net_return=metrics["avg_net_return"],
            num_trades=metrics["trades"],
            max_drawdown=metrics["max_drawdown"],
            drawdown_penalty=drawdown_penalty,
        )
        if not eligible:
            score = -np.inf
        ranked.append((params, metrics, score, eligible))

    return sorted(
        ranked,
        key=lambda item: (item[3], item[2], item[1]["trades"], item[1]["net_pnl"]),
        reverse=True,
    )


def select_top_configs(
    ranked: Sequence[tuple[GridParameters, dict[str, float], float, bool]],
    *,
    top_n: int,
) -> list[tuple[GridParameters, dict[str, float], float, bool]]:
    if not ranked:
        return []
    eligible = [item for item in ranked if item[3]]
    source = eligible if eligible else list(ranked)
    return source[:top_n]


def walk_forward_score(
    *,
    avg_net_return: float,
    num_trades: int | float,
    max_drawdown: float,
    drawdown_penalty: float = DEFAULT_DRAWDOWN_PENALTY,
) -> float:
    if num_trades <= 0:
        return -np.inf
    return float(avg_net_return * np.sqrt(num_trades) - drawdown_penalty * abs(max_drawdown))


def build_walk_forward_result_row(
    *,
    window: WalkForwardWindow,
    selected_rank: int,
    params: GridParameters,
    config_hash: str,
    score: float,
    eligible: bool,
    train_metrics: dict[str, float],
    oos_metrics: dict[str, float],
    fixed_notional_twd: float,
    commission_rate: float,
    commission_discount: float,
    sell_tax_rate: float,
    slippage_bps_per_side: float,
    minimum_commission_twd: float,
    capacity_participation_rate: float,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "window_id": window.window_id,
        "train_start": window.train_start.date().isoformat(),
        "train_end": window.train_end.date().isoformat(),
        "test_start": window.test_start.date().isoformat(),
        "test_end": window.test_end.date().isoformat(),
        "selected_rank": selected_rank,
        "config_hash": config_hash,
        "score": score,
        "train_rank_eligible": bool(eligible),
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
    }
    row.update({f"train_{key}": value for key, value in train_metrics.items()})
    row.update({f"oos_{key}": value for key, value in oos_metrics.items()})
    return {column: row.get(column, np.nan) for column in WALK_FORWARD_RESULT_COLUMNS}


def price_oos_trades(
    trades: pd.DataFrame,
    *,
    params: GridParameters,
    commission_rate: float,
    commission_discount: float,
    sell_tax_rate: float,
    slippage_bps_per_side: float,
    minimum_commission_twd: float,
) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    priced = apply_grid_exits(trades, params=params)
    return apply_vectorized_costs(
        priced,
        commission_rate=commission_rate,
        commission_discount=commission_discount,
        sell_tax_rate=sell_tax_rate,
        slippage_bps_per_side=slippage_bps_per_side,
        minimum_commission_twd=minimum_commission_twd,
    )


def build_oos_equity_curve(
    priced_trades: pd.DataFrame,
    *,
    window_id: str,
    selected_rank: int,
    config_hash: str,
) -> pd.DataFrame:
    if priced_trades.empty:
        return pd.DataFrame(columns=EQUITY_CURVE_COLUMNS)

    daily = (
        priced_trades.groupby("entry_date", dropna=False)
        .agg(
            daily_trades=("net_pnl", "size"),
            daily_net_pnl=("net_pnl", "sum"),
            daily_buy_notional=("buy_notional", "sum"),
        )
        .reset_index()
        .rename(columns={"entry_date": "trade_date"})
    )
    daily["window_id"] = window_id
    daily["selected_rank"] = selected_rank
    daily["config_hash"] = config_hash
    daily["daily_return"] = daily["daily_net_pnl"] / daily["daily_buy_notional"].replace(0, np.nan)
    daily["cumulative_net_pnl"] = daily["daily_net_pnl"].cumsum()
    daily["overall_cumulative_net_pnl"] = np.nan
    return daily[EQUITY_CURVE_COLUMNS]


def combine_equity_curves(frames: Sequence[pd.DataFrame]) -> pd.DataFrame:
    non_empty = [frame for frame in frames if not frame.empty]
    if not non_empty:
        return pd.DataFrame(columns=EQUITY_CURVE_COLUMNS)
    curve = pd.concat(non_empty, ignore_index=True)
    curve["trade_date"] = pd.to_datetime(curve["trade_date"])
    curve = curve.sort_values(["trade_date", "window_id", "selected_rank", "config_hash"]).reset_index(drop=True)
    curve["overall_cumulative_net_pnl"] = curve["daily_net_pnl"].cumsum()
    curve["trade_date"] = curve["trade_date"].dt.date.astype(str)
    return curve[EQUITY_CURVE_COLUMNS]


def add_parameter_stability(results: pd.DataFrame) -> pd.DataFrame:
    if results.empty:
        return pd.DataFrame(columns=SELECTED_CONFIG_COLUMNS)

    frame = results.copy()
    total = len(frame)
    for column in STABILITY_PARAMETER_COLUMNS:
        counts = frame[column].value_counts(dropna=False)
        frame[f"{column}_selection_count"] = frame[column].map(counts).astype("int64")
        frame[f"{column}_selection_share"] = frame[f"{column}_selection_count"] / total
    return frame[SELECTED_CONFIG_COLUMNS]


def validate_walk_forward_inputs(
    *,
    test_months: int,
    top_n: int,
    min_train_trades: int,
    drawdown_penalty: float,
    fixed_notional_twd: float,
    capacity_participation_rate: float,
    margin_crowding_threshold: float,
) -> None:
    if test_months <= 0:
        raise ValueError("test_months must be positive")
    if top_n <= 0:
        raise ValueError("top_n must be positive")
    if min_train_trades < 0:
        raise ValueError("min_train_trades must be non-negative")
    if drawdown_penalty < 0:
        raise ValueError("drawdown_penalty must be non-negative")
    if fixed_notional_twd <= 0:
        raise ValueError("fixed_notional_twd must be positive")
    if capacity_participation_rate <= 0:
        raise ValueError("capacity_participation_rate must be positive")
    if margin_crowding_threshold < 0:
        raise ValueError("margin_crowding_threshold must be non-negative")


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
    parser = argparse.ArgumentParser(description="Run walk-forward validation for limit momentum")
    parser.add_argument("--db", type=Path, default=get_settings().db_path, help="DuckDB path")
    parser.add_argument("--output-dir", type=Path, default=default_report_dir())
    parser.add_argument("--initial-train-start", default=DEFAULT_INITIAL_TRAIN_START)
    parser.add_argument("--first-test-start", default=DEFAULT_FIRST_TEST_START)
    parser.add_argument("--final-test-end", default=DEFAULT_FINAL_TEST_END)
    parser.add_argument("--test-months", type=int, default=DEFAULT_TEST_MONTHS)
    parser.add_argument("--top-n", type=int, default=DEFAULT_TOP_N)
    parser.add_argument("--min-train-trades", type=int, default=1)
    parser.add_argument("--drawdown-penalty", type=float, default=DEFAULT_DRAWDOWN_PENALTY)
    parser.add_argument("--fixed-notional-twd", type=float, default=DEFAULT_FIXED_NOTIONAL_TWD)
    parser.add_argument("--commission-rate", type=float, default=0.001425)
    parser.add_argument("--commission-discount", type=float, default=0.28)
    parser.add_argument("--sell-tax-rate", type=float, default=0.0015)
    parser.add_argument("--slippage-bps-per-side", type=float, default=5.0)
    parser.add_argument("--minimum-commission-twd", type=float, default=20.0)
    parser.add_argument("--capacity-participation-rate", type=float, default=DEFAULT_CAPACITY_PARTICIPATION_RATE)
    parser.add_argument(
        "--skip-build-events",
        action="store_true",
        help="Use existing event_candidates instead of rebuilding the full walk-forward window",
    )
    parser.add_argument("--no-progress", action="store_true", help="Disable tqdm progress output")
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

    results, selected_configs, equity_curve = run_walk_forward(
        db_path=args.db,
        output_dir=args.output_dir,
        initial_train_start=args.initial_train_start,
        first_test_start=args.first_test_start,
        final_test_end=args.final_test_end,
        test_months=args.test_months,
        top_n=args.top_n,
        min_train_trades=args.min_train_trades,
        drawdown_penalty=args.drawdown_penalty,
        fixed_notional_twd=args.fixed_notional_twd,
        commission_rate=args.commission_rate,
        commission_discount=args.commission_discount,
        sell_tax_rate=args.sell_tax_rate,
        slippage_bps_per_side=args.slippage_bps_per_side,
        minimum_commission_twd=args.minimum_commission_twd,
        capacity_participation_rate=args.capacity_participation_rate,
        rebuild_events=not args.skip_build_events,
        show_progress=not args.no_progress,
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

    print(f"Wrote {len(results)} walk-forward result rows to {args.output_dir / 'walk_forward_results.csv'}")
    print(
        f"Wrote {len(selected_configs)} selected config rows to "
        f"{args.output_dir / 'walk_forward_selected_configs.csv'}"
    )
    print(f"Wrote {len(equity_curve)} OOS equity rows to {args.output_dir / 'walk_forward_oos_equity_curve.csv'}")
    if not selected_configs.empty:
        preview_columns = [
            "window_id",
            "selected_rank",
            "event_types",
            "market",
            "path_assumption",
            "score",
            "train_trades",
            "oos_trades",
            "oos_net_pnl",
            "oos_win_rate",
            "oos_avg_net_return",
            "oos_max_drawdown",
        ]
        print("\nselected config preview")
        print(selected_configs[preview_columns].head(10).to_string(index=False))


if __name__ == "__main__":
    main()
