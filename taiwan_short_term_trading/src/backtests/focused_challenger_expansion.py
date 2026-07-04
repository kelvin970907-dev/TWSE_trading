"""Focused full-grid expansion around the clean 4f6aa857 challenger.

This module keeps the closed-limit-up overnight hypothesis locked and explores
market, liquidity, regime, ranking, momentum, and sector-handling choices under
the same portfolio assumptions used by the current paper/live champion.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from itertools import islice, product
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
from tqdm.auto import tqdm

from config.settings import get_settings
from src.backtests.portfolio_sim_closed_limit_up import markdown_table, simulate_portfolio
from src.backtests.strategy_tournament import (
    CHAMPION_NAME,
    INDUSTRIAL_SECTOR,
    TECH_SECTOR,
    StrategyConfig,
    StrategyResult,
    build_portfolio_candidates_for_strategy,
    calculate_tournament_metrics,
    champion_config,
    combine_with_champion,
    config_to_row,
    daily_return_correlation,
    ensure_event_candidates,
    infer_tournament_dates,
    load_tournament_universe,
    portfolio_config_from_strategy,
    strategy_config_hash,
    top_share,
)


FAST_RESULTS_OUTPUT = "focused_challenger_fast_results.csv"
FAST_TOP_OUTPUT = "focused_challenger_fast_top.csv"
EXACT_RESULTS_OUTPUT = "focused_challenger_exact_results.csv"
EXACT_TOP_OUTPUT = "focused_challenger_exact_top.csv"
EXACT_COMBINED_OUTPUT = "focused_challenger_exact_combined.csv"
TWO_STAGE_REPORT_OUTPUT = "focused_challenger_two_stage_report.md"

MarketValue = Literal["TPEX", "TWSE", "BOTH"]
RegimeValue = Literal["none", "not_bear", "avoid_weak_day", "bull_only"]
RankingValue = Literal["fill_quality_score", "day0_turnover_twd", "volume_ratio_20d", "composite_score"]
MomentumCap = Literal["none", "30_80", "20_60", "10_40"]
WeakSectorHandling = Literal[
    "avoid_healthcare_materials",
    "avoid_healthcare_materials_semiconductor_cap_25",
    "technology_industrials_only",
]
ExpansionStage = Literal["fast", "exact", "two_stage"]

GRID_MARKETS: tuple[MarketValue, ...] = ("TPEX", "TWSE", "BOTH")
GRID_TURNOVER = (100_000_000.0, 200_000_000.0, 300_000_000.0, 500_000_000.0, 1_000_000_000.0)
GRID_VOLUME_RATIO = (1.2, 1.5, 2.0, 3.0, 5.0)
GRID_REGIMES: tuple[RegimeValue, ...] = ("none", "not_bear", "avoid_weak_day", "bull_only")
GRID_RANKING: tuple[RankingValue, ...] = (
    "fill_quality_score",
    "day0_turnover_twd",
    "volume_ratio_20d",
    "composite_score",
)
GRID_MOMENTUM_CAPS: tuple[MomentumCap, ...] = ("none", "30_80", "20_60", "10_40")
GRID_WEAK_SECTOR_HANDLING: tuple[WeakSectorHandling, ...] = (
    "avoid_healthcare_materials",
    "avoid_healthcare_materials_semiconductor_cap_25",
    "technology_industrials_only",
)
INITIAL_CAPITAL_TWD = 1_000_000.0
TARGET_NOTIONAL_TWD = 300_000.0
STATIC_FAST_NOTIONAL_TWD = 200_000.0
BOARD_LOT_SIZE = 1000
MAX_POSITIONS_PER_DAY = 5
COMMISSION_RATE = 0.001425
COMMISSION_DISCOUNT = 0.28
NORMAL_SELL_TAX_RATE = 0.003
SLIPPAGE_BPS_PER_SIDE = 5.0
MINIMUM_COMMISSION_TWD = 20.0


@dataclass(frozen=True)
class FocusedGridConfig:
    """One focused expansion configuration."""

    market: MarketValue
    min_turnover_twd: float
    min_volume_ratio_20d: float
    market_regime_filter: RegimeValue
    ranking_method: RankingValue
    momentum_cap: MomentumCap
    weak_sector_handling: WeakSectorHandling

    def payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FocusedExpansionResult:
    """Focused expansion outputs."""

    champion: StrategyResult
    results: pd.DataFrame
    top: pd.DataFrame
    combined: pd.DataFrame
    report_path: Path
    stage: ExpansionStage = "two_stage"
    fast_results: pd.DataFrame = field(default_factory=pd.DataFrame)
    fast_top: pd.DataFrame = field(default_factory=pd.DataFrame)
    exact_results: pd.DataFrame = field(default_factory=pd.DataFrame)
    exact_top: pd.DataFrame = field(default_factory=pd.DataFrame)
    exact_combined: pd.DataFrame = field(default_factory=pd.DataFrame)
    timings: dict[str, float] = field(default_factory=dict)


def run_focused_challenger_expansion(
    *,
    db_path: Path | str,
    output_dir: Path | str | None = None,
    start: str | pd.Timestamp | None = None,
    end: str | pd.Timestamp | None = None,
    rebuild_events: bool = False,
    max_configs: int | None = None,
    stage: ExpansionStage = "two_stage",
    finalists: int = 200,
    fast_output: Path | str | None = None,
    show_progress: bool = True,
) -> FocusedExpansionResult:
    """Run fast, exact, or two-stage focused challenger expansion."""

    if max_configs is not None and max_configs <= 0:
        raise ValueError("max_configs must be positive when provided")
    if stage not in {"fast", "exact", "two_stage"}:
        raise ValueError("stage must be fast, exact, or two_stage")
    if finalists <= 0:
        raise ValueError("finalists must be positive")

    run_started = time.perf_counter()
    timings: dict[str, float] = {}
    report_dir = Path(output_dir) if output_dir is not None else get_settings().project_root / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    start_ts, end_ts = infer_tournament_dates(db_path=db_path, start=start, end=end, output_dir=report_dir)
    ensure_event_candidates(db_path=db_path, start=start_ts, end=end_ts, rebuild_events=rebuild_events)
    universe = load_tournament_universe(db_path=db_path, start=start_ts, end=end_ts)
    champion = evaluate_focused_strategy(champion_to_focused_config(), universe)

    configs = list(iter_focused_grid(max_configs=max_configs))

    fast_results = pd.DataFrame()
    fast_top = pd.DataFrame()
    exact_results = pd.DataFrame()
    exact_top = pd.DataFrame()
    exact_combined = pd.DataFrame()
    finalist_configs: list[FocusedGridConfig] = []

    if stage in {"fast", "two_stage"}:
        fast_started = time.perf_counter()
        fast_results = run_fast_prescreen(configs=configs, universe=universe, champion=champion, show_progress=show_progress)
        fast_top = select_focused_top(fast_results)
        timings["fast_seconds"] = time.perf_counter() - fast_started
        fast_path = Path(fast_output) if fast_output is not None else report_dir / FAST_RESULTS_OUTPUT
        fast_path.parent.mkdir(parents=True, exist_ok=True)
        fast_results.to_csv(fast_path, index=False)
        fast_top.to_csv(report_dir / FAST_TOP_OUTPUT, index=False)
        if stage == "two_stage":
            finalist_configs = select_finalist_configs(fast_results, configs=configs, finalists=finalists)

    if stage == "exact":
        finalist_configs = configs

    if stage in {"exact", "two_stage"}:
        exact_started = time.perf_counter()
        exact_results, exact_combined = run_exact_finalists(
            configs=finalist_configs,
            universe=universe,
            champion=champion,
            show_progress=show_progress,
        )
        exact_top = select_focused_top(exact_results)
        timings["exact_seconds"] = time.perf_counter() - exact_started
        exact_results.to_csv(report_dir / EXACT_RESULTS_OUTPUT, index=False)
        exact_top.to_csv(report_dir / EXACT_TOP_OUTPUT, index=False)
        exact_combined.to_csv(report_dir / EXACT_COMBINED_OUTPUT, index=False)

    timings["total_seconds"] = time.perf_counter() - run_started
    primary_results = exact_results if stage in {"exact", "two_stage"} else fast_results
    primary_top = exact_top if stage in {"exact", "two_stage"} else fast_top
    primary_combined = exact_combined if stage in {"exact", "two_stage"} else pd.DataFrame()
    report_text = build_two_stage_report(
        champion=champion,
        fast_results=fast_results,
        fast_top=fast_top,
        exact_results=exact_results,
        exact_top=exact_top,
        exact_combined=exact_combined,
        start=start_ts,
        end=end_ts,
        db_path=Path(db_path),
        max_configs=max_configs,
        stage=stage,
        finalists_requested=finalists,
        finalist_configs=finalist_configs,
        timings=timings,
    )

    report_path = report_dir / TWO_STAGE_REPORT_OUTPUT
    report_path.write_text(report_text, encoding="utf-8")
    return FocusedExpansionResult(
        champion=champion,
        results=primary_results,
        top=primary_top,
        combined=primary_combined,
        report_path=report_path,
        stage=stage,
        fast_results=fast_results,
        fast_top=fast_top,
        exact_results=exact_results,
        exact_top=exact_top,
        exact_combined=exact_combined,
        timings=timings,
    )


def iter_focused_grid(max_configs: int | None = None) -> list[FocusedGridConfig]:
    """Generate the requested focused grid."""

    configs = [
        FocusedGridConfig(
            market=market,
            min_turnover_twd=turnover,
            min_volume_ratio_20d=volume_ratio,
            market_regime_filter=regime,
            ranking_method=ranking,
            momentum_cap=momentum,
            weak_sector_handling=sector_handling,
        )
        for market, turnover, volume_ratio, regime, ranking, momentum, sector_handling in product(
            GRID_MARKETS,
            GRID_TURNOVER,
            GRID_VOLUME_RATIO,
            GRID_REGIMES,
            GRID_RANKING,
            GRID_MOMENTUM_CAPS,
            GRID_WEAK_SECTOR_HANDLING,
        )
    ]
    if max_configs is not None:
        return list(islice(configs, max_configs))
    return configs


def champion_to_focused_config() -> FocusedGridConfig:
    champion = champion_config()
    return FocusedGridConfig(
        market=champion.market,  # type: ignore[arg-type]
        min_turnover_twd=champion.min_turnover_twd,
        min_volume_ratio_20d=champion.min_volume_ratio_20d,
        market_regime_filter=champion.market_regime_filter,  # type: ignore[arg-type]
        ranking_method=champion.ranking_method,  # type: ignore[arg-type]
        momentum_cap="none",
        weak_sector_handling="avoid_healthcare_materials",
    )


def to_strategy_config(focused_config: FocusedGridConfig) -> StrategyConfig:
    """Map a focused grid row to the tournament StrategyConfig."""

    prior_5d_max, prior_20d_max = momentum_bounds(focused_config.momentum_cap)
    sector_filter = (
        "technology_industrials_only"
        if focused_config.weak_sector_handling == "technology_industrials_only"
        else "avoid_healthcare_materials"
    )
    family = "closed_limit_up_overnight"
    if focused_config.market_regime_filter != "none":
        family = "closed_limit_up_with_market_regime_filter"
    elif focused_config.momentum_cap != "none":
        family = "closed_limit_up_with_prior_momentum_filter"
    elif focused_config.weak_sector_handling == "technology_industrials_only":
        family = "closed_limit_up_with_sector_filter"

    return StrategyConfig(
        strategy_name=f"focused_challenger_{focused_config_hash(focused_config)}",
        family=family,  # type: ignore[arg-type]
        event_type="closed_limit_up",
        market=focused_config.market,
        entry_exit_rule="day0_close_to_day1_open",
        min_turnover_twd=focused_config.min_turnover_twd,
        min_volume_ratio_20d=focused_config.min_volume_ratio_20d,
        min_price=10.0,
        max_price=100.0,
        max_consecutive_limit_ups=3,
        fill_assumption="moderate",
        min_fill_quality_score=60.0,
        sector_filter=sector_filter,  # type: ignore[arg-type]
        market_regime_filter=focused_config.market_regime_filter,
        prior_5d_return_max=prior_5d_max,
        prior_20d_return_max=prior_20d_max,
        ranking_method=focused_config.ranking_method,
        initial_capital_twd=1_000_000.0,
        max_positions_per_day=5,
        fixed_notional_twd=300_000.0,
        max_notional_per_symbol_pct=0.20,
        max_notional_per_sector_pct=0.70,
        max_notional_per_industry_pct=0.25
        if focused_config.weak_sector_handling == "avoid_healthcare_materials_semiconductor_cap_25"
        else 0.35,
        max_trades_per_symbol_per_month=5,
    )


def run_fast_prescreen(
    *,
    configs: list[FocusedGridConfig],
    universe: pd.DataFrame,
    champion: StrategyResult,
    show_progress: bool,
) -> pd.DataFrame:
    """Evaluate configs with fast approximate trade-level portfolio assumptions."""

    base = prepare_fast_universe(universe)
    iterator = tqdm(configs, desc="fast pre-screen", leave=False) if show_progress else configs
    rows: list[dict[str, Any]] = []
    for config in iterator:
        selected, candidate_count = select_fast_trades(base, config)
        row = build_fast_result_row(
            focused_config=config,
            selected=selected,
            candidate_count=candidate_count,
            champion=champion,
        )
        rows.append(row)
    return pd.DataFrame(rows)


def prepare_fast_universe(universe: pd.DataFrame) -> pd.DataFrame:
    """Precompute locked closed-limit-up overnight trade economics."""

    columns = [
        "_diagnostic_row_id",
        "event_id",
        "symbol",
        "name",
        "market",
        "sector",
        "industry",
        "trade_date",
        "next_trade_date",
        "day0_close",
        "day0_turnover_twd",
        "volume_ratio_20d",
        "fill_quality_score",
        "fillable_moderate",
        "consecutive_limit_up_count",
        "prior_5d_return",
        "prior_20d_return",
        "bull_regime",
        "bear_regime",
        "weak_market_day",
        "index_data_available",
        "day1_open",
        "turnover_score",
        "volume_ratio_score",
        "composite_score",
    ]
    if universe.empty:
        return pd.DataFrame(columns=columns)
    frame = universe[universe["event_type"].astype("string").eq("closed_limit_up")].copy()
    if frame.empty:
        return pd.DataFrame(columns=columns)
    for column in columns:
        if column not in frame.columns:
            frame[column] = np.nan
    frame["signal_date"] = pd.to_datetime(frame["trade_date"], errors="coerce").dt.normalize()
    frame["exit_date"] = pd.to_datetime(frame["next_trade_date"], errors="coerce").dt.normalize()
    frame["entry_price"] = pd.to_numeric(frame["day0_close"], errors="coerce")
    frame["exit_price"] = pd.to_numeric(frame["day1_open"], errors="coerce")
    frame["day0_turnover_twd"] = pd.to_numeric(frame["day0_turnover_twd"], errors="coerce")
    frame["volume_ratio_20d"] = pd.to_numeric(frame["volume_ratio_20d"], errors="coerce")
    frame["fill_quality_score"] = pd.to_numeric(frame["fill_quality_score"], errors="coerce")
    frame["consecutive_limit_up_count"] = pd.to_numeric(frame["consecutive_limit_up_count"], errors="coerce")
    frame["prior_5d_return"] = pd.to_numeric(frame["prior_5d_return"], errors="coerce")
    frame["prior_20d_return"] = pd.to_numeric(frame["prior_20d_return"], errors="coerce")
    frame["market"] = frame["market"].astype("string").str.upper().str.strip()
    frame["sector"] = frame["sector"].astype("string").fillna("").str.strip()
    frame["industry"] = frame["industry"].astype("string").fillna("").str.strip()

    raw_shares = np.floor(STATIC_FAST_NOTIONAL_TWD / frame["entry_price"].replace(0, np.nan))
    frame["shares"] = (np.floor(raw_shares / BOARD_LOT_SIZE) * BOARD_LOT_SIZE).fillna(0).astype(int)
    frame = frame[
        (frame["entry_price"] > 0)
        & (frame["exit_price"] > 0)
        & (frame["shares"] >= BOARD_LOT_SIZE)
        & frame["signal_date"].notna()
        & frame["exit_date"].notna()
    ].copy()
    if frame.empty:
        return frame

    frame["buy_notional"] = frame["entry_price"] * frame["shares"]
    frame["sell_notional"] = frame["exit_price"] * frame["shares"]
    effective_commission = COMMISSION_RATE * COMMISSION_DISCOUNT
    frame["buy_commission"] = np.maximum(frame["buy_notional"] * effective_commission, MINIMUM_COMMISSION_TWD)
    frame["sell_commission"] = np.maximum(frame["sell_notional"] * effective_commission, MINIMUM_COMMISSION_TWD)
    frame["sell_tax"] = frame["sell_notional"] * NORMAL_SELL_TAX_RATE
    frame["slippage_cost"] = (frame["buy_notional"] + frame["sell_notional"]) * (SLIPPAGE_BPS_PER_SIDE / 10_000.0)
    frame["total_cost"] = frame["buy_commission"] + frame["sell_commission"] + frame["sell_tax"] + frame["slippage_cost"]
    frame["gross_pnl"] = frame["sell_notional"] - frame["buy_notional"]
    frame["net_pnl"] = frame["gross_pnl"] - frame["total_cost"]
    frame["gross_return"] = frame["gross_pnl"] / frame["buy_notional"]
    frame["net_return"] = frame["net_pnl"] / frame["buy_notional"]
    frame["trade_id"] = frame["event_id"].astype("string") + ":fast"
    return frame.reset_index(drop=True)


def select_fast_trades(base: pd.DataFrame, config: FocusedGridConfig) -> tuple[pd.DataFrame, int]:
    """Apply fast filters and approximate max positions per day by ranking."""

    if base.empty:
        return base.copy(), 0
    frame = base if {"signal_date", "exit_date"}.issubset(base.columns) else ensure_fast_date_columns(base)
    mask = pd.Series(True, index=frame.index)
    if config.market != "BOTH":
        mask &= frame["market"].eq(config.market)
    mask &= frame["day0_turnover_twd"] >= config.min_turnover_twd
    mask &= frame["volume_ratio_20d"] >= config.min_volume_ratio_20d
    mask &= frame["entry_price"].between(10.0, 100.0, inclusive="both")
    mask &= frame["consecutive_limit_up_count"] <= 3
    mask &= frame["fillable_moderate"].fillna(False).astype(bool)
    mask &= frame["fill_quality_score"] >= 60.0

    if config.weak_sector_handling in {
        "avoid_healthcare_materials",
        "avoid_healthcare_materials_semiconductor_cap_25",
    }:
        mask &= ~frame["sector"].isin({"Healthcare", "Materials"})
    elif config.weak_sector_handling == "technology_industrials_only":
        mask &= frame["sector"].isin({TECH_SECTOR, INDUSTRIAL_SECTOR})

    if config.market_regime_filter != "none":
        index_available = frame["index_data_available"].fillna(False).astype(bool)
        mask &= index_available
        if config.market_regime_filter == "not_bear":
            mask &= ~frame["bear_regime"].fillna(False).astype(bool)
        elif config.market_regime_filter == "avoid_weak_day":
            mask &= ~frame["weak_market_day"].fillna(False).astype(bool)
        elif config.market_regime_filter == "bull_only":
            mask &= frame["bull_regime"].fillna(False).astype(bool)

    prior_5d_max, prior_20d_max = momentum_bounds(config.momentum_cap)
    if prior_5d_max is not None:
        mask &= frame["prior_5d_return"] <= prior_5d_max
    if prior_20d_max is not None:
        mask &= frame["prior_20d_return"] <= prior_20d_max

    candidates = frame[mask.fillna(False)].copy()
    candidate_count = int(len(candidates))
    if candidates.empty:
        return candidates, candidate_count
    if not {"signal_date", "exit_date"}.issubset(candidates.columns):
        candidates = ensure_fast_date_columns(candidates)
    rank_column = config.ranking_method
    if rank_column not in candidates.columns:
        candidates[rank_column] = np.nan
    candidates["_fast_rank"] = pd.to_numeric(candidates[rank_column], errors="coerce").fillna(-np.inf)
    candidates = candidates.sort_values(["signal_date", "_fast_rank", "symbol"], ascending=[True, False, True])
    if config.weak_sector_handling == "avoid_healthcare_materials_semiconductor_cap_25":
        selected = select_with_semiconductor_cap(candidates)
    else:
        selected = candidates.groupby("signal_date", group_keys=False).head(MAX_POSITIONS_PER_DAY)
    return selected.reset_index(drop=True), candidate_count


def ensure_fast_date_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """Ensure fast-stage frames carry normalized signal and exit dates."""

    output = frame.copy()
    if "signal_date" not in output.columns:
        source = output["trade_date"] if "trade_date" in output.columns else pd.Series(pd.NaT, index=output.index)
        output["signal_date"] = pd.to_datetime(source, errors="coerce").dt.normalize()
    else:
        output["signal_date"] = pd.to_datetime(output["signal_date"], errors="coerce").dt.normalize()
    if "exit_date" not in output.columns:
        source = output["next_trade_date"] if "next_trade_date" in output.columns else pd.Series(pd.NaT, index=output.index)
        output["exit_date"] = pd.to_datetime(source, errors="coerce").dt.normalize()
    else:
        output["exit_date"] = pd.to_datetime(output["exit_date"], errors="coerce").dt.normalize()
    return output


def select_with_semiconductor_cap(candidates: pd.DataFrame) -> pd.DataFrame:
    """Approximate a 25% Semiconductor cap as at most one same-day position."""

    frame = candidates.copy()
    is_semiconductor = frame["industry"].astype("string").eq("Semiconductor")
    semiconductor_rank = is_semiconductor.astype(int).groupby(frame["signal_date"]).cumsum()
    frame = frame[~(is_semiconductor & (semiconductor_rank > 1))].copy()
    day_rank = frame.groupby("signal_date", sort=False).cumcount()
    return frame[day_rank < MAX_POSITIONS_PER_DAY]


def build_fast_result_row(
    *,
    focused_config: FocusedGridConfig,
    selected: pd.DataFrame,
    candidate_count: int,
    champion: StrategyResult,
) -> dict[str, Any]:
    """Build one approximate fast-screen result row."""

    strategy_config = to_strategy_config(focused_config)
    metrics = calculate_fast_metrics(selected=selected, candidate_count=candidate_count, config=strategy_config)
    approximate_diversification = approximate_fast_diversification(metrics, champion)
    row = {
        **focused_config.payload(),
        "focused_config_hash": focused_config_hash(focused_config),
        **config_to_row(strategy_config),
        **metrics,
        "stage": "fast_approx",
        "is_approximate": True,
        "pnl_vs_champion": metrics["total_net_pnl"] - safe_metric(champion, "total_net_pnl"),
        "cagr_vs_champion": metrics["annualized_return"] - safe_metric(champion, "annualized_return"),
        "maxdd_vs_champion": metrics["max_drawdown_pct"] - safe_metric(champion, "max_drawdown_pct"),
        "pf_vs_champion": metrics["profit_factor"] - safe_metric(champion, "profit_factor"),
        "median_return_vs_champion": metrics["median_net_return_per_trade"]
        - safe_metric(champion, "median_net_return_per_trade"),
        "correlation_with_champion_daily_returns": np.nan,
        "combined_total_net_pnl": np.nan,
        "combined_final_equity": np.nan,
        "combined_cagr_like": np.nan,
        "combined_max_drawdown_twd": np.nan,
        "combined_max_drawdown_pct": np.nan,
        "combined_daily_sharpe_like": np.nan,
        "combined_improves_cagr": False,
        "combined_improves_drawdown": False,
        "diversification_score": approximate_diversification,
    }
    row["robust_score"] = calculate_robust_score(row)
    return row


def approximate_fast_diversification(metrics: dict[str, Any], champion: StrategyResult) -> float:
    """Cheap Stage-1 proxy for selecting possible diversifiers."""

    cagr_bonus = max(0.0, finite_float(metrics.get("annualized_return")) - safe_metric(champion, "annualized_return"))
    drawdown_bonus = max(0.0, finite_float(metrics.get("max_drawdown_pct")) - safe_metric(champion, "max_drawdown_pct"))
    market_mix_bonus = 0.25 if finite_float(metrics.get("twse_trades")) > 0 and finite_float(metrics.get("tpex_trades")) > 0 else 0.0
    consistency_bonus = finite_float(metrics.get("oos_quarterly_consistency")) * 0.25
    return float(cagr_bonus + drawdown_bonus + market_mix_bonus + consistency_bonus)


def calculate_fast_metrics(
    *,
    selected: pd.DataFrame,
    candidate_count: int,
    config: StrategyConfig,
) -> dict[str, Any]:
    """Approximate metrics from selected fast-screen trades."""

    selected = ensure_fast_date_columns(selected) if not selected.empty and not {"signal_date", "exit_date"}.issubset(selected.columns) else selected
    trades = int(len(selected))
    net_pnl = pd.to_numeric(selected.get("net_pnl", pd.Series(dtype=float)), errors="coerce").fillna(0.0)
    net_return = pd.to_numeric(selected.get("net_return", pd.Series(dtype=float)), errors="coerce")
    gains = net_pnl[net_pnl > 0].sum()
    losses = abs(net_pnl[net_pnl < 0].sum())
    final_equity = INITIAL_CAPITAL_TWD + float(net_pnl.sum())
    daily_pnl = fast_pnl_by_date(selected, "exit_date")
    maxdd_twd, maxdd_pct = approximate_drawdown_from_daily_pnl(daily_pnl)
    quarterly = fast_period_pnl(selected, period="Q")
    positive_quarters = int((pd.to_numeric(quarterly.get("net_pnl", pd.Series(dtype=float)), errors="coerce") > 0).sum())
    total_quarters = int(len(quarterly))
    daily_return = daily_pnl / INITIAL_CAPITAL_TWD if not daily_pnl.empty else pd.Series(dtype=float)
    market_split = market_split_metrics(selected)
    positions_per_day = selected.groupby("signal_date", sort=False).size() if trades else pd.Series(dtype=float)
    return {
        "strategy_name": config.strategy_name,
        "family": config.family,
        "candidate_signals": candidate_count,
        "trades": trades,
        "trade_fill_rate": trades / candidate_count if candidate_count else np.nan,
        "active_days": int(selected["signal_date"].nunique()) if not selected.empty else 0,
        "total_net_pnl": float(net_pnl.sum()),
        "final_equity": final_equity,
        "total_return": final_equity / INITIAL_CAPITAL_TWD - 1.0,
        "annualized_return": fast_annualized_from_daily_pnl(daily_pnl, final_equity),
        "avg_net_return_per_trade": series_mean(net_return),
        "median_net_return_per_trade": series_median(net_return),
        "trade_win_rate": float((net_pnl > 0).mean()) if trades else np.nan,
        "day_win_rate": float((daily_pnl > 0).mean()) if not daily_pnl.empty else np.nan,
        "profit_factor": profit_factor(net_pnl),
        "max_drawdown_twd": maxdd_twd,
        "max_drawdown_pct": maxdd_pct,
        "daily_return_mean": series_mean(daily_return),
        "daily_return_median": series_median(daily_return),
        "daily_sharpe_like": daily_sharpe(daily_return),
        "positive_quarters": positive_quarters,
        "total_quarters": total_quarters,
        "oos_quarterly_consistency": positive_quarters / total_quarters if total_quarters else np.nan,
        "worst_quarter": worst_period_label(quarterly),
        "worst_quarter_return": float(quarterly["period_return"].min()) if not quarterly.empty else np.nan,
        "symbol_concentration": top_share(selected, "symbol"),
        "sector_concentration": top_share(selected, "sector"),
        "industry_concentration": top_share(selected, "industry"),
        "top10_symbol_trade_share": top_n_share(selected, "symbol", 10),
        "capital_utilization": np.nan,
        "average_positions_per_signal_day": float(positions_per_day.mean()) if trades else 0.0,
        "monthly_returns_json": "[]",
        "quarterly_returns_json": frame_to_records_json(quarterly),
        "fill_quality_bucket_json": "[]",
        **market_split,
    }


def fast_pnl_by_date(selected: pd.DataFrame, date_column: str) -> pd.Series:
    """Vectorized daily PnL for approximate Stage-1 metrics."""

    if selected.empty or date_column not in selected.columns:
        return pd.Series(dtype=float)
    dates = pd.to_datetime(selected[date_column], errors="coerce").dt.normalize()
    pnl = pd.to_numeric(selected.get("net_pnl", pd.Series(dtype=float)), errors="coerce").fillna(0.0)
    return pnl.groupby(dates).sum().sort_index()


def fast_period_pnl(selected: pd.DataFrame, *, period: str) -> pd.DataFrame:
    """Approximate period returns directly from selected trade PnL."""

    columns = ["period", "start_equity", "end_equity", "period_return", "net_pnl", "positions_entered"]
    if selected.empty or "exit_date" not in selected.columns:
        return pd.DataFrame(columns=columns)
    dates = pd.to_datetime(selected["exit_date"], errors="coerce")
    labels = dates.dt.to_period(period).astype(str)
    pnl = pd.to_numeric(selected.get("net_pnl", pd.Series(dtype=float)), errors="coerce").fillna(0.0)
    grouped = pnl.groupby(labels).agg(["sum", "count"]).reset_index()
    if grouped.empty:
        return pd.DataFrame(columns=columns)
    grouped.columns = ["period", "net_pnl", "positions_entered"]
    grouped["start_equity"] = INITIAL_CAPITAL_TWD
    grouped["end_equity"] = INITIAL_CAPITAL_TWD + grouped["net_pnl"]
    grouped["period_return"] = grouped["net_pnl"] / INITIAL_CAPITAL_TWD
    return grouped[columns]


def approximate_drawdown_from_daily_pnl(daily_pnl: pd.Series) -> tuple[float, float]:
    """Approximate max drawdown from grouped daily PnL."""

    if daily_pnl.empty:
        return 0.0, 0.0
    equity = INITIAL_CAPITAL_TWD + pd.to_numeric(daily_pnl, errors="coerce").fillna(0.0).cumsum()
    running_max = equity.cummax()
    drawdown = equity - running_max
    drawdown_pct = drawdown / running_max.replace(0, np.nan)
    return float(drawdown.min()), float(drawdown_pct.min())


def fast_annualized_from_daily_pnl(daily_pnl: pd.Series, final_equity: float) -> float:
    """Approximate annualized return from grouped daily PnL dates."""

    if daily_pnl.empty or final_equity <= 0:
        return np.nan
    dates = pd.to_datetime(pd.Series(daily_pnl.index), errors="coerce").dropna()
    if dates.empty:
        return np.nan
    years = max((dates.max() - dates.min()).days / 365.25, 1 / 365.25)
    return float((final_equity / INITIAL_CAPITAL_TWD) ** (1.0 / years) - 1.0)


def fast_daily_equity(selected: pd.DataFrame) -> pd.DataFrame:
    """Build approximate daily equity from selected fast-screen trade PnL."""

    columns = [
        "date",
        "start_equity",
        "end_equity",
        "daily_return",
        "realized_pnl",
        "positions_entered",
        "gross_entry_notional",
        "gross_exposure_end",
    ]
    if selected.empty:
        return pd.DataFrame(
            [
                {
                    "date": pd.NaT,
                    "start_equity": INITIAL_CAPITAL_TWD,
                    "end_equity": INITIAL_CAPITAL_TWD,
                    "daily_return": 0.0,
                    "realized_pnl": 0.0,
                    "positions_entered": 0,
                    "gross_entry_notional": 0.0,
                    "gross_exposure_end": 0.0,
                }
            ],
            columns=columns,
        )
    frame = ensure_fast_date_columns(selected)
    grouped = frame.groupby("exit_date", dropna=False).agg(
        realized_pnl=("net_pnl", "sum"),
        positions_entered=("symbol", "count"),
        gross_entry_notional=("buy_notional", "sum"),
    )
    rows = []
    equity = INITIAL_CAPITAL_TWD
    for date, row in grouped.sort_index().iterrows():
        start_equity = equity
        equity += float(row["realized_pnl"])
        rows.append(
            {
                "date": date,
                "start_equity": start_equity,
                "end_equity": equity,
                "daily_return": equity / start_equity - 1.0 if start_equity else np.nan,
                "realized_pnl": float(row["realized_pnl"]),
                "positions_entered": int(row["positions_entered"]),
                "gross_entry_notional": float(row["gross_entry_notional"]),
                "gross_exposure_end": 0.0,
            }
        )
    return pd.DataFrame(rows, columns=columns)


def run_exact_finalists(
    *,
    configs: list[FocusedGridConfig],
    universe: pd.DataFrame,
    champion: StrategyResult,
    show_progress: bool,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Run the exact portfolio simulator for finalist configs only."""

    iterator = tqdm(configs, desc="exact finalist rerun", leave=False) if show_progress else configs
    result_rows: list[dict[str, Any]] = []
    combined_rows: list[dict[str, Any]] = []
    for focused_config in iterator:
        result = evaluate_focused_strategy(focused_config, universe)
        row = build_focused_result_row(focused_config, result, champion)
        combined = combine_with_champion(champion, result)
        row.update(
            {
                "stage": "exact",
                "is_approximate": False,
                "correlation_with_champion_daily_returns": combined["correlation_with_champion_daily_returns"],
                "combined_total_net_pnl": combined["combined_total_net_pnl"],
                "combined_final_equity": combined["combined_final_equity"],
                "combined_cagr_like": combined["combined_cagr_like"],
                "combined_max_drawdown_twd": combined["combined_max_drawdown_twd"],
                "combined_max_drawdown_pct": combined["combined_max_drawdown_pct"],
                "combined_daily_sharpe_like": combined["combined_daily_sharpe_like"],
                "combined_improves_cagr": combined["combined_improves_cagr"],
                "combined_improves_drawdown": combined["combined_improves_drawdown"],
                "diversification_score": combined["diversification_score"],
            }
        )
        row["robust_score"] = calculate_robust_score(row)
        result_rows.append(row)
        combined_rows.append({**focused_config.payload(), **combined, "robust_score": row["robust_score"]})
    return pd.DataFrame(result_rows), pd.DataFrame(combined_rows)


