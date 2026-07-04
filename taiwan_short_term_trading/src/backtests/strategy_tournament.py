"""Strategy tournament framework for Taiwan short-term limit-momentum research.

The tournament keeps the current closed-limit-up overnight portfolio as the
champion and evaluates daily-OHLCV challenger strategies under the same cost,
board-lot, ranking, and capital-allocation assumptions.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass
from itertools import islice, product
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
from tqdm.auto import tqdm

from config.settings import get_settings
from src.backtests.closed_limit_up_fill_audit import (
    FILL_ASSUMPTIONS,
    FillAuditThresholds,
    add_fill_proxy_features,
    evaluate_fill_assumption,
)
from src.backtests.event_study import build_event_candidates
from src.backtests.portfolio_sim_closed_limit_up import (
    PortfolioConfig,
    add_candidate_scores,
    empty_candidates,
    first_nonblank,
    join_index_context,
    join_sector_context,
    markdown_table,
    simulate_portfolio,
)
from src.db import get_connection, init_db


StrategyFamily = Literal[
    "closed_limit_up_overnight",
    "touched_limit_not_closed_overnight",
    "near_limit_9_10_overnight",
    "near_limit_8_9_overnight",
    "closed_limit_up_day1_open_to_close",
    "failed_limit_up_day1_reversal",
    "multi_day_limit_up_continuation",
    "first_limit_up_only",
    "repeat_limit_up_only",
    "closed_limit_up_with_market_regime_filter",
    "closed_limit_up_with_sector_filter",
    "closed_limit_up_with_prior_momentum_filter",
]

EntryExitRule = Literal[
    "day0_close_to_day1_open",
    "day1_open_to_day1_close",
    "day1_open_to_day1_high",
]

SectorFilter = Literal[
    "none",
    "avoid_healthcare_materials",
    "technology_only",
    "technology_industrials_only",
]

MarketRegimeFilter = Literal["none", "not_bear", "avoid_weak_day", "bull_only"]
TournamentMode = Literal["compact", "full"]

TOURNAMENT_RESULTS_OUTPUT = "strategy_tournament_results.csv"
TOP_CHALLENGERS_OUTPUT = "strategy_tournament_top_challengers.csv"
COMBINED_PORTFOLIOS_OUTPUT = "strategy_tournament_combined_portfolios.csv"
TOURNAMENT_REPORT_OUTPUT = "strategy_tournament_report.md"

CHAMPION_NAME = "champion_tpex_closed_limit_up_overnight"
WEAK_SECTORS = {"Healthcare", "Materials"}
TECH_SECTOR = "Technology/Electronics"
INDUSTRIAL_SECTOR = "Industrials/Other"

GRID_EVENT_TYPES = ("closed_limit_up", "touched_limit_not_closed", "near_limit_9_10", "failed_limit_up")
GRID_MARKETS = ("TPEX", "TWSE", "BOTH")
GRID_ENTRY_EXIT = ("day0_close_to_day1_open", "day1_open_to_day1_close", "day1_open_to_day1_high")
GRID_TURNOVER = (100_000_000.0, 200_000_000.0, 500_000_000.0, 1_000_000_000.0)
GRID_VOLUME_RATIO = (1.5, 2.0, 3.0, 5.0)
GRID_PRICE_RANGES = ((10.0, 100.0), (10.0, 200.0), (20.0, 100.0), (20.0, 200.0))
GRID_SECTOR_FILTERS = ("none", "avoid_healthcare_materials", "technology_only", "technology_industrials_only")
GRID_MARKET_REGIMES = ("none", "not_bear", "avoid_weak_day", "bull_only")
GRID_FILL_ASSUMPTIONS = ("optimistic", "moderate", "conservative")
GRID_RANKING = ("fill_quality_score", "day0_turnover_twd", "volume_ratio_20d", "composite_score")


@dataclass(frozen=True)
class StrategyConfig:
    """One portfolio-testable tournament strategy definition."""

    strategy_name: str
    family: StrategyFamily
    event_type: str
    market: str = "BOTH"
    entry_exit_rule: EntryExitRule = "day0_close_to_day1_open"
    min_turnover_twd: float = 100_000_000.0
    min_volume_ratio_20d: float = 1.5
    min_close_location: float | None = None
    min_price: float = 10.0
    max_price: float = 200.0
    max_consecutive_limit_ups: int | None = 3
    min_consecutive_limit_ups: int | None = None
    only_first_limit_up: bool = False
    fill_assumption: str = "moderate"
    min_fill_quality_score: float = 50.0
    sector_filter: SectorFilter = "none"
    market_regime_filter: MarketRegimeFilter = "none"
    prior_5d_return_min: float | None = None
    prior_5d_return_max: float | None = None
    prior_20d_return_min: float | None = None
    prior_20d_return_max: float | None = None
    ranking_method: str = "fill_quality_score"
    initial_capital_twd: float = 1_000_000.0
    max_positions_per_day: int = 5
    fixed_notional_twd: float = 300_000.0
    max_notional_per_symbol_pct: float = 0.20
    max_notional_per_sector_pct: float = 0.70
    max_notional_per_industry_pct: float = 0.35
    max_trades_per_symbol_per_month: int = 5

    def payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StrategyResult:
    """A completed strategy simulation."""

    config: StrategyConfig
    trades: pd.DataFrame
    daily_equity: pd.DataFrame
    metrics: dict[str, Any]


@dataclass
class TournamentResult:
    """Tournament outputs and champion context."""

    champion: StrategyResult
    results: pd.DataFrame
    top_challengers: pd.DataFrame
    combined_portfolios: pd.DataFrame
    report_path: Path


def champion_config() -> StrategyConfig:
    """Return the current research champion exactly as tournament config."""

    return StrategyConfig(
        strategy_name=CHAMPION_NAME,
        family="closed_limit_up_overnight",
        event_type="closed_limit_up",
        market="TPEX",
        entry_exit_rule="day0_close_to_day1_open",
        min_turnover_twd=500_000_000.0,
        min_volume_ratio_20d=1.5,
        min_price=10.0,
        max_price=100.0,
        max_consecutive_limit_ups=3,
        fill_assumption="moderate",
        min_fill_quality_score=60.0,
        sector_filter="avoid_healthcare_materials",
        market_regime_filter="none",
        ranking_method="fill_quality_score",
        max_positions_per_day=5,
        fixed_notional_twd=300_000.0,
        max_notional_per_symbol_pct=0.20,
    )


def run_strategy_tournament(
    *,
    db_path: Path | str,
    mode: TournamentMode = "compact",
    output_dir: Path | str | None = None,
    start: str | pd.Timestamp | None = None,
    end: str | pd.Timestamp | None = None,
    rebuild_events: bool = False,
    max_configs: int | None = None,
    show_progress: bool = True,
) -> TournamentResult:
    """Run the champion/challenger tournament and write reports."""

    if mode not in {"compact", "full"}:
        raise ValueError("mode must be compact or full")
    if max_configs is not None and max_configs <= 0:
        raise ValueError("max_configs must be positive when provided")

    init_db(db_path)
    report_dir = Path(output_dir) if output_dir is not None else get_settings().project_root / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    start_ts, end_ts = infer_tournament_dates(db_path=db_path, start=start, end=end, output_dir=report_dir)

    ensure_event_candidates(db_path=db_path, start=start_ts, end=end_ts, rebuild_events=rebuild_events)
    universe = load_tournament_universe(db_path=db_path, start=start_ts, end=end_ts)

    champ = evaluate_strategy(champion_config(), universe)
    configs = list(iter_strategy_grid(mode=mode, max_configs=max_configs))
    configs = dedupe_configs([champion_config(), *configs])

    iterator = tqdm(configs, desc=f"{mode} tournament", leave=False) if show_progress else configs
    result_rows: list[dict[str, Any]] = []
    combined_rows: list[dict[str, Any]] = []
    for config in iterator:
        strategy_result = champ if config.strategy_name == champ.config.strategy_name else evaluate_strategy(config, universe)
        row = build_result_row(strategy_result, champ)
        result_rows.append(row)
        if config.strategy_name != champ.config.strategy_name:
            combined = combine_with_champion(champ, strategy_result)
            combined_rows.append(combined)
            result_rows[-1].update(
                {
                    "combined_total_net_pnl": combined["combined_total_net_pnl"],
                    "combined_cagr_like": combined["combined_cagr_like"],
                    "combined_max_drawdown_pct": combined["combined_max_drawdown_pct"],
                    "combined_improves_cagr": combined["combined_improves_cagr"],
                    "combined_improves_drawdown": combined["combined_improves_drawdown"],
                    "diversification_score": combined["diversification_score"],
                    "challenger_score": challenger_score(result_rows[-1], combined),
                }
            )
        else:
            result_rows[-1].update(
                {
                    "combined_total_net_pnl": np.nan,
                    "combined_cagr_like": np.nan,
                    "combined_max_drawdown_pct": np.nan,
                    "combined_improves_cagr": False,
                    "combined_improves_drawdown": False,
                    "diversification_score": np.nan,
                    "challenger_score": np.nan,
                }
            )

    results = pd.DataFrame(result_rows)
    combined = pd.DataFrame(combined_rows)
    top = select_top_challengers(results, combined)
    report_text = build_tournament_report(
        champion=champ,
        results=results,
        top_challengers=top,
        combined_portfolios=combined,
        mode=mode,
        start=start_ts,
        end=end_ts,
        db_path=Path(db_path),
    )

    results.to_csv(report_dir / TOURNAMENT_RESULTS_OUTPUT, index=False)
    top.to_csv(report_dir / TOP_CHALLENGERS_OUTPUT, index=False)
    combined.to_csv(report_dir / COMBINED_PORTFOLIOS_OUTPUT, index=False)
    report_path = report_dir / TOURNAMENT_REPORT_OUTPUT
    report_path.write_text(report_text, encoding="utf-8")
    return TournamentResult(
        champion=champ,
        results=results,
        top_challengers=top,
        combined_portfolios=combined,
        report_path=report_path,
    )


def infer_tournament_dates(
    *,
    db_path: Path | str,
    start: str | pd.Timestamp | None,
    end: str | pd.Timestamp | None,
    output_dir: Path,
) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Infer tournament dates from explicit args, OOS trades, or daily_prices."""

    explicit_start = pd.Timestamp(start).normalize() if start is not None else None
    explicit_end = pd.Timestamp(end).normalize() if end is not None else None
    if explicit_start is not None and explicit_end is not None:
        if explicit_end < explicit_start:
            raise ValueError("end must be on or after start")
        return explicit_start, explicit_end

    oos_path = output_dir / "walk_forward_closed_limit_up_overnight_oos_trades.csv"
    if oos_path.exists():
        oos = pd.read_csv(oos_path, usecols=lambda column: column in {"signal_date"})
        if not oos.empty and "signal_date" in oos.columns:
            dates = pd.to_datetime(oos["signal_date"], errors="coerce").dropna()
            if not dates.empty:
                inferred_start = dates.min().normalize()
                inferred_end = dates.max().normalize()
                return explicit_start or inferred_start, explicit_end or inferred_end

    with get_connection(db_path, read_only=True) as conn:
        row = conn.execute(
            """
            SELECT MIN(trade_date) AS min_date, MAX(trade_date) AS max_date
            FROM daily_prices
            """
        ).fetchone()
    if row is None or row[0] is None or row[1] is None:
        raise ValueError("Cannot infer tournament dates because daily_prices is empty")
    return explicit_start or pd.Timestamp(row[0]).normalize(), explicit_end or pd.Timestamp(row[1]).normalize()


