"""Walk-forward validation for closed-limit-up overnight gap capture."""

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
from src.backtests.closed_limit_up_fill_audit import (
    DEFAULT_FILL_QUALITY_THRESHOLDS,
    FILL_ASSUMPTIONS,
    FillAuditThresholds,
    add_fill_proxy_features,
    evaluate_fill_assumption,
)
from src.backtests.closed_limit_up_overnight import build_overnight_trades
from src.backtests.event_study import build_event_candidates
from src.backtests.limit_up_gap_capture import (
    build_limit_up_gap_frame,
    load_closed_limit_up_events,
    load_daily_prices_for_gap_capture,
)
from src.db import get_connection, init_db


DEFAULT_START = "2023-01-01"
DEFAULT_END = "2026-06-24"
DEFAULT_FIRST_TEST_START = "2024-01-01"
DEFAULT_TEST_MONTHS = 3
DEFAULT_TOP_N = 5
DEFAULT_FIXED_NOTIONAL_TWD = 100_000.0

GRID_MARKETS = ("TWSE", "TPEX", "BOTH")
GRID_MIN_TURNOVER_TWD = (100_000_000.0, 200_000_000.0, 500_000_000.0, 1_000_000_000.0)
GRID_MIN_VOLUME_RATIO_20D = (1.5, 2.0, 3.0, 5.0)
GRID_MAX_CONSECUTIVE_LIMIT_UPS = (1, 2, 3)
GRID_ONLY_FIRST_LIMIT_UP = (True, False)
GRID_MIN_PRICE = (10.0, 20.0, 50.0)
GRID_MAX_PRICE = (100.0, 200.0, 500.0)
GRID_MIN_FILL_QUALITY_SCORE = (40.0, 50.0, 60.0, 70.0)

PARAMETER_COLUMNS = [
    "config_hash",
    "market",
    "min_turnover_twd",
    "min_volume_ratio_20d",
    "max_consecutive_limit_ups",
    "only_first_limit_up",
    "min_price",
    "max_price",
    "fill_assumption",
    "min_fill_quality_score",
]

METRIC_COLUMNS = [
    "trades",
    "net_pnl",
    "avg_net_return",
    "median_net_return",
    "win_rate",
    "profit_factor",
    "max_drawdown",
    "max_drawdown_pct",
    "total_buy_notional",
    "avg_turnover_twd",
    "avg_volume_ratio_20d",
    "avg_fill_quality_score",
    "top10_symbol_trade_share",
    "dominant_year_pnl_share",
]

RESULT_COLUMNS = [
    "window_id",
    "train_start",
    "train_end",
    "test_start",
    "test_end",
    "selected_rank",
    *PARAMETER_COLUMNS,
    "robust_score",
    "overfit_penalty",
    "train_rank_eligible",
    *[f"train_{column}" for column in METRIC_COLUMNS],
    *[f"oos_{column}" for column in METRIC_COLUMNS],
]

STABILITY_PARAMETER_COLUMNS = [
    "market",
    "min_turnover_twd",
    "min_volume_ratio_20d",
    "max_consecutive_limit_ups",
    "only_first_limit_up",
    "min_price",
    "max_price",
    "fill_assumption",
    "min_fill_quality_score",
]

SELECTED_CONFIG_COLUMNS = RESULT_COLUMNS + [
    f"{column}_selection_count" for column in STABILITY_PARAMETER_COLUMNS
] + [
    f"{column}_selection_share" for column in STABILITY_PARAMETER_COLUMNS
]

OOS_TRADE_EXTRA_COLUMNS = [
    "window_id",
    "selected_rank",
    "config_hash",
    "test_start",
    "test_end",
    "market_param",
    "min_turnover_twd_param",
    "min_volume_ratio_20d_param",
    "max_consecutive_limit_ups_param",
    "only_first_limit_up_param",
    "min_price_param",
    "max_price_param",
    "fill_assumption_param",
    "min_fill_quality_score_param",
]

SUMMARY_COLUMNS = [
    "summary_level",
    "group",
    "trades",
    "net_pnl",
    "avg_net_return",
    "median_net_return",
    "profit_factor",
    "max_drawdown",
    "positive_oos_windows",
    "total_windows",
    "selection_count",
    "selection_share",
    "details_json",
]


@dataclass(frozen=True)
class WalkForwardWindow:
    window_id: str
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp


@dataclass(frozen=True)
class OvernightWFParameters:
    market: str
    min_turnover_twd: float
    min_volume_ratio_20d: float
    max_consecutive_limit_ups: int
    only_first_limit_up: bool
    min_price: float
    max_price: float
    fill_assumption: str
    min_fill_quality_score: float

    def payload(self) -> dict[str, Any]:
        return {
            "market": self.market,
            "min_turnover_twd": self.min_turnover_twd,
            "min_volume_ratio_20d": self.min_volume_ratio_20d,
            "max_consecutive_limit_ups": self.max_consecutive_limit_ups,
            "only_first_limit_up": self.only_first_limit_up,
            "min_price": self.min_price,
            "max_price": self.max_price,
            "fill_assumption": self.fill_assumption,
            "min_fill_quality_score": self.min_fill_quality_score,
        }


@dataclass(frozen=True)
class PreparedUniverse:
    signal_ord: np.ndarray
    exit_ord: np.ndarray
    market: np.ndarray
    turnover: np.ndarray
    volume_ratio: np.ndarray
    consecutive_count: np.ndarray
    first_limit: np.ndarray
    price: np.ndarray
    fill_score: np.ndarray
    fillable: dict[str, np.ndarray]
    net_pnl: np.ndarray
    net_return: np.ndarray
    buy_notional: np.ndarray
    symbol_code: np.ndarray
    year_code: np.ndarray
    year_values: np.ndarray