def select_finalist_configs(
    fast_results: pd.DataFrame,
    *,
    configs: list[FocusedGridConfig],
    finalists: int,
) -> list[FocusedGridConfig]:
    """Select de-duplicated exact finalists from multiple approximate rankings."""

    if fast_results.empty:
        return []
    configs_by_hash = {focused_config_hash(config): config for config in configs}
    selected_hashes: list[str] = []
    for column in ["robust_score", "total_net_pnl", "median_net_return_per_trade", "diversification_score"]:
        if column in fast_results.columns:
            selected_hashes.extend(
                fast_results.sort_values(column, ascending=False)["focused_config_hash"].head(100).astype(str).tolist()
            )
    anchor_hashes = [focused_config_hash(config) for config in known_compact_winner_configs()]
    selected_hashes.extend([anchor for anchor in anchor_hashes if anchor in configs_by_hash])
    deduped: list[str] = []
    seen: set[str] = set()
    for config_hash in selected_hashes:
        if config_hash in configs_by_hash and config_hash not in seen:
            seen.add(config_hash)
            deduped.append(config_hash)

    if len(deduped) > finalists:
        anchors = [config_hash for config_hash in anchor_hashes if config_hash in deduped]
        ranked = (
            fast_results[fast_results["focused_config_hash"].isin(deduped)]
            .sort_values("robust_score", ascending=False)["focused_config_hash"]
            .astype(str)
            .tolist()
        )
        limited: list[str] = []
        for config_hash in [*anchors, *ranked]:
            if config_hash not in limited:
                limited.append(config_hash)
            if len(limited) >= finalists:
                break
        deduped = limited
    return [configs_by_hash[config_hash] for config_hash in deduped]