def ensure_event_candidates(
    *,
    db_path: Path | str,
    start: pd.Timestamp,
    end: pd.Timestamp,
    rebuild_events: bool,
) -> None:
    """Build event candidates only when requested or missing for the window."""

    should_build = rebuild_events
    if not should_build:
        with get_connection(db_path, read_only=True) as conn:
            count = conn.execute(
                """
                SELECT COUNT(*)
                FROM event_candidates
                WHERE trade_date >= ? AND trade_date <= ?
                """,
                [start.date(), end.date()],
            ).fetchone()[0]
        should_build = int(count) == 0
    if should_build:
        build_event_candidates(db_path=db_path, start=start, end=end, markets=["TWSE", "TPEX"])


def load_tournament_universe(
    *,
    db_path: Path | str,
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
) -> pd.DataFrame:
    """Load event candidates with Day1 outcomes, fill proxies, sector, and regime fields."""

    start_ts = pd.Timestamp(start).normalize()
    end_ts = pd.Timestamp(end).normalize()
    with get_connection(db_path, read_only=True) as conn:
        events = conn.execute(
            """
            SELECT
                ec.event_id,
                ec.symbol,
                ec.market,
                ec.trade_date,
                ec.event_type,
                ec.day0_return,
                ec.day0_open,
                ec.day0_high,
                ec.day0_low,
                ec.day0_close,
                ec.day0_volume_shares,
                ec.day0_turnover_twd,
                ec.close_location,
                ec.volume_ratio_20d,
                ec.touched_limit_up,
                ec.closed_limit_up,
                ec.failed_limit_up,
                ec.next_trade_date,
                d0.name AS day0_name,
                d0.trades AS day0_trades,
                d0.limit_up_price AS day0_limit_up_price,
                d1.open AS day1_open,
                d1.high AS day1_high,
                d1.low AS day1_low,
                d1.close AS day1_close,
                d1.turnover_twd AS day1_turnover_twd
            FROM event_candidates ec
            LEFT JOIN daily_prices d0
              ON d0.symbol = ec.symbol
             AND d0.market = ec.market
             AND d0.trade_date = ec.trade_date
            LEFT JOIN daily_prices d1
              ON d1.symbol = ec.symbol
             AND d1.market = ec.market
             AND d1.trade_date = ec.next_trade_date
            WHERE ec.trade_date >= ?
              AND ec.trade_date <= ?
            ORDER BY ec.market, ec.symbol, ec.trade_date, ec.event_type
            """,
            [start_ts.date(), end_ts.date()],
        ).fetch_df()
        history = conn.execute(
            """
            SELECT
                symbol,
                market,
                trade_date,
                close,
                daily_return,
                closed_limit_up
            FROM daily_prices
            WHERE trade_date <= ?
            ORDER BY market, symbol, trade_date
            """,
            [end_ts.date()],
        ).fetch_df()

    if events.empty:
        return empty_tournament_universe()

    events = normalize_event_frame(events)
    history_features = build_daily_history_features(history)
    events = events.merge(history_features, on=["symbol", "market", "trade_date"], how="left")
    events["consecutive_limit_up_count"] = pd.to_numeric(
        events["consecutive_limit_up_count"],
        errors="coerce",
    ).fillna(0.0)
    events["first_limit_up_in_sequence"] = events["first_limit_up_in_sequence"].fillna(False).astype(bool)
    events["gross_return"] = events["day1_open"] / events["day0_close"] - 1.0
    events["net_return"] = np.nan
    events["gross_pnl"] = np.nan
    events["net_pnl"] = np.nan
    scored = add_fill_proxy_features(events)
    for assumption in FILL_ASSUMPTIONS:
        fillable, reason = evaluate_fill_assumption(scored, fill_assumption=assumption, thresholds=FillAuditThresholds())
        scored[f"fillable_{assumption}"] = fillable.fillna(False).astype(bool).to_numpy()
        scored[f"fill_reason_{assumption}"] = reason.astype("string").to_numpy()
    scored["_diagnostic_row_id"] = np.arange(len(scored))
    scored["signal_date"] = scored["trade_date"]
    scored = join_sector_context(scored, db_path=db_path)
    scored = join_index_context(scored, db_path=db_path)
    scored = restore_event_trade_date_after_context_joins(scored)
    scored = add_candidate_scores(scored)
    return scored.sort_values(["trade_date", "market", "symbol", "event_type"]).reset_index(drop=True)