def run_walk_forward_closed_limit_up_overnight(
    *,
    db_path: Path | str,
    start: str | pd.Timestamp = DEFAULT_START,
    end: str | pd.Timestamp = DEFAULT_END,
    output_dir: Path | str | None = None,
    first_test_start: str | pd.Timestamp = DEFAULT_FIRST_TEST_START,
    test_months: int = DEFAULT_TEST_MONTHS,
    top_n: int = DEFAULT_TOP_N,
    min_train_trades: int = 1,
    fixed_notional_twd: float = DEFAULT_FIXED_NOTIONAL_TWD,
    commission_rate: float = 0.001425,
    commission_discount: float = 0.28,
    sell_tax_rate: float = 0.003,
    slippage_bps_per_side: float = 5.0,
    minimum_commission_twd: float = 20.0,
    fill_thresholds: FillAuditThresholds | None = None,
    rebuild_events: bool = True,
    show_progress: bool = True,
    markets: Sequence[str] = GRID_MARKETS,
    min_turnover_values: Sequence[float] = GRID_MIN_TURNOVER_TWD,
    min_volume_ratio_values: Sequence[float] = GRID_MIN_VOLUME_RATIO_20D,
    max_consecutive_limit_up_values: Sequence[int] = GRID_MAX_CONSECUTIVE_LIMIT_UPS,
    only_first_limit_up_values: Sequence[bool] = GRID_ONLY_FIRST_LIMIT_UP,
    min_price_values: Sequence[float] = GRID_MIN_PRICE,
    max_price_values: Sequence[float] = GRID_MAX_PRICE,
    fill_assumptions: Sequence[str] = FILL_ASSUMPTIONS,
    min_fill_quality_score_values: Sequence[float] = GRID_MIN_FILL_QUALITY_SCORE,
    max_configs: int | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Run quarterly expanding-window OOS validation for the overnight edge."""

    validate_inputs(
        start=start,
        end=end,
        first_test_start=first_test_start,
        test_months=test_months,
        top_n=top_n,
        min_train_trades=min_train_trades,
        fixed_notional_twd=fixed_notional_twd,
        commission_rate=commission_rate,
        commission_discount=commission_discount,
        sell_tax_rate=sell_tax_rate,
        slippage_bps_per_side=slippage_bps_per_side,
        minimum_commission_twd=minimum_commission_twd,
        max_configs=max_configs,
    )
    windows = list(
        generate_walk_forward_windows(
            start=start,
            first_test_start=first_test_start,
            end=end,
            test_months=test_months,
        )
    )
    parameter_sets = list(
        iter_parameter_grid(
            markets=markets,
            min_turnover_values=min_turnover_values,
            min_volume_ratio_values=min_volume_ratio_values,
            max_consecutive_limit_up_values=max_consecutive_limit_up_values,
            only_first_limit_up_values=only_first_limit_up_values,
            min_price_values=min_price_values,
            max_price_values=max_price_values,
            fill_assumptions=fill_assumptions,
            min_fill_quality_score_values=min_fill_quality_score_values,
        )
    )
    if max_configs is not None:
        parameter_sets = parameter_sets[:max_configs]

    output_path = Path(output_dir) if output_dir is not None else default_report_dir()
    output_path.mkdir(parents=True, exist_ok=True)

    universe = build_walk_forward_universe(
        db_path=db_path,
        start=start,
        end=end,
        fixed_notional_twd=fixed_notional_twd,
        commission_rate=commission_rate,
        commission_discount=commission_discount,
        sell_tax_rate=sell_tax_rate,
        slippage_bps_per_side=slippage_bps_per_side,
        minimum_commission_twd=minimum_commission_twd,
        fill_thresholds=fill_thresholds,
        rebuild_events=rebuild_events,
    )
    prepared_universe = prepare_universe_arrays(universe)

    result_rows: list[dict[str, Any]] = []
    oos_trade_frames: list[pd.DataFrame] = []
    window_iter = tqdm(windows, total=len(windows), disable=not show_progress, desc="closed-limit WF")
    for window in window_iter:
        ranked = rank_train_configs(
            parameter_sets,
            universe=universe,
            prepared_universe=prepared_universe,
            window=window,
            min_train_trades=min_train_trades,
            show_progress=False,
        )
        selected = select_top_configs(ranked, top_n=top_n)
        for selected_rank, ranked_item in enumerate(selected, start=1):
            params = ranked_item["params"]
            config_hash = make_config_hash(params)
            oos_trades = filter_universe(
                universe,
                params=params,
                start=window.test_start,
                end=window.test_end,
            )
            oos_metrics = evaluate_trade_metrics(oos_trades)
            result_rows.append(
                build_result_row(
                    window=window,
                    selected_rank=selected_rank,
                    params=params,
                    config_hash=config_hash,
                    train_metrics=ranked_item["metrics"],
                    oos_metrics=oos_metrics,
                    robust_score=ranked_item["score"],
                    overfit_penalty=ranked_item["penalty"],
                    eligible=ranked_item["eligible"],
                )
            )
            oos_trade_frames.append(
                annotate_oos_trades(
                    oos_trades,
                    window=window,
                    selected_rank=selected_rank,
                    params=params,
                    config_hash=config_hash,
                )
            )

    results = pd.DataFrame(result_rows, columns=RESULT_COLUMNS)
    selected_configs = add_parameter_stability(results)
    oos_trades = combine_oos_trades(oos_trade_frames)
    summary = summarize_walk_forward(results, oos_trades)

    results.to_csv(output_path / "walk_forward_closed_limit_up_overnight_results.csv", index=False)
    selected_configs.to_csv(output_path / "walk_forward_closed_limit_up_overnight_selected_configs.csv", index=False)
    oos_trades.to_csv(output_path / "walk_forward_closed_limit_up_overnight_oos_trades.csv", index=False)
    summary.to_csv(output_path / "walk_forward_closed_limit_up_overnight_summary.csv", index=False)
    return results, selected_configs, oos_trades, summary


def build_walk_forward_universe(
    *,
    db_path: Path | str,
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
    fixed_notional_twd: float,
    commission_rate: float,
    commission_discount: float,
    sell_tax_rate: float,
    slippage_bps_per_side: float,
    minimum_commission_twd: float,
    fill_thresholds: FillAuditThresholds | None,
    rebuild_events: bool,
) -> pd.DataFrame:
    """Build all candidate overnight trades and daily fill proxies once."""

    init_db(db_path)
    if rebuild_events:
        build_event_candidates(db_path=db_path, start=start, end=end, markets=["BOTH"])

    with get_connection(db_path) as conn:
        events = load_closed_limit_up_events(conn, start=start, end=end, markets=["BOTH"])
        daily_prices = load_daily_prices_for_gap_capture(conn, markets=["BOTH"])

    study = build_limit_up_gap_frame(
        events,
        daily_prices,
        normal_sell_tax_rate=sell_tax_rate,
        slippage_bps_per_side=slippage_bps_per_side,
        minimum_commission_twd=minimum_commission_twd,
    )
    trades = build_overnight_trades(
        study,
        fixed_notional_twd=fixed_notional_twd,
        commission_rate=commission_rate,
        commission_discount=commission_discount,
        sell_tax_rate=sell_tax_rate,
        slippage_bps_per_side=slippage_bps_per_side,
        minimum_commission_twd=minimum_commission_twd,
    )
    universe = add_fill_proxy_features(trades)
    if universe.empty:
        return universe

    thresholds = fill_thresholds or FillAuditThresholds()
    for assumption in FILL_ASSUMPTIONS:
        fillable, _ = evaluate_fill_assumption(
            universe,
            fill_assumption=assumption,
            thresholds=thresholds,
        )
        universe[f"fillable_{assumption}"] = fillable.astype(bool).to_numpy()

    universe["signal_date"] = pd.to_datetime(universe["signal_date"]).dt.normalize()
    universe["entry_date"] = pd.to_datetime(universe["entry_date"]).dt.normalize()
    universe["exit_date"] = pd.to_datetime(universe["exit_date"]).dt.normalize()
    universe["year"] = universe["entry_date"].dt.year
    universe["quarter"] = universe["entry_date"].dt.to_period("Q").astype(str)
    return universe.sort_values(["entry_date", "market", "symbol", "trade_id"]).reset_index(drop=True)


def generate_walk_forward_windows(
    *,
    start: str | pd.Timestamp,
    first_test_start: str | pd.Timestamp,
    end: str | pd.Timestamp,
    test_months: int = DEFAULT_TEST_MONTHS,
) -> Iterator[WalkForwardWindow]:
    if test_months <= 0:
        raise ValueError("test_months must be positive")
    train_start = pd.Timestamp(start).normalize()
    current_test_start = pd.Timestamp(first_test_start).normalize()
    final_end = pd.Timestamp(end).normalize()
    if current_test_start <= train_start:
        raise ValueError("first_test_start must be after start")
    if final_end < current_test_start:
        raise ValueError("end must be on or after first_test_start")

    index = 1
    while current_test_start <= final_end:
        test_end = min(current_test_start + pd.DateOffset(months=test_months) - pd.Timedelta(days=1), final_end)
        yield WalkForwardWindow(
            window_id=f"CLUWF{index:03d}",
            train_start=train_start,
            train_end=current_test_start - pd.Timedelta(days=1),
            test_start=current_test_start,
            test_end=test_end,
        )
        current_test_start = test_end + pd.Timedelta(days=1)
        index += 1


def iter_parameter_grid(
    *,
    markets: Sequence[str] = GRID_MARKETS,
    min_turnover_values: Sequence[float] = GRID_MIN_TURNOVER_TWD,
    min_volume_ratio_values: Sequence[float] = GRID_MIN_VOLUME_RATIO_20D,
    max_consecutive_limit_up_values: Sequence[int] = GRID_MAX_CONSECUTIVE_LIMIT_UPS,
    only_first_limit_up_values: Sequence[bool] = GRID_ONLY_FIRST_LIMIT_UP,
    min_price_values: Sequence[float] = GRID_MIN_PRICE,
    max_price_values: Sequence[float] = GRID_MAX_PRICE,
    fill_assumptions: Sequence[str] = FILL_ASSUMPTIONS,
    min_fill_quality_score_values: Sequence[float] = GRID_MIN_FILL_QUALITY_SCORE,
) -> Iterator[OvernightWFParameters]:
    normalized_markets = normalize_grid_markets(markets)
    normalized_fill_assumptions = [normalize_fill_assumption(value) for value in fill_assumptions]
    for (
        market,
        min_turnover,
        min_volume_ratio,
        max_count,
        first_only,
        min_price,
        max_price,
        fill_assumption,
        min_score,
    ) in itertools.product(
        normalized_markets,
        min_turnover_values,
        min_volume_ratio_values,
        max_consecutive_limit_up_values,
        only_first_limit_up_values,
        min_price_values,
        max_price_values,
        normalized_fill_assumptions,
        min_fill_quality_score_values,
    ):
        if float(max_price) < float(min_price):
            continue
        yield OvernightWFParameters(
            market=market,
            min_turnover_twd=float(min_turnover),
            min_volume_ratio_20d=float(min_volume_ratio),
            max_consecutive_limit_ups=int(max_count),
            only_first_limit_up=bool(first_only),
            min_price=float(min_price),
            max_price=float(max_price),
            fill_assumption=fill_assumption,
            min_fill_quality_score=float(min_score),
        )


def filter_universe(
    universe: pd.DataFrame,
    *,
    params: OvernightWFParameters,
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
) -> pd.DataFrame:
    if universe.empty:
        return universe.copy()
    start_ts = pd.Timestamp(start).normalize()
    end_ts = pd.Timestamp(end).normalize()
    dates = pd.to_datetime(universe["signal_date"]).dt.normalize()
    mask = (dates >= start_ts) & (dates <= end_ts)
    if params.market != "BOTH":
        mask &= universe["market"].eq(params.market)
    mask &= pd.to_numeric(universe["day0_turnover_twd"], errors="coerce") >= params.min_turnover_twd
    mask &= pd.to_numeric(universe["volume_ratio_20d"], errors="coerce") >= params.min_volume_ratio_20d
    mask &= pd.to_numeric(universe["consecutive_limit_up_count"], errors="coerce") <= params.max_consecutive_limit_ups
    if params.only_first_limit_up:
        mask &= universe["first_limit_up_in_sequence"].fillna(False).astype(bool)
    mask &= pd.to_numeric(universe["day0_close"], errors="coerce") >= params.min_price
    mask &= pd.to_numeric(universe["day0_close"], errors="coerce") <= params.max_price
    mask &= pd.to_numeric(universe["fill_quality_score"], errors="coerce") >= params.min_fill_quality_score
    fill_column = f"fillable_{params.fill_assumption}"
    if fill_column not in universe.columns:
        raise ValueError(f"universe is missing {fill_column}; build fill proxies first")
    mask &= universe[fill_column].fillna(False).astype(bool)
    return universe[mask.fillna(False)].copy().reset_index(drop=True)


def prepare_universe_arrays(universe: pd.DataFrame) -> PreparedUniverse:
    """Precompute arrays used by the full walk-forward grid scorer."""

    if universe.empty:
        empty = np.array([], dtype=float)
        return PreparedUniverse(
            signal_ord=np.array([], dtype="int64"),
            exit_ord=np.array([], dtype="int64"),
            market=np.array([], dtype=object),
            turnover=empty,
            volume_ratio=empty,
            consecutive_count=empty,
            first_limit=np.array([], dtype=bool),
            price=empty,
            fill_score=empty,
            fillable={assumption: np.array([], dtype=bool) for assumption in FILL_ASSUMPTIONS},
            net_pnl=empty,
            net_return=empty,
            buy_notional=empty,
            symbol_code=np.array([], dtype="int64"),
            year_code=np.array([], dtype="int64"),
            year_values=np.array([], dtype="int64"),
        )

    signal_dates = pd.to_datetime(universe["signal_date"]).dt.normalize()
    exit_dates = pd.to_datetime(universe["exit_date"]).dt.normalize()
    symbols = universe["market"].astype("string").fillna("") + "|" + universe["symbol"].astype("string").fillna("")
    symbol_codes, _ = pd.factorize(symbols, sort=False)
    years = signal_dates.dt.year
    year_codes, year_values = pd.factorize(years, sort=True)
    fillable = {
        assumption: universe.get(f"fillable_{assumption}", pd.Series(False, index=universe.index))
        .fillna(False)
        .astype(bool)
        .to_numpy()
        for assumption in FILL_ASSUMPTIONS
    }
    return PreparedUniverse(
        signal_ord=signal_dates.map(pd.Timestamp.toordinal).to_numpy(dtype="int64"),
        exit_ord=exit_dates.map(pd.Timestamp.toordinal).to_numpy(dtype="int64"),
        market=universe["market"].astype("string").fillna("").to_numpy(dtype=object),
        turnover=pd.to_numeric(universe["day0_turnover_twd"], errors="coerce").to_numpy(dtype=float),
        volume_ratio=pd.to_numeric(universe["volume_ratio_20d"], errors="coerce").to_numpy(dtype=float),
        consecutive_count=pd.to_numeric(universe["consecutive_limit_up_count"], errors="coerce").to_numpy(dtype=float),
        first_limit=universe["first_limit_up_in_sequence"].fillna(False).astype(bool).to_numpy(),
        price=pd.to_numeric(universe["day0_close"], errors="coerce").to_numpy(dtype=float),
        fill_score=pd.to_numeric(universe["fill_quality_score"], errors="coerce").to_numpy(dtype=float),
        fillable=fillable,
        net_pnl=pd.to_numeric(universe["net_pnl"], errors="coerce").fillna(0.0).to_numpy(dtype=float),
        net_return=pd.to_numeric(universe["net_return"], errors="coerce").to_numpy(dtype=float),
        buy_notional=pd.to_numeric(universe["buy_notional"], errors="coerce").fillna(0.0).to_numpy(dtype=float),
        symbol_code=symbol_codes.astype("int64"),
        year_code=year_codes.astype("int64"),
        year_values=np.asarray(year_values, dtype="int64"),
    )


def filter_prepared_universe(
    prepared: PreparedUniverse,
    *,
    params: OvernightWFParameters,
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
) -> np.ndarray:
    if prepared.signal_ord.size == 0:
        return np.array([], dtype=bool)
    start_ord = pd.Timestamp(start).normalize().toordinal()
    end_ord = pd.Timestamp(end).normalize().toordinal()
    mask = (prepared.signal_ord >= start_ord) & (prepared.signal_ord <= end_ord)
    if params.market != "BOTH":
        mask &= prepared.market == params.market
    mask &= prepared.turnover >= params.min_turnover_twd
    mask &= prepared.volume_ratio >= params.min_volume_ratio_20d
    mask &= prepared.consecutive_count <= params.max_consecutive_limit_ups
    if params.only_first_limit_up:
        mask &= prepared.first_limit
    mask &= prepared.price >= params.min_price
    mask &= prepared.price <= params.max_price
    mask &= prepared.fill_score >= params.min_fill_quality_score
    mask &= prepared.fillable[params.fill_assumption]
    return np.nan_to_num(mask, nan=False).astype(bool)


def evaluate_prepared_metrics(prepared: PreparedUniverse, mask: np.ndarray) -> dict[str, float]:
    count = int(mask.sum())
    if count == 0:
        return {column: 0.0 for column in METRIC_COLUMNS}

    net_pnl = prepared.net_pnl[mask]
    net_return = prepared.net_return[mask]
    buy_notional = prepared.buy_notional[mask]
    gains = net_pnl[net_pnl > 0].sum()
    losses = abs(net_pnl[net_pnl < 0].sum())
    if losses == 0 and gains > 0:
        profit_factor = float("inf")
    elif losses == 0:
        profit_factor = 0.0
    else:
        profit_factor = float(gains / losses)

    total_buy_notional = float(buy_notional.sum())
    max_drawdown = max_drawdown_from_arrays(prepared.exit_ord[mask], net_pnl)
    max_drawdown_pct = max_drawdown / total_buy_notional if total_buy_notional > 0 else 0.0
    return {
        "trades": count,
        "net_pnl": float(net_pnl.sum()),
        "avg_net_return": float(np.nanmean(net_return)),
        "median_net_return": float(np.nanmedian(net_return)),
        "win_rate": float((net_pnl > 0).mean()),
        "profit_factor": profit_factor,
        "max_drawdown": float(max_drawdown),
        "max_drawdown_pct": float(max_drawdown_pct),
        "total_buy_notional": total_buy_notional,
        "avg_turnover_twd": float(np.nanmean(prepared.turnover[mask])),
        "avg_volume_ratio_20d": float(np.nanmean(prepared.volume_ratio[mask])),
        "avg_fill_quality_score": float(np.nanmean(prepared.fill_score[mask])),
        "top10_symbol_trade_share": top10_symbol_trade_share_from_codes(prepared.symbol_code[mask]),
        "dominant_year_pnl_share": dominant_year_pnl_share_from_arrays(prepared.year_code[mask], net_pnl),
    }


def max_drawdown_from_arrays(exit_ord: np.ndarray, net_pnl: np.ndarray) -> float:
    if net_pnl.size == 0:
        return 0.0
    unique_dates, inverse = np.unique(exit_ord, return_inverse=True)
    daily_pnl = np.bincount(inverse, weights=net_pnl, minlength=len(unique_dates))
    equity = np.concatenate([[0.0], np.cumsum(daily_pnl)])
    drawdown = equity - np.maximum.accumulate(equity)
    return float(drawdown.min())


def top10_symbol_trade_share_from_codes(symbol_codes: np.ndarray) -> float:
    if symbol_codes.size == 0:
        return 0.0
    counts = np.bincount(symbol_codes[symbol_codes >= 0])
    if counts.size == 0:
        return 0.0
    top = np.sort(counts)[-10:].sum()
    return float(top / symbol_codes.size)


def dominant_year_pnl_share_from_arrays(year_codes: np.ndarray, net_pnl: np.ndarray) -> float:
    if year_codes.size == 0:
        return 0.0
    valid = year_codes >= 0
    unique_years = np.unique(year_codes[valid])
    if unique_years.size < 2:
        return 0.0
    pnl_by_year = np.bincount(year_codes[valid], weights=net_pnl[valid])
    positive_total = pnl_by_year[pnl_by_year > 0].sum()
    if positive_total <= 0:
        return 1.0
    return float(pnl_by_year.max() / positive_total)


def rank_train_configs(
    parameter_sets: Sequence[OvernightWFParameters],
    *,
    universe: pd.DataFrame,
    prepared_universe: PreparedUniverse | None = None,
    window: WalkForwardWindow,
    min_train_trades: int,
    show_progress: bool = False,
) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    prepared = prepared_universe if prepared_universe is not None else prepare_universe_arrays(universe)
    iterator = tqdm(parameter_sets, total=len(parameter_sets), disable=not show_progress, desc=f"{window.window_id} train")
    for params in iterator:
        mask = filter_prepared_universe(
            prepared,
            params=params,
            start=window.train_start,
            end=window.train_end,
        )
        metrics = evaluate_prepared_metrics(prepared, mask)
        penalty = calculate_overfit_penalty(metrics)
        score = calculate_robust_score(metrics, overfit_penalty=penalty)
        eligible = metrics["trades"] >= min_train_trades
        if not eligible:
            score = -np.inf
        ranked.append(
            {
                "params": params,
                "metrics": metrics,
                "score": score,
                "penalty": penalty,
                "eligible": eligible,
            }
        )
    return sorted(
        ranked,
        key=lambda item: (
            item["eligible"],
            item["score"],
            item["metrics"]["trades"],
            item["metrics"]["net_pnl"],
        ),
        reverse=True,
    )


def select_top_configs(ranked: Sequence[dict[str, Any]], *, top_n: int) -> list[dict[str, Any]]:
    eligible = [item for item in ranked if item["eligible"]]
    source = eligible if eligible else list(ranked)
    return source[:top_n]


def evaluate_trade_metrics(trades: pd.DataFrame) -> dict[str, float]:
    if trades.empty:
        return {column: 0.0 for column in METRIC_COLUMNS}

    net_pnl = pd.to_numeric(trades["net_pnl"], errors="coerce").fillna(0.0)
    net_return = pd.to_numeric(trades["net_return"], errors="coerce").dropna()
    buy_notional = pd.to_numeric(trades["buy_notional"], errors="coerce").fillna(0.0)
    gains = net_pnl[net_pnl > 0].sum()
    losses = abs(net_pnl[net_pnl < 0].sum())
    if losses == 0 and gains > 0:
        profit_factor = float("inf")
    elif losses == 0:
        profit_factor = 0.0
    else:
        profit_factor = float(gains / losses)

    max_drawdown = max_drawdown_by_exit_date(trades)
    total_buy_notional = float(buy_notional.sum())
    max_drawdown_pct = max_drawdown / total_buy_notional if total_buy_notional > 0 else 0.0
    top10_share = top10_symbol_trade_share(trades)
    dominant_year_share = dominant_year_pnl_share(trades)
    return {
        "trades": int(len(trades)),
        "net_pnl": float(net_pnl.sum()),
        "avg_net_return": float(net_return.mean()) if not net_return.empty else 0.0,
        "median_net_return": float(net_return.median()) if not net_return.empty else 0.0,
        "win_rate": float((net_pnl > 0).mean()) if len(net_pnl) else 0.0,
        "profit_factor": profit_factor,
        "max_drawdown": float(max_drawdown),
        "max_drawdown_pct": float(max_drawdown_pct),
        "total_buy_notional": total_buy_notional,
        "avg_turnover_twd": float(pd.to_numeric(trades["day0_turnover_twd"], errors="coerce").mean()),
        "avg_volume_ratio_20d": float(pd.to_numeric(trades["volume_ratio_20d"], errors="coerce").mean()),
        "avg_fill_quality_score": float(pd.to_numeric(trades["fill_quality_score"], errors="coerce").mean()),
        "top10_symbol_trade_share": top10_share,
        "dominant_year_pnl_share": dominant_year_share,
    }


def calculate_robust_score(metrics: dict[str, float], *, overfit_penalty: float) -> float:
    trades = metrics["trades"]
    if trades <= 0:
        return -np.inf
    profit_factor = metrics["profit_factor"]
    pf_score = 10.0 if np.isinf(profit_factor) else min(max(profit_factor, 0.0), 10.0)
    return float(
        metrics["avg_net_return"] * np.sqrt(trades)
        + 0.5 * metrics["median_net_return"]
        + 0.2 * pf_score
        - 0.5 * abs(metrics["max_drawdown_pct"])
        - overfit_penalty
    )


def calculate_overfit_penalty(metrics: dict[str, float]) -> float:
    penalty = 0.0
    trades = metrics["trades"]
    if trades < 50:
        penalty += (50 - trades) / 50.0
    if metrics["median_net_return"] <= 0:
        penalty += 0.5
    profit_factor = metrics["profit_factor"]
    if not np.isinf(profit_factor) and profit_factor < 1.2:
        penalty += 0.5
    top10_share = metrics["top10_symbol_trade_share"]
    if top10_share > 0.20:
        penalty += (top10_share - 0.20) * 2.0
    dominant_year_share = metrics["dominant_year_pnl_share"]
    if dominant_year_share > 0.70:
        penalty += (dominant_year_share - 0.70)
    return float(penalty)


def max_drawdown_by_exit_date(trades: pd.DataFrame) -> float:
    if trades.empty:
        return 0.0
    dated = trades.copy()
    dated["_pnl_date"] = pd.to_datetime(dated["exit_date"]).dt.normalize()
    daily = dated.groupby("_pnl_date", dropna=False)["net_pnl"].sum().sort_index()
    equity = pd.concat([pd.Series([0.0]), daily.reset_index(drop=True)], ignore_index=True).cumsum()
    drawdown = equity - equity.cummax()
    return float(drawdown.min())


def top10_symbol_trade_share(trades: pd.DataFrame) -> float:
    if trades.empty:
        return 0.0
    counts = trades.groupby(["market", "symbol"], dropna=False).size().sort_values(ascending=False)
    return float(counts.head(10).sum() / len(trades))


def dominant_year_pnl_share(trades: pd.DataFrame) -> float:
    if trades.empty:
        return 0.0
    years = pd.to_datetime(trades["entry_date"]).dt.year
    if years.nunique() < 2:
        return 0.0
    by_year = pd.to_numeric(trades["net_pnl"], errors="coerce").fillna(0.0).groupby(years).sum()
    positive_total = by_year[by_year > 0].sum()
    if positive_total <= 0:
        return 1.0
    return float(by_year.max() / positive_total)


def build_result_row(
    *,
    window: WalkForwardWindow,
    selected_rank: int,
    params: OvernightWFParameters,
    config_hash: str,
    train_metrics: dict[str, float],
    oos_metrics: dict[str, float],
    robust_score: float,
    overfit_penalty: float,
    eligible: bool,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "window_id": window.window_id,
        "train_start": window.train_start.date().isoformat(),
        "train_end": window.train_end.date().isoformat(),
        "test_start": window.test_start.date().isoformat(),
        "test_end": window.test_end.date().isoformat(),
        "selected_rank": selected_rank,
        "config_hash": config_hash,
        **params.payload(),
        "robust_score": robust_score,
        "overfit_penalty": overfit_penalty,
        "train_rank_eligible": bool(eligible),
    }
    row.update({f"train_{key}": value for key, value in train_metrics.items()})
    row.update({f"oos_{key}": value for key, value in oos_metrics.items()})
    return {column: row.get(column, np.nan) for column in RESULT_COLUMNS}


def annotate_oos_trades(
    trades: pd.DataFrame,
    *,
    window: WalkForwardWindow,
    selected_rank: int,
    params: OvernightWFParameters,
    config_hash: str,
) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame(columns=[*OOS_TRADE_EXTRA_COLUMNS, *trades.columns.tolist()])
    frame = trades.copy()
    frame.insert(0, "window_id", window.window_id)
    frame.insert(1, "selected_rank", selected_rank)
    frame.insert(2, "config_hash", config_hash)
    frame.insert(3, "test_start", window.test_start.date().isoformat())
    frame.insert(4, "test_end", window.test_end.date().isoformat())
    frame.insert(5, "market_param", params.market)
    frame.insert(6, "min_turnover_twd_param", params.min_turnover_twd)
    frame.insert(7, "min_volume_ratio_20d_param", params.min_volume_ratio_20d)
    frame.insert(8, "max_consecutive_limit_ups_param", params.max_consecutive_limit_ups)
    frame.insert(9, "only_first_limit_up_param", params.only_first_limit_up)
    frame.insert(10, "min_price_param", params.min_price)
    frame.insert(11, "max_price_param", params.max_price)
    frame.insert(12, "fill_assumption_param", params.fill_assumption)
    frame.insert(13, "min_fill_quality_score_param", params.min_fill_quality_score)
    return frame


def combine_oos_trades(frames: Sequence[pd.DataFrame]) -> pd.DataFrame:
    non_empty = [frame for frame in frames if not frame.empty]
    if not non_empty:
        return pd.DataFrame(columns=OOS_TRADE_EXTRA_COLUMNS)
    return pd.concat(non_empty, ignore_index=True)


def summarize_walk_forward(results: pd.DataFrame, oos_trades: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    overall_metrics = evaluate_trade_metrics(oos_trades) if not oos_trades.empty else {column: 0.0 for column in METRIC_COLUMNS}
    window_pnl = results.groupby("window_id", dropna=False)["oos_net_pnl"].sum() if not results.empty else pd.Series(dtype=float)
    positive_windows = int((window_pnl > 0).sum())
    total_windows = int(window_pnl.size)
    best = best_robust_config(results)
    rows.append(
        summary_row(
            summary_level="overall",
            group="all_selected_configs",
            metrics=overall_metrics,
            positive_oos_windows=positive_windows,
            total_windows=total_windows,
            details={
                "best_robust_config_hash": best.get("config_hash"),
                "best_robust_config": {column: best.get(column) for column in PARAMETER_COLUMNS if column != "config_hash"},
            },
        )
    )

    if not oos_trades.empty:
        frame = oos_trades.copy()
        frame["_year"] = pd.to_datetime(frame["entry_date"]).dt.year.astype(str)
        frame["_quarter"] = pd.to_datetime(frame["entry_date"]).dt.to_period("Q").astype(str)
        for year, group in frame.groupby("_year", dropna=False):
            rows.append(summary_row(summary_level="by_year", group=str(year), metrics=evaluate_trade_metrics(group)))
        for quarter, group in frame.groupby("_quarter", dropna=False):
            rows.append(summary_row(summary_level="by_quarter", group=str(quarter), metrics=evaluate_trade_metrics(group)))

    if not results.empty:
        total_selections = len(results)
        for column in STABILITY_PARAMETER_COLUMNS:
            counts = results[column].value_counts(dropna=False)
            for value, count in counts.items():
                rows.append(
                    summary_row(
                        summary_level=f"parameter_stability:{column}",
                        group=str(value),
                        metrics=None,
                        selection_count=int(count),
                        selection_share=float(count / total_selections),
                    )
                )

    return pd.DataFrame(rows, columns=SUMMARY_COLUMNS)


def summary_row(
    *,
    summary_level: str,
    group: str,
    metrics: dict[str, float] | None,
    positive_oos_windows: int | None = None,
    total_windows: int | None = None,
    selection_count: int | None = None,
    selection_share: float | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metric_values = metrics or {}
    return {
        "summary_level": summary_level,
        "group": group,
        "trades": metric_values.get("trades", np.nan),
        "net_pnl": metric_values.get("net_pnl", np.nan),
        "avg_net_return": metric_values.get("avg_net_return", np.nan),
        "median_net_return": metric_values.get("median_net_return", np.nan),
        "profit_factor": metric_values.get("profit_factor", np.nan),
        "max_drawdown": metric_values.get("max_drawdown", np.nan),
        "positive_oos_windows": positive_oos_windows,
        "total_windows": total_windows,
        "selection_count": selection_count,
        "selection_share": selection_share,
        "details_json": json.dumps(details or {}, ensure_ascii=False, default=json_default),
    }


def best_robust_config(results: pd.DataFrame) -> dict[str, Any]:
    if results.empty:
        return {}
    ordered = results.sort_values(
        ["robust_score", "oos_net_pnl", "oos_trades"],
        ascending=[False, False, False],
    )
    return ordered.iloc[0].to_dict()


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


def make_config_hash(params: OvernightWFParameters) -> str:
    raw = json.dumps(params.payload(), sort_keys=True, default=json_default)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def normalize_grid_markets(markets: Sequence[str]) -> list[str]:
    values = [value.upper().strip() for value in markets]
    invalid = sorted(set(values) - {"TWSE", "TPEX", "BOTH"})
    if invalid:
        raise ValueError(f"markets contains invalid values: {invalid}")
    return values


def normalize_fill_assumption(value: str) -> str:
    normalized = value.lower().strip()
    if normalized not in FILL_ASSUMPTIONS:
        raise ValueError(f"fill_assumption must be one of {FILL_ASSUMPTIONS}, got {value!r}")
    return normalized


def json_default(value: Any) -> Any:
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, np.generic):
        return value.item()
    if pd.isna(value):
        return None
    return value


def validate_inputs(
    *,
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
    first_test_start: str | pd.Timestamp,
    test_months: int,
    top_n: int,
    min_train_trades: int,
    fixed_notional_twd: float,
    commission_rate: float,
    commission_discount: float,
    sell_tax_rate: float,
    slippage_bps_per_side: float,
    minimum_commission_twd: float,
    max_configs: int | None,
) -> None:
    if pd.Timestamp(end) < pd.Timestamp(start):
        raise ValueError("end must be on or after start")
    if pd.Timestamp(first_test_start) <= pd.Timestamp(start):
        raise ValueError("first_test_start must be after start")
    if test_months <= 0:
        raise ValueError("test_months must be positive")
    if top_n <= 0:
        raise ValueError("top_n must be positive")
    if min_train_trades < 0:
        raise ValueError("min_train_trades must be non-negative")
    for name, value in {
        "fixed_notional_twd": fixed_notional_twd,
        "commission_rate": commission_rate,
        "commission_discount": commission_discount,
        "sell_tax_rate": sell_tax_rate,
        "slippage_bps_per_side": slippage_bps_per_side,
        "minimum_commission_twd": minimum_commission_twd,
    }.items():
        if value < 0:
            raise ValueError(f"{name} must be non-negative")
    if fixed_notional_twd <= 0:
        raise ValueError("fixed_notional_twd must be positive")
    if max_configs is not None and max_configs <= 0:
        raise ValueError("max_configs must be positive when provided")


def default_report_dir() -> Path:
    return get_settings().project_root / "reports"


def _parse_csv_floats(value: str | None, default: Sequence[float]) -> list[float]:
    if value is None:
        return list(default)
    return [float(part.strip()) for part in value.split(",") if part.strip()]


def _parse_csv_ints(value: str | None, default: Sequence[int]) -> list[int]:
    if value is None:
        return list(default)
    return [int(part.strip()) for part in value.split(",") if part.strip()]


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


def print_walk_forward_summary(summary: pd.DataFrame, results: pd.DataFrame) -> None:
    overall = summary[summary["summary_level"].eq("overall")]
    if overall.empty:
        print("\nclosed-limit-up overnight walk-forward: no OOS trades")
        return
    row = overall.iloc[0]
    print("\nclosed-limit-up overnight walk-forward")
    print(f"total OOS trades: {int(row['trades'])}")
    print(f"total OOS net PnL: {row['net_pnl']:.2f}")
    print(
        "OOS net return: "
        f"mean {row['avg_net_return']:.6f}, "
        f"median {row['median_net_return']:.6f}, "
        f"profit factor {row['profit_factor']:.4f}"
    )
    print(f"OOS max drawdown: {row['max_drawdown']:.2f}")
    print(f"positive OOS windows: {int(row['positive_oos_windows'])}/{int(row['total_windows'])}")

    if not results.empty:
        stability = []
        for column in STABILITY_PARAMETER_COLUMNS:
            top = results[column].value_counts(dropna=False).head(3)
            stability.append(f"{column}: " + ", ".join(f"{idx} ({count})" for idx, count in top.items()))
        print("\nselected parameter stability")
        print("\n".join(stability))


def main() -> None:
    parser = argparse.ArgumentParser(description="Walk-forward validation for closed-limit-up overnight strategy")
    parser.add_argument("--db", type=Path, default=get_settings().db_path, help="DuckDB path")
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=DEFAULT_END)
    parser.add_argument("--output-dir", type=Path, default=default_report_dir())
    parser.add_argument("--first-test-start", default=DEFAULT_FIRST_TEST_START)
    parser.add_argument("--test-months", type=int, default=DEFAULT_TEST_MONTHS)
    parser.add_argument("--top-n", type=int, default=DEFAULT_TOP_N)
    parser.add_argument("--min-train-trades", type=int, default=1)
    parser.add_argument("--fixed-notional-twd", type=float, default=DEFAULT_FIXED_NOTIONAL_TWD)
    parser.add_argument("--commission-rate", type=float, default=0.001425)
    parser.add_argument("--commission-discount", type=float, default=0.28)
    parser.add_argument("--sell-tax-rate", type=float, default=0.003)
    parser.add_argument("--slippage-bps-per-side", type=float, default=5.0)
    parser.add_argument("--minimum-commission-twd", type=float, default=20.0)
    parser.add_argument(
        "--skip-build-events",
        action="store_true",
        help="Use existing event_candidates instead of rebuilding the full walk-forward window",
    )
    parser.add_argument("--no-progress", action="store_true")
    parser.add_argument("--markets", help="Comma-separated override, e.g. TWSE,TPEX,BOTH")
    parser.add_argument("--min-turnover-values", help="Comma-separated override")
    parser.add_argument("--min-volume-ratio-values", help="Comma-separated override")
    parser.add_argument("--max-consecutive-limit-up-values", help="Comma-separated override")
    parser.add_argument("--only-first-limit-up-values", help="Comma-separated booleans")
    parser.add_argument("--min-price-values", help="Comma-separated override")
    parser.add_argument("--max-price-values", help="Comma-separated override")
    parser.add_argument("--fill-assumptions", help="Comma-separated override, e.g. optimistic,moderate")
    parser.add_argument("--min-fill-quality-score-values", help="Comma-separated override")
    parser.add_argument("--max-configs", type=int, help="Optional cap for debugging")
    args = parser.parse_args()

    results, selected_configs, oos_trades, summary = run_walk_forward_closed_limit_up_overnight(
        db_path=args.db,
        start=args.start,
        end=args.end,
        output_dir=args.output_dir,
        first_test_start=args.first_test_start,
        test_months=args.test_months,
        top_n=args.top_n,
        min_train_trades=args.min_train_trades,
        fixed_notional_twd=args.fixed_notional_twd,
        commission_rate=args.commission_rate,
        commission_discount=args.commission_discount,
        sell_tax_rate=args.sell_tax_rate,
        slippage_bps_per_side=args.slippage_bps_per_side,
        minimum_commission_twd=args.minimum_commission_twd,
        rebuild_events=not args.skip_build_events,
        show_progress=not args.no_progress,
        markets=_parse_csv_strings(args.markets, GRID_MARKETS, upper=True),
        min_turnover_values=_parse_csv_floats(args.min_turnover_values, GRID_MIN_TURNOVER_TWD),
        min_volume_ratio_values=_parse_csv_floats(args.min_volume_ratio_values, GRID_MIN_VOLUME_RATIO_20D),
        max_consecutive_limit_up_values=_parse_csv_ints(
            args.max_consecutive_limit_up_values,
            GRID_MAX_CONSECUTIVE_LIMIT_UPS,
        ),
        only_first_limit_up_values=_parse_csv_bools(args.only_first_limit_up_values, GRID_ONLY_FIRST_LIMIT_UP),
        min_price_values=_parse_csv_floats(args.min_price_values, GRID_MIN_PRICE),
        max_price_values=_parse_csv_floats(args.max_price_values, GRID_MAX_PRICE),
        fill_assumptions=_parse_csv_strings(args.fill_assumptions, FILL_ASSUMPTIONS),
        min_fill_quality_score_values=_parse_csv_floats(
            args.min_fill_quality_score_values,
            DEFAULT_FILL_QUALITY_THRESHOLDS[:-1],
        ),
        max_configs=args.max_configs,
    )

    print(f"Wrote {len(results)} rows to {args.output_dir / 'walk_forward_closed_limit_up_overnight_results.csv'}")
    print(
        f"Wrote {len(selected_configs)} rows to "
        f"{args.output_dir / 'walk_forward_closed_limit_up_overnight_selected_configs.csv'}"
    )
    print(f"Wrote {len(oos_trades)} rows to {args.output_dir / 'walk_forward_closed_limit_up_overnight_oos_trades.csv'}")
    print(f"Wrote {len(summary)} rows to {args.output_dir / 'walk_forward_closed_limit_up_overnight_summary.csv'}")
    print_walk_forward_summary(summary, results)


if __name__ == "__main__":
    main()