def known_compact_winner_configs() -> list[FocusedGridConfig]:
    """Focused-grid equivalents for known compact winners 4f6aa857/f16ad211/08915384."""

    return [
        FocusedGridConfig(
            market="BOTH",
            min_turnover_twd=200_000_000.0,
            min_volume_ratio_20d=1.5,
            market_regime_filter="not_bear",
            ranking_method="fill_quality_score",
            momentum_cap="none",
            weak_sector_handling="avoid_healthcare_materials",
        ),
        FocusedGridConfig(
            market="BOTH",
            min_turnover_twd=200_000_000.0,
            min_volume_ratio_20d=1.5,
            market_regime_filter="none",
            ranking_method="fill_quality_score",
            momentum_cap="none",
            weak_sector_handling="avoid_healthcare_materials",
        ),
        FocusedGridConfig(
            market="BOTH",
            min_turnover_twd=200_000_000.0,
            min_volume_ratio_20d=1.5,
            market_regime_filter="none",
            ranking_method="fill_quality_score",
            momentum_cap="30_80",
            weak_sector_handling="avoid_healthcare_materials",
        ),
    ]


def evaluate_focused_strategy(focused_config: FocusedGridConfig, universe: pd.DataFrame) -> StrategyResult:
    """Evaluate one focused config and attach extra diagnostics."""

    strategy_config = to_strategy_config(focused_config)
    candidates = build_portfolio_candidates_for_strategy(universe, strategy_config)
    portfolio_config = portfolio_config_from_strategy(strategy_config)
    trades, daily_equity, base_metrics = simulate_portfolio(candidates, portfolio_config)
    metrics = calculate_tournament_metrics(
        trades=trades,
        daily_equity=daily_equity,
        candidates=candidates,
        base_metrics=base_metrics,
        config=strategy_config,
    )
    metrics.update(extra_focused_metrics(trades=trades, daily_equity=daily_equity))
    return StrategyResult(config=strategy_config, trades=trades, daily_equity=daily_equity, metrics=metrics)