def restore_event_trade_date_after_context_joins(frame: pd.DataFrame) -> pd.DataFrame:
    """Keep Day0 event trade_date stable after context joins that may suffix it."""

    output = frame.copy()
    if "trade_date" not in output.columns and "trade_date_x" in output.columns:
        output = output.rename(columns={"trade_date_x": "trade_date"})
    return output.drop(columns=["trade_date_y"], errors="ignore")


def normalize_event_frame(events: pd.DataFrame) -> pd.DataFrame:
    """Normalize tournament event data types."""

    frame = events.copy()
    frame["symbol"] = frame["symbol"].astype("string").str.strip()
    frame["market"] = frame["market"].astype("string").str.upper().str.strip()
    for column in ["trade_date", "next_trade_date"]:
        frame[column] = pd.to_datetime(frame[column], errors="coerce").dt.normalize()
    numeric_columns = [
        "day0_open",
        "day0_high",
        "day0_low",
        "day0_close",
        "day0_turnover_twd",
        "day0_volume_shares",
        "close_location",
        "volume_ratio_20d",
        "day1_open",
        "day1_high",
        "day1_low",
        "day1_close",
        "day1_turnover_twd",
    ]
    for column in numeric_columns:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["name"] = first_nonblank(frame, ["day0_name", "symbol"])
    return frame