def build_focused_result_row(
    focused_config: FocusedGridConfig,
    result: StrategyResult,
    champion: StrategyResult,
) -> dict[str, Any]:
    """Flatten focused config, strategy config, and result metrics."""

    row = {
        **focused_config.payload(),
        "focused_config_hash": focused_config_hash(focused_config),
        **config_to_row(result.config),
        **result.metrics,
    }
    row["strategy_config_hash"] = strategy_config_hash(result.config)
    row["pnl_vs_champion"] = safe_metric(result, "total_net_pnl") - safe_metric(champion, "total_net_pnl")
    row["cagr_vs_champion"] = safe_metric(result, "annualized_return") - safe_metric(champion, "annualized_return")
    row["maxdd_vs_champion"] = safe_metric(result, "max_drawdown_pct") - safe_metric(champion, "max_drawdown_pct")
    row["pf_vs_champion"] = safe_metric(result, "profit_factor") - safe_metric(champion, "profit_factor")
    row["median_return_vs_champion"] = safe_metric(result, "median_net_return_per_trade") - safe_metric(
        champion,
        "median_net_return_per_trade",
    )
    row["correlation_with_champion_daily_returns"] = daily_return_correlation(champion.daily_equity, result.daily_equity)
    return row


def extra_focused_metrics(*, trades: pd.DataFrame, daily_equity: pd.DataFrame) -> dict[str, Any]:
    """Compute focused-grid diagnostics not in the base tournament output."""

    monthly = period_returns(daily_equity, period="M")
    quarterly = period_returns(daily_equity, period="Q")
    market_split = market_split_metrics(trades)
    fill_bucket = fill_quality_bucket_metrics(trades)
    return {
        "industry_concentration": top_share(trades, "industry"),
        "top10_symbol_trade_share": top_n_share(trades, "symbol", 10),
        "worst_monthly_return": float(monthly["period_return"].min()) if not monthly.empty else np.nan,
        "worst_quarter": worst_period_label(quarterly),
        "worst_quarter_return": float(quarterly["period_return"].min()) if not quarterly.empty else np.nan,
        "monthly_returns_json": frame_to_records_json(monthly),
        "quarterly_returns_json": frame_to_records_json(quarterly),
        "fill_quality_bucket_json": json.dumps(fill_bucket, sort_keys=True),
        **market_split,
    }


def period_returns(daily_equity: pd.DataFrame, *, period: str) -> pd.DataFrame:
    """Calculate monthly or quarterly returns from daily equity rows."""

    columns = ["period", "start_equity", "end_equity", "period_return", "net_pnl", "positions_entered"]
    if daily_equity.empty or "date" not in daily_equity.columns:
        return pd.DataFrame(columns=columns)
    frame = daily_equity.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame = frame.dropna(subset=["date"])
    if frame.empty:
        return pd.DataFrame(columns=columns)
    frame["period"] = frame["date"].dt.to_period(period).astype(str)
    rows = []
    for label, group in frame.groupby("period", sort=True):
        start_equity = float(group["start_equity"].iloc[0])
        end_equity = float(group["end_equity"].iloc[-1])
        rows.append(
            {
                "period": label,
                "start_equity": start_equity,
                "end_equity": end_equity,
                "period_return": end_equity / start_equity - 1.0 if start_equity else np.nan,
                "net_pnl": end_equity - start_equity,
                "positions_entered": int(pd.to_numeric(group["positions_entered"], errors="coerce").fillna(0).sum()),
            }
        )
    return pd.DataFrame(rows, columns=columns)


def approximate_drawdown(daily_equity: pd.DataFrame) -> tuple[float, float]:
    """Return approximate max drawdown in TWD and percent."""

    if daily_equity.empty:
        return 0.0, 0.0
    equity = pd.to_numeric(daily_equity.get("end_equity", pd.Series(dtype=float)), errors="coerce").fillna(
        INITIAL_CAPITAL_TWD
    )
    if equity.empty:
        return 0.0, 0.0
    running_max = equity.cummax()
    drawdown = equity - running_max
    drawdown_pct = drawdown / running_max.replace(0, np.nan)
    return float(drawdown.min()), float(drawdown_pct.min())