def build_daily_history_features(history: pd.DataFrame) -> pd.DataFrame:
    """Compute prior-only momentum and consecutive limit-up features."""

    columns = [
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
    if history.empty:
        return pd.DataFrame(columns=columns)
    frame = history.copy()
    frame["symbol"] = frame["symbol"].astype("string").str.strip()
    frame["market"] = frame["market"].astype("string").str.upper().str.strip()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce").dt.normalize()
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame["daily_return"] = pd.to_numeric(frame["daily_return"], errors="coerce")
    frame["closed_limit_up"] = frame["closed_limit_up"].fillna(False).astype(bool)
    frame = frame.sort_values(["market", "symbol", "trade_date"]).reset_index(drop=True)

    def consecutive_counts(values: pd.Series) -> pd.Series:
        block = (~values.astype(bool)).cumsum()
        return values.astype(int).groupby(block).cumsum()

    grouped = frame.groupby(["market", "symbol"], group_keys=False)
    frame["consecutive_limit_up_count"] = grouped["closed_limit_up"].transform(consecutive_counts)
    grouped = frame.groupby(["market", "symbol"], group_keys=False)
    frame["prior_consecutive_limit_up_count"] = grouped["consecutive_limit_up_count"].shift(1).fillna(0)
    frame["first_limit_up_in_sequence"] = frame["closed_limit_up"] & frame["prior_consecutive_limit_up_count"].eq(0)
    frame["day_minus1_return"] = grouped["daily_return"].shift(1)
    frame["day_minus1_return_positive"] = frame["day_minus1_return"] > 0
    frame["prior_5d_return"] = grouped["close"].transform(lambda close: close.shift(1) / close.shift(6) - 1.0)
    frame["prior_20d_return"] = grouped["close"].transform(lambda close: close.shift(1) / close.shift(21) - 1.0)
    return frame[columns]


def evaluate_strategy(config: StrategyConfig, universe: pd.DataFrame) -> StrategyResult:
    """Filter the tournament universe, simulate the portfolio, and compute metrics."""

    candidates = build_portfolio_candidates_for_strategy(universe, config)
    portfolio_config = portfolio_config_from_strategy(config)
    trades, daily_equity, base_metrics = simulate_portfolio(candidates, portfolio_config)
    metrics = calculate_tournament_metrics(
        trades=trades,
        daily_equity=daily_equity,
        candidates=candidates,
        base_metrics=base_metrics,
        config=config,
    )
    return StrategyResult(config=config, trades=trades, daily_equity=daily_equity, metrics=metrics)


def build_portfolio_candidates_for_strategy(universe: pd.DataFrame, config: StrategyConfig) -> pd.DataFrame:
    """Convert filtered Day0 events into portfolio-simulator candidates."""

    if universe.empty:
        return empty_candidates()
    validate_strategy_config(config)
    frame = apply_strategy_filters(universe, config)
    if frame.empty:
        return empty_candidates()

    output = frame.copy()
    if config.entry_exit_rule == "day0_close_to_day1_open":
        output["signal_date"] = output["trade_date"]
        output["entry_date"] = output["trade_date"]
        output["exit_date"] = output["next_trade_date"]
        output["entry_price"] = output["day0_close"]
        output["exit_price"] = output["day1_open"]
    elif config.entry_exit_rule == "day1_open_to_day1_close":
        output["signal_date"] = output["next_trade_date"]
        output["entry_date"] = output["next_trade_date"]
        output["exit_date"] = output["next_trade_date"]
        output["entry_price"] = output["day1_open"]
        output["exit_price"] = output["day1_close"]
    else:
        output["signal_date"] = output["next_trade_date"]
        output["entry_date"] = output["next_trade_date"]
        output["exit_date"] = output["next_trade_date"]
        output["entry_price"] = output["day1_open"]
        output["exit_price"] = output["day1_high"]

    output = output.dropna(subset=["signal_date", "entry_date", "exit_date", "entry_price", "exit_price"]).copy()
    output = output[(output["entry_price"] > 0) & (output["exit_price"] > 0)].copy()
    if output.empty:
        return empty_candidates()

    output["trade_id"] = output.apply(lambda row: make_strategy_trade_id(config, row), axis=1)
    output["source_trade_id"] = output["event_id"]
    output["day0_price"] = output["day0_close"]
    output["turnover_twd"] = output["day0_turnover_twd"]
    output["month"] = pd.to_datetime(output["signal_date"], errors="coerce").dt.to_period("M").astype(str)
    output["quarter"] = pd.to_datetime(output["signal_date"], errors="coerce").dt.to_period("Q").astype(str)
    output["_diagnostic_row_id"] = np.arange(len(output))
    output = add_candidate_scores(output)
    return output.sort_values(["signal_date", "symbol", "trade_id"]).reset_index(drop=True)


def apply_strategy_filters(universe: pd.DataFrame, config: StrategyConfig) -> pd.DataFrame:
    """Apply event, liquidity, fillability, sector, regime, and momentum filters."""

    frame = universe.copy()
    mask = frame["event_type"].astype("string").eq(config.event_type)
    market = config.market.upper().strip()
    if market != "BOTH":
        mask &= frame["market"].astype("string").str.upper().eq(market)

    mask &= pd.to_numeric(frame["day0_turnover_twd"], errors="coerce") >= config.min_turnover_twd
    mask &= pd.to_numeric(frame["volume_ratio_20d"], errors="coerce") >= config.min_volume_ratio_20d
    if config.min_close_location is not None:
        mask &= pd.to_numeric(frame["close_location"], errors="coerce") >= config.min_close_location
    price = pd.to_numeric(frame["day0_close"], errors="coerce")
    mask &= price >= config.min_price
    mask &= price <= config.max_price

    consecutive = pd.to_numeric(frame["consecutive_limit_up_count"], errors="coerce").fillna(0.0)
    if config.min_consecutive_limit_ups is not None:
        mask &= consecutive >= config.min_consecutive_limit_ups
    if config.max_consecutive_limit_ups is not None:
        mask &= consecutive <= config.max_consecutive_limit_ups
    if config.only_first_limit_up:
        mask &= frame["first_limit_up_in_sequence"].fillna(False).astype(bool)

    fillable_column = f"fillable_{config.fill_assumption}"
    if fillable_column in frame.columns:
        mask &= frame[fillable_column].fillna(False).astype(bool)
    mask &= pd.to_numeric(frame["fill_quality_score"], errors="coerce") >= config.min_fill_quality_score

    sector = frame.get("sector", pd.Series("", index=frame.index)).astype("string").fillna("")
    if config.sector_filter == "avoid_healthcare_materials":
        mask &= ~sector.isin(WEAK_SECTORS)
    elif config.sector_filter == "technology_only":
        mask &= sector.eq(TECH_SECTOR)
    elif config.sector_filter == "technology_industrials_only":
        mask &= sector.isin({TECH_SECTOR, INDUSTRIAL_SECTOR})

    if config.market_regime_filter != "none":
        index_available = frame.get("index_data_available", pd.Series(False, index=frame.index)).fillna(False).astype(bool)
        mask &= index_available
        if config.market_regime_filter == "not_bear":
            mask &= ~frame.get("bear_regime", pd.Series(False, index=frame.index)).fillna(False).astype(bool)
        elif config.market_regime_filter == "avoid_weak_day":
            mask &= ~frame.get("weak_market_day", pd.Series(False, index=frame.index)).fillna(False).astype(bool)
        elif config.market_regime_filter == "bull_only":
            mask &= frame.get("bull_regime", pd.Series(False, index=frame.index)).fillna(False).astype(bool)

    mask = apply_prior_return_filter(mask, frame, "prior_5d_return", config.prior_5d_return_min, config.prior_5d_return_max)
    mask = apply_prior_return_filter(
        mask,
        frame,
        "prior_20d_return",
        config.prior_20d_return_min,
        config.prior_20d_return_max,
    )
    return frame[mask.fillna(False)].copy().reset_index(drop=True)


def apply_prior_return_filter(
    mask: pd.Series,
    frame: pd.DataFrame,
    column: str,
    min_value: float | None,
    max_value: float | None,
) -> pd.Series:
    values = pd.to_numeric(frame.get(column, pd.Series(np.nan, index=frame.index)), errors="coerce")
    if min_value is not None:
        mask &= values >= min_value
    if max_value is not None:
        mask &= values <= max_value
    return mask


def portfolio_config_from_strategy(config: StrategyConfig) -> PortfolioConfig:
    """Map tournament strategy settings to the shared portfolio simulator."""

    sell_tax_rate = 0.003 if config.entry_exit_rule == "day0_close_to_day1_open" else 0.0015
    return PortfolioConfig(
        initial_capital_twd=config.initial_capital_twd,
        max_positions_per_day=config.max_positions_per_day,
        fixed_notional_twd=config.fixed_notional_twd,
        ranking_method=config.ranking_method,  # type: ignore[arg-type]
        max_notional_per_symbol_pct=config.max_notional_per_symbol_pct,
        max_notional_per_sector_pct=config.max_notional_per_sector_pct,
        max_notional_per_industry_pct=config.max_notional_per_industry_pct,
        max_trades_per_symbol_per_month=config.max_trades_per_symbol_per_month,
        not_bear_regime=False,
        avoid_weak_sectors=False,
        sell_tax_rate=sell_tax_rate,
    )


def calculate_tournament_metrics(
    *,
    trades: pd.DataFrame,
    daily_equity: pd.DataFrame,
    candidates: pd.DataFrame,
    base_metrics: dict[str, Any],
    config: StrategyConfig,
) -> dict[str, Any]:
    """Add tournament-specific metrics to portfolio simulator output."""

    metrics = dict(base_metrics)
    metrics["strategy_name"] = config.strategy_name
    metrics["family"] = config.family
    metrics["active_days"] = int(
        (pd.to_numeric(daily_equity.get("positions_entered", pd.Series(dtype=float)), errors="coerce").fillna(0) > 0).sum()
    )
    metrics["average_daily_exposure"] = float(
        pd.to_numeric(daily_equity.get("gross_exposure_end", pd.Series(dtype=float)), errors="coerce").fillna(0).mean()
    )
    metrics["average_daily_entry_notional"] = float(
        pd.to_numeric(daily_equity.get("gross_entry_notional", pd.Series(dtype=float)), errors="coerce").fillna(0).mean()
    )
    metrics["symbol_concentration"] = top_share(trades, "symbol")
    metrics["sector_concentration"] = top_share(trades, "sector")
    quarter_metrics = quarterly_consistency(trades)
    metrics.update(quarter_metrics)
    metrics["candidate_signals_after_strategy_filter"] = int(len(candidates))
    return metrics


def top_share(frame: pd.DataFrame, column: str) -> float:
    if frame.empty or column not in frame.columns:
        return np.nan
    counts = frame[column].astype("string").value_counts(dropna=False)
    return float(counts.iloc[0] / counts.sum()) if counts.sum() else np.nan


def quarterly_consistency(trades: pd.DataFrame) -> dict[str, Any]:
    if trades.empty:
        return {
            "oos_quarterly_consistency": np.nan,
            "positive_quarters": 0,
            "total_quarters": 0,
        }
    frame = trades.copy()
    frame["quarter"] = pd.to_datetime(frame["exit_date"], errors="coerce").dt.to_period("Q").astype(str)
    by_quarter = pd.to_numeric(frame["net_pnl"], errors="coerce").fillna(0.0).groupby(frame["quarter"]).sum()
    total = int(len(by_quarter))
    positive = int((by_quarter > 0).sum())
    return {
        "oos_quarterly_consistency": positive / total if total else np.nan,
        "positive_quarters": positive,
        "total_quarters": total,
    }


def build_result_row(result: StrategyResult, champion: StrategyResult) -> dict[str, Any]:
    """Flatten config, metrics, and champion-relative values."""

    row = {**config_to_row(result.config), **result.metrics}
    row["config_hash"] = strategy_config_hash(result.config)
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


def combine_with_champion(champion: StrategyResult, challenger: StrategyResult) -> dict[str, Any]:
    """Evaluate a simple 50/50 champion-plus-challenger daily-return blend."""

    combined_daily = combine_daily_returns(champion.daily_equity, challenger.daily_equity)
    combined_metrics = combined_return_metrics(combined_daily, initial_capital=2 * champion.config.initial_capital_twd)
    corr = daily_return_correlation(champion.daily_equity, challenger.daily_equity)
    champ_cagr = safe_metric(champion, "annualized_return")
    champ_dd = safe_metric(champion, "max_drawdown_pct")
    combined_cagr = combined_metrics["combined_cagr_like"]
    combined_dd = combined_metrics["combined_max_drawdown_pct"]
    improves_cagr = combined_cagr > champ_cagr
    improves_drawdown = combined_dd > champ_dd
    diversification = calculate_diversification_score(
        correlation=corr,
        combined_cagr=combined_cagr,
        champion_cagr=champ_cagr,
        combined_max_drawdown_pct=combined_dd,
        champion_max_drawdown_pct=champ_dd,
    )
    return {
        "strategy_name": challenger.config.strategy_name,
        "family": challenger.config.family,
        "config_hash": strategy_config_hash(challenger.config),
        "correlation_with_champion_daily_returns": corr,
        "combined_total_net_pnl": combined_metrics["combined_total_net_pnl"],
        "combined_final_equity": combined_metrics["combined_final_equity"],
        "combined_total_return": combined_metrics["combined_total_return"],
        "combined_cagr_like": combined_cagr,
        "combined_max_drawdown_twd": combined_metrics["combined_max_drawdown_twd"],
        "combined_max_drawdown_pct": combined_dd,
        "combined_daily_sharpe_like": combined_metrics["combined_daily_sharpe_like"],
        "combined_improves_cagr": improves_cagr,
        "combined_improves_drawdown": improves_drawdown,
        "diversification_score": diversification,
    }


def combine_daily_returns(champion_daily: pd.DataFrame, challenger_daily: pd.DataFrame) -> pd.DataFrame:
    """Return a date-aligned 50/50 daily-return blend."""

    left = daily_return_series(champion_daily).rename("champion_return")
    right = daily_return_series(challenger_daily).rename("challenger_return")
    combined = pd.concat([left, right], axis=1, sort=True).fillna(0.0).sort_index()
    if combined.empty:
        return pd.DataFrame(columns=["date", "combined_daily_return"])
    combined["combined_daily_return"] = 0.5 * combined["champion_return"] + 0.5 * combined["challenger_return"]
    return combined.reset_index(names="date")


def combined_return_metrics(combined_daily: pd.DataFrame, *, initial_capital: float) -> dict[str, float]:
    if combined_daily.empty:
        return {
            "combined_final_equity": initial_capital,
            "combined_total_net_pnl": 0.0,
            "combined_total_return": 0.0,
            "combined_cagr_like": np.nan,
            "combined_max_drawdown_twd": 0.0,
            "combined_max_drawdown_pct": 0.0,
            "combined_daily_sharpe_like": np.nan,
        }
    frame = combined_daily.copy()
    returns = pd.to_numeric(frame["combined_daily_return"], errors="coerce").fillna(0.0)
    equity = initial_capital * (1.0 + returns).cumprod()
    running_max = equity.cummax()
    drawdown = equity - running_max
    drawdown_pct = drawdown / running_max.replace(0, np.nan)
    start = pd.to_datetime(frame["date"], errors="coerce").min()
    end = pd.to_datetime(frame["date"], errors="coerce").max()
    years = max((end - start).days / 365.25, 1 / 365.25) if pd.notna(start) and pd.notna(end) else np.nan
    final_equity = float(equity.iloc[-1])
    cagr = (final_equity / initial_capital) ** (1.0 / years) - 1.0 if years and initial_capital > 0 else np.nan
    sharpe = (
        float(returns.mean() / returns.std(ddof=0) * np.sqrt(252))
        if len(returns) > 1 and returns.std(ddof=0) > 0
        else np.nan
    )
    return {
        "combined_final_equity": final_equity,
        "combined_total_net_pnl": final_equity - initial_capital,
        "combined_total_return": final_equity / initial_capital - 1.0,
        "combined_cagr_like": float(cagr),
        "combined_max_drawdown_twd": float(drawdown.min()),
        "combined_max_drawdown_pct": float(drawdown_pct.min()),
        "combined_daily_sharpe_like": sharpe,
    }


def calculate_diversification_score(
    *,
    correlation: float,
    combined_cagr: float,
    champion_cagr: float,
    combined_max_drawdown_pct: float,
    champion_max_drawdown_pct: float,
) -> float:
    """Reward low correlation and combined-portfolio improvement."""

    corr = 0.0 if pd.isna(correlation) else float(correlation)
    low_corr_bonus = max(0.0, 1.0 - corr)
    cagr_bonus = max(0.0, combined_cagr - champion_cagr) if not pd.isna(combined_cagr) else 0.0
    drawdown_bonus = max(0.0, combined_max_drawdown_pct - champion_max_drawdown_pct)
    return float(low_corr_bonus + 2.0 * cagr_bonus + drawdown_bonus)


def challenger_score(result_row: dict[str, Any], combined_row: dict[str, Any]) -> float:
    """Rank useful challengers, including diversifiers that do not beat champion alone."""

    pnl_vs = float(result_row.get("pnl_vs_champion", 0.0) or 0.0)
    median_vs = float(result_row.get("median_return_vs_champion", 0.0) or 0.0)
    pf_vs = float(result_row.get("pf_vs_champion", 0.0) or 0.0)
    diversification = float(combined_row.get("diversification_score", 0.0) or 0.0)
    improves_cagr = 1.0 if combined_row.get("combined_improves_cagr") else 0.0
    improves_drawdown = 1.0 if combined_row.get("combined_improves_drawdown") else 0.0
    return float(pnl_vs / 1_000_000 + 0.25 * median_vs + 0.05 * pf_vs + diversification + improves_cagr + improves_drawdown)


def daily_return_correlation(left_daily: pd.DataFrame, right_daily: pd.DataFrame) -> float:
    left = daily_return_series(left_daily)
    right = daily_return_series(right_daily)
    joined = pd.concat([left, right], axis=1, sort=True).fillna(0.0)
    if len(joined) < 2:
        return np.nan
    corr = joined.iloc[:, 0].corr(joined.iloc[:, 1])
    return float(corr) if pd.notna(corr) else np.nan


def daily_return_series(daily_equity: pd.DataFrame) -> pd.Series:
    if daily_equity.empty or "date" not in daily_equity.columns:
        return pd.Series(dtype=float)
    frame = daily_equity.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
    frame = frame.dropna(subset=["date"])
    if frame.empty:
        return pd.Series(dtype=float)
    return pd.to_numeric(frame.get("daily_return", 0.0), errors="coerce").fillna(0.0).set_axis(frame["date"])


def iter_strategy_grid(*, mode: TournamentMode, max_configs: int | None = None) -> list[StrategyConfig]:
    """Generate compact or full challenger grids."""

    configs = list(iter_compact_grid()) if mode == "compact" else list(iter_full_grid())
    configs = [config for config in configs if config.strategy_name != CHAMPION_NAME]
    configs = dedupe_configs(configs)
    if max_configs is not None:
        return list(islice(configs, max_configs))
    return configs


def iter_compact_grid() -> list[StrategyConfig]:
    """A quick but broad challenger grid touching every strategy family."""

    configs: list[StrategyConfig] = []
    base_families: list[StrategyFamily] = [
        "closed_limit_up_overnight",
        "touched_limit_not_closed_overnight",
        "near_limit_9_10_overnight",
        "near_limit_8_9_overnight",
        "closed_limit_up_day1_open_to_close",
        "failed_limit_up_day1_reversal",
        "multi_day_limit_up_continuation",
        "first_limit_up_only",
        "repeat_limit_up_only",
        "closed_limit_up_with_market_regime_filter",
        "closed_limit_up_with_sector_filter",
        "closed_limit_up_with_prior_momentum_filter",
    ]
    for family, market, turnover, ranking in product(
        base_families,
        ("TPEX", "BOTH"),
        (200_000_000.0, 500_000_000.0),
        ("fill_quality_score", "composite_score"),
    ):
        config = config_for_family(
            family=family,
            market=market,
            min_turnover_twd=turnover,
            min_volume_ratio_20d=1.5,
            min_price=10.0,
            max_price=100.0,
            sector_filter="avoid_healthcare_materials",
            market_regime_filter="none",
            fill_assumption="moderate",
            ranking_method=ranking,
        )
        configs.append(config)
    return configs


def iter_full_grid() -> list[StrategyConfig]:
    """Larger default challenger grid using the dimensions requested by research."""

    configs: list[StrategyConfig] = []
    for (
        event_type,
        market,
        entry_exit,
        turnover,
        volume_ratio,
        price_range,
        sector_filter,
        regime_filter,
        fill_assumption,
        ranking,
    ) in product(
        GRID_EVENT_TYPES,
        GRID_MARKETS,
        GRID_ENTRY_EXIT,
        GRID_TURNOVER,
        GRID_VOLUME_RATIO,
        GRID_PRICE_RANGES,
        GRID_SECTOR_FILTERS,
        GRID_MARKET_REGIMES,
        GRID_FILL_ASSUMPTIONS,
        GRID_RANKING,
    ):
        family = infer_family(event_type=event_type, entry_exit_rule=entry_exit, regime_filter=regime_filter, sector_filter=sector_filter)
        config = StrategyConfig(
            strategy_name=make_strategy_name(
                family=family,
                event_type=event_type,
                market=market,
                entry_exit_rule=entry_exit,
                min_turnover_twd=turnover,
                min_volume_ratio_20d=volume_ratio,
                min_price=price_range[0],
                max_price=price_range[1],
                sector_filter=sector_filter,
                market_regime_filter=regime_filter,
                fill_assumption=fill_assumption,
                ranking_method=ranking,
            ),
            family=family,
            event_type=event_type,
            market=market,
            entry_exit_rule=entry_exit,  # type: ignore[arg-type]
            min_turnover_twd=turnover,
            min_volume_ratio_20d=volume_ratio,
            min_price=price_range[0],
            max_price=price_range[1],
            sector_filter=sector_filter,  # type: ignore[arg-type]
            market_regime_filter=regime_filter,  # type: ignore[arg-type]
            fill_assumption=fill_assumption,
            min_fill_quality_score=60.0 if fill_assumption != "optimistic" else 40.0,
            ranking_method=ranking,
        )
        configs.append(config)
    return configs


def config_for_family(
    *,
    family: StrategyFamily,
    market: str,
    min_turnover_twd: float,
    min_volume_ratio_20d: float,
    min_price: float,
    max_price: float,
    sector_filter: SectorFilter,
    market_regime_filter: MarketRegimeFilter,
    fill_assumption: str,
    ranking_method: str,
) -> StrategyConfig:
    """Create a sensible default config for one named family."""

    event_type = "closed_limit_up"
    entry_exit: EntryExitRule = "day0_close_to_day1_open"
    min_consecutive: int | None = None
    max_consecutive: int | None = 3
    only_first = False
    prior_5d_max: float | None = None
    prior_20d_max: float | None = None
    selected_sector_filter = sector_filter
    selected_regime = market_regime_filter

    if family == "touched_limit_not_closed_overnight":
        event_type = "touched_limit_not_closed"
    elif family == "near_limit_9_10_overnight":
        event_type = "near_limit_9_10"
    elif family == "near_limit_8_9_overnight":
        event_type = "near_limit_8_9"
    elif family == "closed_limit_up_day1_open_to_close":
        entry_exit = "day1_open_to_day1_close"
    elif family == "failed_limit_up_day1_reversal":
        event_type = "failed_limit_up"
        entry_exit = "day1_open_to_day1_close"
    elif family == "multi_day_limit_up_continuation":
        min_consecutive = 2
        max_consecutive = 5
    elif family == "first_limit_up_only":
        only_first = True
        max_consecutive = 1
    elif family == "repeat_limit_up_only":
        min_consecutive = 2
        max_consecutive = 5
    elif family == "closed_limit_up_with_market_regime_filter":
        selected_regime = "not_bear"
    elif family == "closed_limit_up_with_sector_filter":
        selected_sector_filter = "technology_only"
    elif family == "closed_limit_up_with_prior_momentum_filter":
        prior_5d_max = 0.30
        prior_20d_max = 0.80

    return StrategyConfig(
        strategy_name=make_strategy_name(
            family=family,
            event_type=event_type,
            market=market,
            entry_exit_rule=entry_exit,
            min_turnover_twd=min_turnover_twd,
            min_volume_ratio_20d=min_volume_ratio_20d,
            min_price=min_price,
            max_price=max_price,
            sector_filter=selected_sector_filter,
            market_regime_filter=selected_regime,
            fill_assumption=fill_assumption,
            ranking_method=ranking_method,
        ),
        family=family,
        event_type=event_type,
        market=market,
        entry_exit_rule=entry_exit,
        min_turnover_twd=min_turnover_twd,
        min_volume_ratio_20d=min_volume_ratio_20d,
        min_price=min_price,
        max_price=max_price,
        min_consecutive_limit_ups=min_consecutive,
        max_consecutive_limit_ups=max_consecutive,
        only_first_limit_up=only_first,
        fill_assumption=fill_assumption,
        min_fill_quality_score=60.0,
        sector_filter=selected_sector_filter,
        market_regime_filter=selected_regime,
        prior_5d_return_max=prior_5d_max,
        prior_20d_return_max=prior_20d_max,
        ranking_method=ranking_method,
    )


def infer_family(
    *,
    event_type: str,
    entry_exit_rule: str,
    regime_filter: str,
    sector_filter: str,
) -> StrategyFamily:
    if event_type == "closed_limit_up" and regime_filter != "none":
        return "closed_limit_up_with_market_regime_filter"
    if event_type == "closed_limit_up" and sector_filter in {"technology_only", "technology_industrials_only"}:
        return "closed_limit_up_with_sector_filter"
    if event_type == "closed_limit_up" and entry_exit_rule == "day1_open_to_day1_close":
        return "closed_limit_up_day1_open_to_close"
    if event_type == "touched_limit_not_closed":
        return "touched_limit_not_closed_overnight"
    if event_type == "near_limit_9_10":
        return "near_limit_9_10_overnight"
    if event_type == "failed_limit_up":
        return "failed_limit_up_day1_reversal"
    return "closed_limit_up_overnight"


def make_strategy_name(**parts: Any) -> str:
    ordered = [str(parts[key]) for key in sorted(parts)]
    digest = hashlib.sha1("|".join(ordered).encode("utf-8")).hexdigest()[:8]
    return f"{parts['family']}_{digest}"


def select_top_challengers(results: pd.DataFrame, combined: pd.DataFrame, limit: int = 50) -> pd.DataFrame:
    if results.empty:
        return results
    challengers = results[results["strategy_name"] != CHAMPION_NAME].copy()
    if challengers.empty:
        return challengers
    sort_columns = ["challenger_score", "combined_improves_cagr", "total_net_pnl"]
    return challengers.sort_values(sort_columns, ascending=[False, False, False]).head(limit).reset_index(drop=True)


def build_tournament_report(
    *,
    champion: StrategyResult,
    results: pd.DataFrame,
    top_challengers: pd.DataFrame,
    combined_portfolios: pd.DataFrame,
    mode: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
    db_path: Path,
) -> str:
    """Build a concise Markdown tournament report."""

    best_outight = best_outright_challenger(results)
    best_combined = best_combined_challenger(combined_portfolios)
    dead_families = identify_dead_families(results)
    promising_families = identify_promising_families(results, combined_portfolios)
    verdict_lines = tournament_verdict(champion, best_outight, best_combined)

    lines = [
        "# Strategy Tournament Report",
        "",
        "## Executive Summary",
        "",
        *verdict_lines,
        "",
        "## Inputs",
        "",
        f"Database: `{db_path}`",
        f"Mode: `{mode}`",
        f"Window: {start.date()} to {end.date()}",
        f"Strategies evaluated: {len(results):,}",
        "",
        "## Current Champion",
        "",
        markdown_table(pd.DataFrame([{**config_to_row(champion.config), **champion.metrics}])),
        "",
        "## Top Challengers",
        "",
        markdown_table(top_challengers.head(20)),
        "",
        "## Champion Plus Challenger",
        "",
        markdown_table(combined_portfolios.sort_values("diversification_score", ascending=False).head(20) if not combined_portfolios.empty else combined_portfolios),
        "",
        "## Strategy Families",
        "",
        "Dead families under this run: " + (", ".join(dead_families) if dead_families else "none flagged"),
        "",
        "Families deserving deeper research: " + (", ".join(promising_families) if promising_families else "none flagged"),
        "",
        "## Answers",
        "",
        f"1. Current champion still best: {answer_yes_no(best_outight is None)}",
        f"2. Challenger beat it outright: {best_outight['strategy_name'] if best_outight is not None else 'none'}",
        f"3. Challenger improved combined portfolio: {best_combined['strategy_name'] if best_combined is not None else 'none'}",
        f"4. New champion: {best_outight['strategy_name'] if best_outight is not None else CHAMPION_NAME}",
        f"5. Champion + diversifier: {best_combined['strategy_name'] if best_combined is not None else 'none found'}",
        "",
        "Daily OHLCV remains a proxy layer. Any strategy that buys Day0 close at limit-up still needs closing-auction/order-book fill validation before live trading.",
        "",
    ]
    return "\n".join(lines)


def best_outright_challenger(results: pd.DataFrame) -> dict[str, Any] | None:
    if results.empty:
        return None
    challengers = results[results["strategy_name"] != CHAMPION_NAME].copy()
    if challengers.empty:
        return None
    winners = challengers[
        (pd.to_numeric(challengers["total_net_pnl"], errors="coerce") > pd.to_numeric(results.loc[results["strategy_name"] == CHAMPION_NAME, "total_net_pnl"], errors="coerce").max())
        & (pd.to_numeric(challengers["profit_factor"], errors="coerce") >= pd.to_numeric(results.loc[results["strategy_name"] == CHAMPION_NAME, "profit_factor"], errors="coerce").max())
    ]
    if winners.empty:
        return None
    return winners.sort_values(["total_net_pnl", "profit_factor"], ascending=[False, False]).iloc[0].to_dict()


def best_combined_challenger(combined: pd.DataFrame) -> dict[str, Any] | None:
    if combined.empty:
        return None
    candidates = combined[
        combined["combined_improves_cagr"].fillna(False) | combined["combined_improves_drawdown"].fillna(False)
    ]
    if candidates.empty:
        return None
    return candidates.sort_values("diversification_score", ascending=False).iloc[0].to_dict()


def identify_dead_families(results: pd.DataFrame) -> list[str]:
    if results.empty:
        return []
    dead = []
    for family, group in results[results["strategy_name"] != CHAMPION_NAME].groupby("family"):
        positive = pd.to_numeric(group["total_net_pnl"], errors="coerce") > 0
        enough = pd.to_numeric(group["trades"], errors="coerce") >= 20
        if not (positive & enough).any():
            dead.append(str(family))
    return sorted(dead)


def identify_promising_families(results: pd.DataFrame, combined: pd.DataFrame) -> list[str]:
    if results.empty:
        return []
    promising = set(
        results[
            (pd.to_numeric(results["total_net_pnl"], errors="coerce") > 0)
            & (pd.to_numeric(results["profit_factor"], errors="coerce") > 1.2)
            & (pd.to_numeric(results["trades"], errors="coerce") >= 20)
        ]["family"].astype(str)
    )
    if not combined.empty:
        promising.update(combined[combined["diversification_score"] > 1.0]["family"].astype(str))
    return sorted(promising)


def tournament_verdict(
    champion: StrategyResult,
    best_outight: dict[str, Any] | None,
    best_combined: dict[str, Any] | None,
) -> list[str]:
    champion_pnl = champion.metrics.get("total_net_pnl", 0.0)
    lines = [f"Champion net PnL in this tournament window: {float(champion_pnl):,.0f} TWD."]
    if best_outight is None:
        lines.append("No challenger beat the champion outright on both net PnL and profit factor.")
    else:
        lines.append(f"New outright leader: `{best_outight['strategy_name']}`.")
    if best_combined is None:
        lines.append("No challenger clearly improved the simple 50/50 combined portfolio.")
    else:
        lines.append(f"Best diversifier candidate: `{best_combined['strategy_name']}`.")
    return lines


def config_to_row(config: StrategyConfig) -> dict[str, Any]:
    return asdict(config)


def dedupe_configs(configs: list[StrategyConfig]) -> list[StrategyConfig]:
    seen: set[str] = set()
    output: list[StrategyConfig] = []
    for config in configs:
        key = strategy_config_hash(config)
        if key in seen:
            continue
        seen.add(key)
        output.append(config)
    return output


def strategy_config_hash(config: StrategyConfig) -> str:
    payload = json.dumps(config.payload(), sort_keys=True, default=str)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


def make_strategy_trade_id(config: StrategyConfig, row: pd.Series) -> str:
    raw = "|".join(
        [
            config.strategy_name,
            str(row.get("event_id", "")),
            str(row.get("symbol", "")),
            str(row.get("trade_date", "")),
            str(row.get("entry_exit_rule", config.entry_exit_rule)),
        ]
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def safe_metric(result: StrategyResult, key: str, default: float = 0.0) -> float:
    value = result.metrics.get(key, default)
    try:
        if pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def answer_yes_no(value: bool) -> str:
    return "yes" if value else "no"


def validate_strategy_config(config: StrategyConfig) -> None:
    if config.market.upper() not in {"TWSE", "TPEX", "BOTH"}:
        raise ValueError(f"market must be TWSE, TPEX, or BOTH, got {config.market!r}")
    if config.fill_assumption not in FILL_ASSUMPTIONS:
        raise ValueError(f"fill_assumption must be one of {FILL_ASSUMPTIONS}")
    if config.entry_exit_rule not in set(GRID_ENTRY_EXIT):
        raise ValueError(f"unsupported entry_exit_rule: {config.entry_exit_rule}")
    if config.min_turnover_twd < 0:
        raise ValueError("min_turnover_twd must be non-negative")
    if config.min_volume_ratio_20d < 0:
        raise ValueError("min_volume_ratio_20d must be non-negative")
    if config.max_price < config.min_price:
        raise ValueError("max_price must be greater than or equal to min_price")
    if not 0 <= config.min_fill_quality_score <= 100:
        raise ValueError("min_fill_quality_score must be between 0 and 100")


def empty_tournament_universe() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "event_id",
            "symbol",
            "market",
            "trade_date",
            "event_type",
            "day0_open",
            "day0_high",
            "day0_low",
            "day0_close",
            "day0_turnover_twd",
            "close_location",
            "volume_ratio_20d",
            "next_trade_date",
            "day1_open",
            "day1_high",
            "day1_low",
            "day1_close",
            "fill_quality_score",
            "sector",
            "industry",
        ]
    )