def annualized_from_daily_equity(daily_equity: pd.DataFrame, final_equity: float) -> float:
    """Approximate CAGR-like annualized return from daily equity dates."""

    if daily_equity.empty or final_equity <= 0:
        return np.nan
    dates = pd.to_datetime(daily_equity.get("date", pd.Series(dtype="datetime64[ns]")), errors="coerce").dropna()
    if dates.empty:
        return np.nan
    years = max((dates.max() - dates.min()).days / 365.25, 1 / 365.25)
    return float((final_equity / INITIAL_CAPITAL_TWD) ** (1.0 / years) - 1.0)


def daily_sharpe(values: pd.Series) -> float:
    """Sharpe-like daily return metric."""

    numeric = pd.to_numeric(values, errors="coerce").dropna()
    if len(numeric) <= 1 or numeric.std(ddof=0) <= 0:
        return np.nan
    return float(numeric.mean() / numeric.std(ddof=0) * np.sqrt(252))


def market_split_metrics(trades: pd.DataFrame) -> dict[str, Any]:
    """Summarize selected trades by TWSE/TPEx."""

    output: dict[str, Any] = {}
    for market in ["TWSE", "TPEX"]:
        prefix = market.lower()
        group = trades[trades.get("market", pd.Series(dtype="object")).astype("string").str.upper().eq(market)]
        output[f"{prefix}_trades"] = int(len(group))
        output[f"{prefix}_net_pnl"] = float(pd.to_numeric(group.get("net_pnl", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
        output[f"{prefix}_avg_net_return"] = series_mean(group.get("net_return", pd.Series(dtype=float)))
        output[f"{prefix}_profit_factor"] = profit_factor(group.get("net_pnl", pd.Series(dtype=float)))
    return output


def fill_quality_bucket_metrics(trades: pd.DataFrame) -> list[dict[str, Any]]:
    """Summarize performance by fill-quality score bucket."""

    if trades.empty or "fill_quality_score" not in trades.columns:
        return []
    frame = trades.copy()
    score = pd.to_numeric(frame["fill_quality_score"], errors="coerce")
    frame["fill_quality_bucket"] = pd.cut(
        score,
        bins=[-np.inf, 60, 70, 80, 90, np.inf],
        labels=["<60", "60_70", "70_80", "80_90", ">=90"],
    ).astype("string")
    rows = []
    for bucket, group in frame.groupby("fill_quality_bucket", dropna=False, observed=False):
        rows.append(
            {
                "bucket": str(bucket),
                "trades": int(len(group)),
                "net_pnl": float(pd.to_numeric(group["net_pnl"], errors="coerce").fillna(0).sum()),
                "avg_net_return": series_mean(group["net_return"]),
                "profit_factor": profit_factor(group["net_pnl"]),
            }
        )
    return rows


def calculate_robust_score(metrics: dict[str, Any]) -> float:
    """Compute the requested robustness score with explicit penalties."""

    cagr = finite_float(metrics.get("annualized_return"), 0.0)
    pf = finite_float(metrics.get("profit_factor"), 0.0)
    median_return = finite_float(metrics.get("median_net_return_per_trade"), 0.0)
    maxdd = finite_float(metrics.get("max_drawdown_pct"), 0.0)
    positive_quarters = finite_float(metrics.get("positive_quarters"), 0.0)
    total_quarters = finite_float(metrics.get("total_quarters"), 0.0)
    positive_quarter_ratio = positive_quarters / total_quarters if total_quarters else 0.0
    score = cagr + 0.5 * pf + 20.0 * median_return - 3.0 * abs(maxdd) + 5.0 * positive_quarter_ratio
    return float(score - robustness_penalty(metrics))


def robustness_penalty(metrics: dict[str, Any]) -> float:
    """Penalty side of the robustness score."""

    penalty = 0.0
    trades = finite_float(metrics.get("trades"), 0.0)
    if trades < 300:
        penalty += (300.0 - trades) / 100.0
    positive_quarters = finite_float(metrics.get("positive_quarters"), 0.0)
    total_quarters = finite_float(metrics.get("total_quarters"), 0.0)
    if total_quarters and positive_quarters < 8:
        penalty += (8.0 - positive_quarters) * 0.75
    top10_symbols = finite_float(metrics.get("top10_symbol_trade_share"), 0.0)
    if top10_symbols > 0.30:
        penalty += (top10_symbols - 0.30) * 5.0
    top_sector = finite_float(metrics.get("sector_concentration"), 0.0)
    if top_sector > 0.85:
        penalty += (top_sector - 0.85) * 5.0
    pf = finite_float(metrics.get("profit_factor"), 0.0)
    if pf < 2.0:
        penalty += (2.0 - pf) * 1.5
    maxdd = finite_float(metrics.get("max_drawdown_pct"), 0.0)
    if maxdd < -0.08:
        penalty += (abs(maxdd) - 0.08) * 10.0
    return float(penalty)


def select_focused_top(results: pd.DataFrame, limit: int = 100) -> pd.DataFrame:
    """Return the highest-scoring focused rows."""

    if results.empty:
        return results
    return results.sort_values(["robust_score", "annualized_return"], ascending=[False, False]).head(limit).reset_index(drop=True)


def build_focused_report(
    *,
    champion: StrategyResult,
    results: pd.DataFrame,
    top: pd.DataFrame,
    combined: pd.DataFrame,
    start: pd.Timestamp,
    end: pd.Timestamp,
    db_path: Path,
    max_configs: int | None,
) -> str:
    """Build the focused expansion Markdown report."""

    best_overall = row_or_none(results, "final_equity")
    best_risk = row_or_none(results, "robust_score")
    best_champ_dd = best_beats_champion_with_drawdown(results, champion)
    best_combined = best_combined_row(combined)
    market_summary = summarize_dimension(results, "market")
    turnover_summary = summarize_dimension(results, "min_turnover_twd")
    regime_summary = summarize_dimension(results, "market_regime_filter")
    momentum_summary = summarize_dimension(results, "momentum_cap")
    replacement = replacement_verdict(champion, best_risk, best_champ_dd, best_combined)

    lines = [
        "# Focused Challenger Expansion",
        "",
        "## Executive Summary",
        "",
        replacement,
        "",
        "## Inputs",
        "",
        f"Database: `{db_path}`",
        f"Window: {start.date()} to {end.date()}",
        f"Configs evaluated: {len(results):,}" + (f" (capped by --max-configs {max_configs})" if max_configs else ""),
        "",
        "## Current Champion",
        "",
        markdown_table(pd.DataFrame([{**config_to_row(champion.config), **champion.metrics}])),
        "",
        "## Best Overall Config",
        "",
        markdown_table(pd.DataFrame([best_overall]) if best_overall else pd.DataFrame()),
        "",
        "## Best Risk-Adjusted Config",
        "",
        markdown_table(pd.DataFrame([best_risk]) if best_risk else pd.DataFrame()),
        "",
        "## Best Config Beating Champion With Max DD No Worse Than Champion",
        "",
        markdown_table(pd.DataFrame([best_champ_dd]) if best_champ_dd else pd.DataFrame()),
        "",
        "## Best Champion Plus Challenger Combination",
        "",
        markdown_table(pd.DataFrame([best_combined]) if best_combined else pd.DataFrame()),
        "",
        "## Top Robust Configs",
        "",
        markdown_table(top.head(25)),
        "",
        "## Market Robustness",
        "",
        markdown_table(market_summary),
        "",
        "## Turnover Robustness",
        "",
        markdown_table(turnover_summary),
        "",
        "## Regime Filter Robustness",
        "",
        markdown_table(regime_summary),
        "",
        "## Prior Momentum Caps",
        "",
        markdown_table(momentum_summary),
        "",
        "## Strict Answers",
        "",
        strict_answers(results, champion),
        "",
    ]
    return "\n".join(lines)


def build_two_stage_report(
    *,
    champion: StrategyResult,
    fast_results: pd.DataFrame,
    fast_top: pd.DataFrame,
    exact_results: pd.DataFrame,
    exact_top: pd.DataFrame,
    exact_combined: pd.DataFrame,
    start: pd.Timestamp,
    end: pd.Timestamp,
    db_path: Path,
    max_configs: int | None,
    stage: ExpansionStage,
    finalists_requested: int,
    finalist_configs: list[FocusedGridConfig],
    timings: dict[str, float],
) -> str:
    """Build the two-stage focused expansion Markdown report."""

    exact_available = not exact_results.empty
    result_frame = exact_results if exact_available else fast_results
    top_frame = exact_top if exact_available else fast_top
    best_overall = row_or_none(result_frame, "final_equity")
    best_risk = row_or_none(result_frame, "robust_score")
    best_champ_dd = best_beats_champion_with_drawdown(result_frame, champion) if exact_available else None
    best_combined = best_combined_row(exact_combined) if exact_available else None
    recovery = known_winner_recovery(fast_results, finalist_configs)
    replacement = (
        replacement_verdict(champion, best_risk, best_champ_dd, best_combined)
        if exact_available
        else "Fast results are approximate only. No champion replacement decision is allowed without exact finalist reruns."
    )

    lines = [
        "# Focused Challenger Two-Stage Expansion",
        "",
        "## Executive Summary",
        "",
        replacement,
        "",
        "Fast-stage results are approximate. Exact results are the only outputs eligible to replace the champion.",
        "",
        "## Inputs",
        "",
        f"Database: `{db_path}`",
        f"Window: {start.date()} to {end.date()}",
        f"Stage: `{stage}`",
        f"Max configs: {max_configs if max_configs is not None else 'all'}",
        f"Finalists requested: {finalists_requested:,}",
        f"Fast configs evaluated: {len(fast_results):,}",
        f"Exact finalists rerun: {len(exact_results):,}",
        f"Timings: `{json.dumps({key: round(value, 2) for key, value in timings.items()}, sort_keys=True)}`",
        "",
        "## Known Compact Winner Recovery",
        "",
        markdown_table(recovery),
        "",
        "## Current Champion",
        "",
        markdown_table(pd.DataFrame([{**config_to_row(champion.config), **champion.metrics}])),
        "",
        "## Best Overall Config",
        "",
        markdown_table(pd.DataFrame([best_overall]) if best_overall else pd.DataFrame()),
        "",
        "## Best Risk-Adjusted Config",
        "",
        markdown_table(pd.DataFrame([best_risk]) if best_risk else pd.DataFrame()),
        "",
        "## Best Config Beating Champion With Max DD No Worse Than Champion",
        "",
        markdown_table(pd.DataFrame([best_champ_dd]) if best_champ_dd else pd.DataFrame()),
        "",
        "## Best Champion Plus Challenger Combination",
        "",
        markdown_table(pd.DataFrame([best_combined]) if best_combined else pd.DataFrame()),
        "",
        "## Top Configs",
        "",
        markdown_table(top_frame.head(25)),
        "",
        "## Fast Stage Top Approximate Configs",
        "",
        markdown_table(fast_top.head(20)),
        "",
        "## Market Robustness",
        "",
        markdown_table(summarize_dimension(result_frame, "market")),
        "",
        "## Turnover Robustness",
        "",
        markdown_table(summarize_dimension(result_frame, "min_turnover_twd")),
        "",
        "## Regime Filter Robustness",
        "",
        markdown_table(summarize_dimension(result_frame, "market_regime_filter")),
        "",
        "## Prior Momentum Caps",
        "",
        markdown_table(summarize_dimension(result_frame, "momentum_cap")),
        "",
        "## Final Questions",
        "",
        two_stage_answers(
            champion=champion,
            exact_results=exact_results,
            exact_combined=exact_combined,
            fast_results=fast_results,
            finalist_configs=finalist_configs,
        ),
        "",
    ]
    return "\n".join(lines)


def known_winner_recovery(fast_results: pd.DataFrame, finalist_configs: list[FocusedGridConfig]) -> pd.DataFrame:
    """Report whether known compact winners were recovered into exact finalists."""

    labels = ["4f6aa857", "f16ad211", "08915384"]
    anchors = known_compact_winner_configs()
    finalist_hashes = {focused_config_hash(config) for config in finalist_configs}
    rows = []
    ranked = (
        fast_results.sort_values("robust_score", ascending=False).reset_index(drop=True)
        if not fast_results.empty and "robust_score" in fast_results.columns
        else pd.DataFrame()
    )
    for label, config in zip(labels, anchors):
        config_hash = focused_config_hash(config)
        fast_rank = np.nan
        if not ranked.empty:
            matches = ranked.index[ranked["focused_config_hash"].astype(str).eq(config_hash)].tolist()
            if matches:
                fast_rank = matches[0] + 1
        rows.append(
            {
                "known_compact_id": label,
                "focused_config_hash": config_hash,
                "market": config.market,
                "turnover": config.min_turnover_twd,
                "regime": config.market_regime_filter,
                "momentum_cap": config.momentum_cap,
                "fast_rank_by_robust_score": fast_rank,
                "selected_for_exact": config_hash in finalist_hashes,
            }
        )
    return pd.DataFrame(rows)


def two_stage_answers(
    *,
    champion: StrategyResult,
    exact_results: pd.DataFrame,
    exact_combined: pd.DataFrame,
    fast_results: pd.DataFrame,
    finalist_configs: list[FocusedGridConfig],
) -> str:
    """Answer the requested research questions from exact finalist output."""

    recovery = known_winner_recovery(fast_results, finalist_configs)
    recovered_all = bool(recovery["selected_for_exact"].all()) if not recovery.empty else False
    if exact_results.empty:
        return "\n".join(
            [
                f"- Did the two-stage method recover known top challengers? {recovered_all}.",
                "- No exact finalist rerun was performed, so no replacement decision can be made.",
            ]
        )

    best_exact = exact_results.sort_values("robust_score", ascending=False).iloc[0]
    champ_final = safe_metric(champion, "final_equity")
    champ_dd = safe_metric(champion, "max_drawdown_pct")
    anchor_4_hash = focused_config_hash(known_compact_winner_configs()[0])
    anchor_4 = exact_results[exact_results["focused_config_hash"].astype(str).eq(anchor_4_hash)]
    best_beats_4 = False
    if not anchor_4.empty:
        best_beats_4 = float(best_exact["final_equity"]) > float(anchor_4.iloc[0]["final_equity"])
    beats_champion = bool(
        (float(best_exact["final_equity"]) > champ_final) and (float(best_exact["max_drawdown_pct"]) >= champ_dd)
    )
    best_combined = best_combined_row(exact_combined)
    replacement = replacement_verdict(
        champion,
        best_exact.to_dict(),
        best_beats_champion_with_drawdown(exact_results, champion),
        best_combined,
    )
    return "\n".join(
        [
            f"- Did the two-stage method recover known top challengers? {recovered_all}.",
            f"- Best exact finalist: `{best_exact['focused_config_hash']}` / `{best_exact['strategy_name']}`.",
            f"- Does any exact finalist beat 4f6aa857? {best_beats_4}.",
            f"- Does the best exact finalist beat current paper champion with no worse drawdown? {beats_champion}.",
            f"- Decision: {replacement}",
        ]
    )


def strict_answers(results: pd.DataFrame, champion: StrategyResult) -> str:
    if results.empty:
        return "No focused configs were evaluated."
    market_summary = summarize_dimension(results, "market")
    best_market = str(market_summary.sort_values("best_robust_score", ascending=False).iloc[0]["value"])
    turnover_summary = summarize_dimension(results, "min_turnover_twd")
    best_turnover = turnover_summary.sort_values("best_robust_score", ascending=False).iloc[0]["value"]
    regime_summary = summarize_dimension(results, "market_regime_filter")
    best_regime = str(regime_summary.sort_values("best_robust_score", ascending=False).iloc[0]["value"])
    momentum_summary = summarize_dimension(results, "momentum_cap")
    best_momentum = str(momentum_summary.sort_values("best_robust_score", ascending=False).iloc[0]["value"])
    champ_final = safe_metric(champion, "final_equity")
    champ_dd = safe_metric(champion, "max_drawdown_pct")
    replacement_count = int(
        ((results["final_equity"] > champ_final) & (results["max_drawdown_pct"] >= champ_dd)).sum()
    )
    return "\n".join(
        [
            f"1. BOTH-market expansion best bucket: `{best_market}`.",
            f"2. Best turnover bucket by robust score: `{best_turnover}`.",
            f"3. Best regime bucket by robust score: `{best_regime}`.",
            f"4. Best prior-momentum cap bucket by robust score: `{best_momentum}`.",
            f"5. Configs beating champion with no worse max drawdown: {replacement_count:,}.",
            "6. A replacement decision still requires a separate TWSE execution/fill audit if the winning config uses BOTH or TWSE.",
        ]
    )


def best_beats_champion_with_drawdown(results: pd.DataFrame, champion: StrategyResult) -> dict[str, Any] | None:
    if results.empty:
        return None
    champ_final = safe_metric(champion, "final_equity")
    champ_dd = safe_metric(champion, "max_drawdown_pct")
    candidates = results[(results["final_equity"] > champ_final) & (results["max_drawdown_pct"] >= champ_dd)]
    if candidates.empty:
        return None
    return candidates.sort_values(["robust_score", "final_equity"], ascending=[False, False]).iloc[0].to_dict()


def best_combined_row(combined: pd.DataFrame) -> dict[str, Any] | None:
    if combined.empty:
        return None
    candidates = combined[combined["combined_improves_cagr"].fillna(False) | combined["combined_improves_drawdown"].fillna(False)]
    if candidates.empty:
        candidates = combined
    return candidates.sort_values(["diversification_score", "combined_cagr_like"], ascending=[False, False]).iloc[0].to_dict()


def row_or_none(frame: pd.DataFrame, sort_column: str) -> dict[str, Any] | None:
    if frame.empty:
        return None
    return frame.sort_values(sort_column, ascending=False).iloc[0].to_dict()


def summarize_dimension(results: pd.DataFrame, column: str) -> pd.DataFrame:
    """Summarize robustness by one grid dimension."""

    output_columns = [
        "dimension",
        "value",
        "configs",
        "best_robust_score",
        "best_final_equity",
        "median_final_equity",
        "median_max_drawdown_pct",
        "median_profit_factor",
        "positive_configs",
    ]
    if results.empty or column not in results.columns:
        return pd.DataFrame(columns=output_columns)
    rows = []
    for value, group in results.groupby(column, dropna=False, observed=False):
        rows.append(
            {
                "dimension": column,
                "value": value,
                "configs": int(len(group)),
                "best_robust_score": series_max(group["robust_score"]),
                "best_final_equity": series_max(group["final_equity"]),
                "median_final_equity": series_median(group["final_equity"]),
                "median_max_drawdown_pct": series_median(group["max_drawdown_pct"]),
                "median_profit_factor": series_median(group["profit_factor"]),
                "positive_configs": int((pd.to_numeric(group["total_net_pnl"], errors="coerce") > 0).sum()),
            }
        )
    return pd.DataFrame(rows, columns=output_columns).sort_values("best_robust_score", ascending=False)


def replacement_verdict(
    champion: StrategyResult,
    best_risk: dict[str, Any] | None,
    best_champ_dd: dict[str, Any] | None,
    best_combined: dict[str, Any] | None,
) -> str:
    if best_risk is None:
        return "No focused config was evaluated; keep the champion."
    if best_champ_dd is None:
        return "Keep the current champion. No focused config beat it while keeping max drawdown no worse."
    if str(best_champ_dd.get("market")) != "TPEX":
        return (
            "Treat the best focused challenger as a separate research candidate, not an immediate replacement. "
            "It beats the champion on paper with no worse drawdown, but it expands beyond the TPEX-only live universe."
        )
    return "The focused challenger is eligible to replace the champion subject to fresh fill-realism and paper-trading checks."


def momentum_bounds(momentum_cap: MomentumCap) -> tuple[float | None, float | None]:
    if momentum_cap == "30_80":
        return 0.30, 0.80
    if momentum_cap == "20_60":
        return 0.20, 0.60
    if momentum_cap == "10_40":
        return 0.10, 0.40
    return None, None


def focused_config_hash(config: FocusedGridConfig) -> str:
    payload = json.dumps(config.payload(), sort_keys=True)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:8]


def top_n_share(frame: pd.DataFrame, column: str, n: int) -> float:
    if frame.empty or column not in frame.columns:
        return np.nan
    counts = frame[column].astype("string").value_counts(dropna=False)
    total = counts.sum()
    return float(counts.head(n).sum() / total) if total else np.nan


def frame_to_records_json(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "[]"
    return json.dumps(frame.to_dict(orient="records"), default=str, ensure_ascii=False)


def worst_period_label(frame: pd.DataFrame) -> str:
    if frame.empty:
        return ""
    return str(frame.sort_values("period_return", ascending=True).iloc[0]["period"])


def profit_factor(values: pd.Series) -> float:
    pnl = pd.to_numeric(values, errors="coerce").fillna(0.0)
    gains = pnl[pnl > 0].sum()
    losses = abs(pnl[pnl < 0].sum())
    if losses == 0 and gains > 0:
        return float("inf")
    if losses == 0:
        return np.nan
    return float(gains / losses)


def series_mean(values: pd.Series) -> float:
    numeric = pd.to_numeric(values, errors="coerce")
    return float(numeric.mean()) if not numeric.dropna().empty else np.nan


def series_median(values: pd.Series) -> float:
    numeric = pd.to_numeric(values, errors="coerce")
    return float(numeric.median()) if not numeric.dropna().empty else np.nan


def series_max(values: pd.Series) -> float:
    numeric = pd.to_numeric(values, errors="coerce")
    return float(numeric.max()) if not numeric.dropna().empty else np.nan


def finite_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    if not np.isfinite(result):
        return default
    return result


def safe_metric(result: StrategyResult, key: str, default: float = 0.0) -> float:
    return finite_float(result.metrics.get(key), default)


def print_focused_summary(result: FocusedExpansionResult, output_dir: Path) -> None:
    if not result.fast_results.empty:
        print(f"Wrote {len(result.fast_results)} fast rows to {output_dir / FAST_RESULTS_OUTPUT}")
        print(f"Wrote {len(result.fast_top)} fast top rows to {output_dir / FAST_TOP_OUTPUT}")
    if not result.exact_results.empty:
        print(f"Wrote {len(result.exact_results)} exact rows to {output_dir / EXACT_RESULTS_OUTPUT}")
        print(f"Wrote {len(result.exact_top)} exact top rows to {output_dir / EXACT_TOP_OUTPUT}")
        print(f"Wrote {len(result.exact_combined)} exact combined rows to {output_dir / EXACT_COMBINED_OUTPUT}")
    print(f"Wrote report to {result.report_path}")
    print(f"Timings: {json.dumps({key: round(value, 2) for key, value in result.timings.items()}, sort_keys=True)}")
    if not result.top.empty:
        columns = [
            "focused_config_hash",
            "market",
            "min_turnover_twd",
            "min_volume_ratio_20d",
            "market_regime_filter",
            "ranking_method",
            "momentum_cap",
            "weak_sector_handling",
            "trades",
            "final_equity",
            "annualized_return",
            "profit_factor",
            "max_drawdown_pct",
            "robust_score",
        ]
        print("\nTop focused configs")
        print(result.top[[column for column in columns if column in result.top.columns]].head(20).to_string(index=False))


def main() -> None:
    parser = argparse.ArgumentParser(description="Two-stage focused expansion around challenger 4f6aa857")
    parser.add_argument("--db", type=Path, default=get_settings().db_path)
    parser.add_argument("--output-dir", type=Path, default=get_settings().project_root / "reports")
    parser.add_argument("--start", help="Optional start date, YYYY-MM-DD")
    parser.add_argument("--end", help="Optional end date, YYYY-MM-DD")
    parser.add_argument("--rebuild-events", action="store_true")
    parser.add_argument("--stage", choices=["fast", "exact", "two_stage"], default="two_stage")
    parser.add_argument("--finalists", type=int, default=200)
    parser.add_argument("--max-configs", type=int, help="Optional cap for smoke/debug runs")
    parser.add_argument(
        "--fast-output",
        type=Path,
        help="Optional fast-stage result path. Defaults to reports/focused_challenger_fast_results.csv.",
    )
    parser.add_argument("--no-progress", action="store_true")
    args = parser.parse_args()

    result = run_focused_challenger_expansion(
        db_path=args.db,
        output_dir=args.output_dir,
        start=args.start,
        end=args.end,
        rebuild_events=args.rebuild_events,
        max_configs=args.max_configs,
        stage=args.stage,
        finalists=args.finalists,
        fast_output=args.fast_output,
        show_progress=not args.no_progress,
    )
    print_focused_summary(result, args.output_dir)


if __name__ == "__main__":
    main()