def print_tournament_summary(tournament: TournamentResult, output_dir: Path) -> None:
    print(f"Wrote {len(tournament.results)} strategy rows to {output_dir / TOURNAMENT_RESULTS_OUTPUT}")
    print(f"Wrote {len(tournament.top_challengers)} top challenger rows to {output_dir / TOP_CHALLENGERS_OUTPUT}")
    print(f"Wrote {len(tournament.combined_portfolios)} combined rows to {output_dir / COMBINED_PORTFOLIOS_OUTPUT}")
    print(f"Wrote report to {tournament.report_path}")
    if not tournament.top_challengers.empty:
        display = [
            "strategy_name",
            "family",
            "market",
            "entry_exit_rule",
            "trades",
            "total_net_pnl",
            "profit_factor",
            "max_drawdown_pct",
            "correlation_with_champion_daily_returns",
            "diversification_score",
            "challenger_score",
        ]
        print("\nTop challengers")
        print(tournament.top_challengers[[column for column in display if column in tournament.top_challengers.columns]].head(20).to_string(index=False))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a champion/challenger strategy tournament")
    parser.add_argument("--db", type=Path, default=get_settings().db_path)
    parser.add_argument("--mode", choices=["compact", "full"], default="compact")
    parser.add_argument("--output-dir", type=Path, default=get_settings().project_root / "reports")
    parser.add_argument("--start", help="Optional start date, YYYY-MM-DD")
    parser.add_argument("--end", help="Optional end date, YYYY-MM-DD")
    parser.add_argument("--rebuild-events", action="store_true")
    parser.add_argument("--max-configs", type=int, help="Optional cap for quick/debug runs")
    parser.add_argument("--no-progress", action="store_true")
    args = parser.parse_args()

    tournament = run_strategy_tournament(
        db_path=args.db,
        mode=args.mode,
        output_dir=args.output_dir,
        start=args.start,
        end=args.end,
        rebuild_events=args.rebuild_events,
        max_configs=args.max_configs,
        show_progress=not args.no_progress,
    )
    print_tournament_summary(tournament, args.output_dir)


if __name__ == "__main__":
    main()
