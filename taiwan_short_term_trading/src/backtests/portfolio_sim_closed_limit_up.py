"""Live-style portfolio simulation for the closed-limit-up overnight strategy."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, replace
from itertools import product
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
from tqdm import tqdm

from config.settings import get_settings
from src.backtests.costs import calculate_trade_costs
from src.backtests.strategy_limit_momentum import BOARD_LOT_SIZE
from src.db import get_connection, init_db
from src.reports.diagnose_closed_limit_up_execution import load_and_filter_oos_trades


RankingMethod = Literal[
    "fill_quality_score",
    "day0_turnover_twd",
    "volume_ratio_20d",
    "consecutive_limit_up_count",
    "random",
    "composite_score",
]
SizingMethod = Literal["fixed", "equal_weight"]

PORTFOLIO_TRADES_OUTPUT = "closed_limit_up_portfolio_trades.csv"
PORTFOLIO_EQUITY_OUTPUT = "closed_limit_up_portfolio_daily_equity.csv"
PORTFOLIO_GRID_OUTPUT = "closed_limit_up_portfolio_grid.csv"
PORTFOLIO_MONTHLY_OUTPUT = "closed_limit_up_portfolio_monthly_returns.csv"
PORTFOLIO_REPORT_OUTPUT = "closed_limit_up_portfolio_report.md"

WEAK_SECTORS = {"Healthcare", "Materials"}
DEFAULT_REPORT_TOP_ROWS = 20


@dataclass(frozen=True)
class PortfolioConfig:
    """Capital-allocation settings for a one-night portfolio simulation."""

    initial_capital_twd: float = 1_000_000.0
    max_gross_exposure_pct: float = 1.0
    max_positions_per_day: int = 3
    sizing_method: SizingMethod = "fixed"
    fixed_notional_twd: float = 100_000.0
    ranking_method: RankingMethod = "fill_quality_score"
    max_notional_per_symbol_pct: float = 0.20
    max_notional_per_sector_pct: float = 0.70
    max_notional_per_industry_pct: float = 0.35
    max_trades_per_symbol_per_month: int = 5
    not_bear_regime: bool = False
    avoid_weak_sectors: bool = False
    sector_allowlist: tuple[str, ...] | None = None
    random_seed: int = 42
    commission_rate: float = 0.001425
    commission_discount: float = 0.28
    sell_tax_rate: float = 0.003
    slippage_bps_per_side: float = 5.0
    minimum_commission_twd: float = 20.0


def run_closed_limit_up_portfolio_simulation(
    *,
    db_path: Path | str,
    oos_trades_path: Path | str,
    output_dir: Path | str | None = None,
    base_config: PortfolioConfig | None = None,
    run_grid_search: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, Path]:
    """Run the portfolio simulation, compact grid, and Markdown report."""

    init_db(db_path)
    report_dir = Path(output_dir) if output_dir is not None else get_settings().project_root / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)

    candidates = load_portfolio_candidates(db_path=db_path, oos_trades_path=oos_trades_path)
    config = base_config or PortfolioConfig()
    if run_grid_search:
        grid = run_portfolio_grid(candidates)
        chosen_config = config_from_grid_row(select_best_grid_row(grid), base_config=config)
    else:
        trades, daily_equity, metrics = simulate_portfolio(candidates, config)
        grid = pd.DataFrame([{**config_to_row(config), **metrics}])
        chosen_config = config

    trades, daily_equity, metrics = simulate_portfolio(candidates, chosen_config)
    monthly_returns = calculate_monthly_returns(daily_equity)
    report_text = build_portfolio_report(
        candidates=candidates,
        trades=trades,
        daily_equity=daily_equity,
        grid=grid,
        monthly_returns=monthly_returns,
        config=chosen_config,
        metrics=metrics,
        db_path=Path(db_path),
        oos_trades_path=Path(oos_trades_path),
    )

    trades.to_csv(report_dir / PORTFOLIO_TRADES_OUTPUT, index=False)
    daily_equity.to_csv(report_dir / PORTFOLIO_EQUITY_OUTPUT, index=False)
    grid.to_csv(report_dir / PORTFOLIO_GRID_OUTPUT, index=False)
    monthly_returns.to_csv(report_dir / PORTFOLIO_MONTHLY_OUTPUT, index=False)
    report_path = report_dir / PORTFOLIO_REPORT_OUTPUT
    report_path.write_text(report_text, encoding="utf-8")
    return trades, daily_equity, grid, monthly_returns, report_path


def load_portfolio_candidates(*, db_path: Path | str, oos_trades_path: Path | str) -> pd.DataFrame:
    """Load best-strategy OOS trades and add sector/regime fields when available."""

    trades = load_and_filter_oos_trades(oos_trades_path)
    if trades.empty:
        return empty_candidates()
    candidates = normalize_candidate_columns(trades)
    candidates = join_sector_context(candidates, db_path=db_path)
    candidates = join_index_context(candidates, db_path=db_path)
    candidates = add_candidate_scores(candidates)
    return candidates.sort_values(["signal_date", "symbol", "trade_id"]).reset_index(drop=True)


def normalize_candidate_columns(trades: pd.DataFrame) -> pd.DataFrame:
    """Make OOS trade rows usable as portfolio candidates."""

    frame = trades.copy()
    frame["symbol"] = frame["symbol"].astype("string").str.strip()
    frame["market"] = frame["market"].astype("string").str.upper().str.strip()
    for column in ["signal_date", "entry_date", "exit_date"]:
        if column in frame.columns:
            frame[column] = pd.to_datetime(frame[column], errors="coerce").dt.normalize()

    numeric_defaults = {
        "entry_price": "day0_close",
        "exit_price": "day1_open",
        "day0_close": "entry_price",
        "day0_turnover_twd": "turnover_twd",
        "turnover_twd": "day0_turnover_twd",
        "volume_ratio_20d": None,
        "fill_quality_score": None,
        "consecutive_limit_up_count": None,
        "net_return": None,
        "net_pnl": None,
    }
    for column, fallback in numeric_defaults.items():
        if column not in frame.columns:
            frame[column] = frame[fallback] if fallback and fallback in frame.columns else np.nan
        frame[column] = pd.to_numeric(frame[column], errors="coerce")

    if "entry_date" not in frame.columns:
        frame["entry_date"] = frame["signal_date"]
    if "exit_date" not in frame.columns:
        frame["exit_date"] = pd.NaT
    if "trade_id" not in frame.columns:
        frame["trade_id"] = [f"candidate_{i}" for i in range(len(frame))]
    if "event_id" not in frame.columns:
        frame["event_id"] = frame["trade_id"]

    frame["day0_price"] = pd.to_numeric(frame.get("day0_price", frame["entry_price"]), errors="coerce")
    frame["day0_price"] = frame["day0_price"].fillna(frame["entry_price"])
    frame["month"] = frame["signal_date"].dt.to_period("M").astype(str)
    frame["quarter"] = frame["signal_date"].dt.to_period("Q").astype(str)
    return frame


def join_sector_context(candidates: pd.DataFrame, *, db_path: Path | str) -> pd.DataFrame:
    """Join stock-sector metadata and daily-price names."""

    if candidates.empty:
        return candidates.copy()
    keys = candidates[["_diagnostic_row_id", "symbol", "market", "signal_date"]].copy()
    keys["signal_date"] = pd.to_datetime(keys["signal_date"], errors="coerce").dt.date
    with get_connection(db_path, read_only=True) as conn:
        conn.register("_portfolio_sector_keys", keys)
        context = conn.execute(
            """
            SELECT
                k._diagnostic_row_id,
                dp.name AS daily_price_name,
                m.name AS mapped_name,
                m.sector,
                m.industry,
                m.source AS sector_source
            FROM _portfolio_sector_keys k
            LEFT JOIN daily_prices dp
              ON dp.symbol = k.symbol
             AND dp.market = k.market
             AND dp.trade_date = k.signal_date
            LEFT JOIN stock_sector_map m
              ON m.symbol = k.symbol
             AND m.market = k.market
            """
        ).fetch_df()

    joined = candidates.merge(context, on="_diagnostic_row_id", how="left")
    joined["name"] = first_nonblank(joined, ["mapped_name", "daily_price_name", "name", "symbol"])
    joined["sector"] = clean_group_values(joined.get("sector"), "MISSING_SECTOR")
    joined["industry"] = clean_group_values(joined.get("industry"), "MISSING_INDUSTRY")
    joined["sector_source"] = clean_group_values(joined.get("sector_source"), "")
    return joined


def join_index_context(candidates: pd.DataFrame, *, db_path: Path | str, index_symbol: str = "TAIEX") -> pd.DataFrame:
    """Join TAIEX Day0 regime fields when index_daily_prices is populated."""

    if candidates.empty:
        return candidates.copy()
    start = pd.to_datetime(candidates["signal_date"], errors="coerce").min()
    end = pd.to_datetime(candidates["signal_date"], errors="coerce").max()
    with get_connection(db_path, read_only=True) as conn:
        index_frame = conn.execute(
            """
            SELECT
                trade_date,
                daily_return AS taiex_day0_return,
                close_above_ma20 AS taiex_close_above_ma20,
                close_above_ma60 AS taiex_close_above_ma60,
                drawdown_from_60d_high AS taiex_drawdown_from_60d_high
            FROM index_daily_prices
            WHERE UPPER(index_symbol) = ?
              AND trade_date >= ?
              AND trade_date <= ?
            ORDER BY trade_date
            """,
            [index_symbol.upper(), start.date(), end.date()],
        ).fetch_df()

    output = candidates.copy()
    if index_frame.empty:
        output["index_data_available"] = False
        output["bull_regime"] = False
        output["bear_regime"] = False
        output["weak_market_day"] = False
        return output

    index_frame["trade_date"] = pd.to_datetime(index_frame["trade_date"], errors="coerce").dt.normalize()
    joined = output.merge(index_frame, left_on="signal_date", right_on="trade_date", how="left")
    joined = joined.drop(columns=["trade_date"], errors="ignore")
    joined["index_data_available"] = joined["taiex_day0_return"].notna()
    above20 = joined["taiex_close_above_ma20"].fillna(False).astype(bool)
    above60 = joined["taiex_close_above_ma60"].fillna(False).astype(bool)
    joined["bull_regime"] = above20 & above60
    joined["bear_regime"] = (~above20) & (~above60) & joined["index_data_available"]
    joined["weak_market_day"] = pd.to_numeric(joined["taiex_day0_return"], errors="coerce") <= -0.005
    return joined


def add_candidate_scores(candidates: pd.DataFrame) -> pd.DataFrame:
    """Add normalized ranking fields used by the portfolio allocator."""

    frame = candidates.copy()
    frame["fill_quality_score"] = pd.to_numeric(frame["fill_quality_score"], errors="coerce").fillna(0.0)
    frame["day0_turnover_twd"] = pd.to_numeric(frame["day0_turnover_twd"], errors="coerce")
    frame["volume_ratio_20d"] = pd.to_numeric(frame["volume_ratio_20d"], errors="coerce")
    frame["turnover_score"] = minmax_score(np.log1p(frame["day0_turnover_twd"].clip(lower=0)))
    frame["volume_ratio_score"] = minmax_score(frame["volume_ratio_20d"].clip(lower=0))
    frame["composite_score"] = frame["fill_quality_score"] + frame["turnover_score"] + frame["volume_ratio_score"]
    return frame


def simulate_portfolio(
    candidates: pd.DataFrame,
    config: PortfolioConfig | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Simulate cash, entries, exits, and risk caps for overnight signals."""

    portfolio_config = config or PortfolioConfig()
    validate_config(portfolio_config)
    frame = apply_portfolio_filters(candidates, portfolio_config)
    if frame.empty:
        daily = empty_daily_equity(portfolio_config)
        metrics = calculate_portfolio_metrics(
            trades=empty_portfolio_trades(),
            daily_equity=daily,
            skipped=Counter(),
            candidates=frame,
            config=portfolio_config,
        )
        return empty_portfolio_trades(), daily, metrics

    frame = frame.copy()
    frame["_random_rank"] = np.random.default_rng(portfolio_config.random_seed).random(len(frame))
    frame = frame.sort_values(["signal_date", "symbol", "trade_id"]).reset_index(drop=True)

    cash = float(portfolio_config.initial_capital_twd)
    open_positions: list[dict[str, Any]] = []
    selected_rows: list[dict[str, Any]] = []
    skipped: Counter[str] = Counter()
    symbol_month_counts: defaultdict[tuple[str, str], int] = defaultdict(int)
    daily_rows: list[dict[str, Any]] = []

    signal_dates = set(pd.to_datetime(frame["signal_date"], errors="coerce").dropna().dt.normalize())
    exit_dates = set(pd.to_datetime(frame["exit_date"], errors="coerce").dropna().dt.normalize())
    all_dates = sorted(signal_dates | exit_dates)
    grouped = {date: group.copy() for date, group in frame.groupby("signal_date", sort=False)}

    for current_date in all_dates:
        start_equity = cash + sum(position["buy_notional"] for position in open_positions)
        exiting, still_open = split_positions_by_exit_date(open_positions, current_date)
        realized_pnl = 0.0
        exit_cash = 0.0
        for position in exiting:
            cash += position["exit_cash_flow"]
            exit_cash += position["exit_cash_flow"]
            realized_pnl += position["net_pnl"]
        open_positions = still_open

        equity_after_exits = cash + sum(position["buy_notional"] for position in open_positions)
        candidates_today = grouped.get(current_date)
        selected_today = 0
        entry_costs_paid = 0.0
        gross_entry_notional = 0.0
        day_exposure_by_symbol: defaultdict[str, float] = defaultdict(float)
        day_exposure_by_sector: defaultdict[str, float] = defaultdict(float)
        day_exposure_by_industry: defaultdict[str, float] = defaultdict(float)

        if candidates_today is not None and not candidates_today.empty:
            ranked = rank_candidates_for_day(candidates_today, portfolio_config.ranking_method)
            for rank_order, (_, candidate) in enumerate(ranked.iterrows(), start=1):
                decision = try_select_candidate(
                    candidate=candidate,
                    config=portfolio_config,
                    cash=cash,
                    equity=equity_after_exits,
                    selected_today=selected_today,
                    rank_order=rank_order,
                    day_exposure_by_symbol=day_exposure_by_symbol,
                    day_exposure_by_sector=day_exposure_by_sector,
                    day_exposure_by_industry=day_exposure_by_industry,
                    symbol_month_counts=symbol_month_counts,
                )
                if not decision["selected"]:
                    skipped[decision["skip_reason"]] += 1
                    continue

                trade = decision["trade"]
                selected_today += 1
                cash -= trade["entry_cash_required"]
                entry_costs_paid += trade["entry_cost_cash"]
                gross_entry_notional += trade["buy_notional"]
                symbol_month_counts[(trade["symbol"], trade["signal_month"])] += 1
                day_exposure_by_symbol[trade["symbol"]] += trade["buy_notional"]
                day_exposure_by_sector[trade["sector"]] += trade["buy_notional"]
                day_exposure_by_industry[trade["industry"]] += trade["buy_notional"]
                open_positions.append(trade)
                selected_rows.append(trade)

        end_equity = cash + sum(position["buy_notional"] for position in open_positions)
        capacity = max(equity_after_exits * portfolio_config.max_gross_exposure_pct, 0.0)
        daily_rows.append(
            {
                "date": current_date,
                "start_equity": start_equity,
                "equity_after_exits": equity_after_exits,
                "end_equity": end_equity,
                "cash": cash,
                "realized_pnl": realized_pnl,
                "exit_cash_flow": exit_cash,
                "entry_costs_paid": entry_costs_paid,
                "gross_entry_notional": gross_entry_notional,
                "positions_entered": selected_today,
                "open_positions_end": len(open_positions),
                "gross_exposure_end": sum(position["buy_notional"] for position in open_positions),
                "capital_utilization": gross_entry_notional / capacity if capacity else np.nan,
                "daily_return": end_equity / start_equity - 1.0 if start_equity else np.nan,
            }
        )

    trades = pd.DataFrame(selected_rows)
    if trades.empty:
        trades = empty_portfolio_trades()
    else:
        trades = finalize_trade_columns(trades, portfolio_config)
    daily_equity = pd.DataFrame(daily_rows)
    if daily_equity.empty:
        daily_equity = empty_daily_equity(portfolio_config)
    metrics = calculate_portfolio_metrics(
        trades=trades,
        daily_equity=daily_equity,
        skipped=skipped,
        candidates=frame,
        config=portfolio_config,
    )
    return trades, daily_equity, metrics


def try_select_candidate(
    *,
    candidate: pd.Series,
    config: PortfolioConfig,
    cash: float,
    equity: float,
    selected_today: int,
    rank_order: int,
    day_exposure_by_symbol: defaultdict[str, float],
    day_exposure_by_sector: defaultdict[str, float],
    day_exposure_by_industry: defaultdict[str, float],
    symbol_month_counts: defaultdict[tuple[str, str], int],
) -> dict[str, Any]:
    """Return either a selected trade row or a skip reason."""

    if selected_today >= config.max_positions_per_day:
        return skip("max_positions_per_day")

    symbol = str(candidate["symbol"])
    sector = str(candidate.get("sector", "MISSING_SECTOR"))
    industry = str(candidate.get("industry", "MISSING_INDUSTRY"))
    signal_month = str(candidate.get("month", pd.Timestamp(candidate["signal_date"]).to_period("M")))
    if symbol_month_counts[(symbol, signal_month)] >= config.max_trades_per_symbol_per_month:
        return skip("monthly_symbol_cap")

    entry_price = safe_float(candidate.get("entry_price"))
    exit_price = safe_float(candidate.get("exit_price"))
    if entry_price <= 0 or exit_price <= 0:
        return skip("invalid_price")

    remaining_slots = max(config.max_positions_per_day - selected_today, 1)
    remaining_exposure = max(equity * config.max_gross_exposure_pct - sum(day_exposure_by_symbol.values()), 0.0)
    if config.sizing_method == "equal_weight":
        target_notional = remaining_exposure / remaining_slots
    else:
        target_notional = config.fixed_notional_twd
    target_notional = max(min(target_notional, remaining_exposure), 0.0)

    symbol_cap = max(equity * config.max_notional_per_symbol_pct - day_exposure_by_symbol[symbol], 0.0)
    sector_cap = max(equity * config.max_notional_per_sector_pct - day_exposure_by_sector[sector], 0.0)
    industry_cap = max(equity * config.max_notional_per_industry_pct - day_exposure_by_industry[industry], 0.0)
    risk_cap_notional = min(symbol_cap, sector_cap, industry_cap)
    board_lot_notional = entry_price * BOARD_LOT_SIZE
    if risk_cap_notional < board_lot_notional:
        return skip("risk_cap")

    allowed_notional = min(target_notional, risk_cap_notional, cash)
    shares = calculate_board_lot_shares_from_notional(allowed_notional, entry_price)
    if shares < BOARD_LOT_SIZE:
        if target_notional < board_lot_notional:
            return skip("board_lot")
        if remaining_exposure < board_lot_notional or cash < board_lot_notional:
            return skip("capital")
        return skip("board_lot")

    costs = calculate_trade_costs(
        side="long",
        entry_price=entry_price,
        exit_price=exit_price,
        shares=shares,
        commission_rate=config.commission_rate,
        commission_discount=config.commission_discount,
        sell_tax_rate=config.sell_tax_rate,
        slippage_bps_per_side=config.slippage_bps_per_side,
        minimum_commission_twd=config.minimum_commission_twd,
        is_day_trade=False,
        normal_sell_tax_rate=config.sell_tax_rate,
    )
    entry_slippage = costs["slippage_cost"] / 2.0
    exit_slippage = costs["slippage_cost"] / 2.0
    entry_cash_required = costs["buy_notional"] + costs["buy_commission"] + entry_slippage
    if entry_cash_required > cash + 1e-9:
        return skip("capital")

    signal_date = pd.Timestamp(candidate["signal_date"]).normalize()
    exit_date = pd.Timestamp(candidate["exit_date"]).normalize()
    portfolio_trade_id = make_portfolio_trade_id(candidate, config, shares=shares, rank_order=rank_order)
    trade = {
        "portfolio_trade_id": portfolio_trade_id,
        "source_trade_id": candidate.get("trade_id"),
        "event_id": candidate.get("event_id"),
        "symbol": symbol,
        "name": candidate.get("name", symbol),
        "market": candidate.get("market", ""),
        "sector": sector,
        "industry": industry,
        "signal_date": signal_date,
        "entry_date": signal_date,
        "exit_date": exit_date,
        "signal_month": signal_month,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "shares": int(shares),
        "ranking_method": config.ranking_method,
        "rank_order": rank_order,
        "rank_value": ranking_value(candidate, config.ranking_method),
        "buy_notional": costs["buy_notional"],
        "sell_notional": costs["sell_notional"],
        "buy_commission": costs["buy_commission"],
        "sell_commission": costs["sell_commission"],
        "sell_tax": costs["sell_tax"],
        "slippage_cost": costs["slippage_cost"],
        "total_cost": costs["total_cost"],
        "gross_pnl": costs["gross_pnl"],
        "net_pnl": costs["net_pnl"],
        "gross_return": costs["gross_return"],
        "net_return": costs["net_return"],
        "entry_cash_required": entry_cash_required,
        "entry_cost_cash": costs["buy_commission"] + entry_slippage,
        "exit_cash_flow": costs["sell_notional"] - costs["sell_commission"] - costs["sell_tax"] - exit_slippage,
        "fill_quality_score": safe_float(candidate.get("fill_quality_score")),
        "day0_turnover_twd": safe_float(candidate.get("day0_turnover_twd")),
        "volume_ratio_20d": safe_float(candidate.get("volume_ratio_20d")),
        "consecutive_limit_up_count": safe_float(candidate.get("consecutive_limit_up_count")),
        "bull_regime": bool(candidate.get("bull_regime", False)),
        "bear_regime": bool(candidate.get("bear_regime", False)),
        "weak_market_day": bool(candidate.get("weak_market_day", False)),
        "index_data_available": bool(candidate.get("index_data_available", False)),
    }
    return {"selected": True, "trade": trade, "skip_reason": ""}


def apply_portfolio_filters(candidates: pd.DataFrame, config: PortfolioConfig) -> pd.DataFrame:
    """Apply optional regime and sector filters before capital allocation."""

    if candidates.empty:
        return candidates.copy()
    frame = candidates.copy()
    mask = pd.Series(True, index=frame.index)
    if config.not_bear_regime:
        index_available = bool_series(frame, "index_data_available", default=False)
        bear_regime = bool_series(frame, "bear_regime", default=False)
        mask &= index_available
        mask &= ~bear_regime
    if config.avoid_weak_sectors:
        mask &= ~text_series(frame, "sector").isin(WEAK_SECTORS)
    if config.sector_allowlist:
        mask &= text_series(frame, "sector").isin(config.sector_allowlist)
    return frame[mask.fillna(False)].reset_index(drop=True)


def run_portfolio_grid(candidates: pd.DataFrame) -> pd.DataFrame:
    """Run the compact capital-allocation grid requested for the live simulation."""

    rows: list[dict[str, Any]] = []
    grid_values = list(
        product(
            [1_000_000.0, 3_000_000.0, 5_000_000.0],
            [1, 2, 3, 5],
            ["fill_quality_score", "day0_turnover_twd", "composite_score"],
            [100_000.0, 200_000.0, 300_000.0],
            [False, True],
            [False, True],
        )
    )
    for (
        initial_capital,
        max_positions,
        ranking_method,
        fixed_notional,
        not_bear_regime,
        avoid_weak_sectors,
    ) in tqdm(grid_values, desc="portfolio grid", leave=False):
        config = PortfolioConfig(
            initial_capital_twd=initial_capital,
            max_positions_per_day=max_positions,
            ranking_method=ranking_method,
            fixed_notional_twd=fixed_notional,
            not_bear_regime=not_bear_regime,
            avoid_weak_sectors=avoid_weak_sectors,
        )
        _trades, _daily, metrics = simulate_portfolio(candidates, config)
        rows.append({**config_to_row(config), **metrics})
    grid = pd.DataFrame(rows)
    if grid.empty:
        return grid
    return grid.sort_values(["portfolio_score", "total_net_pnl"], ascending=[False, False]).reset_index(drop=True)


def calculate_portfolio_metrics(
    *,
    trades: pd.DataFrame,
    daily_equity: pd.DataFrame,
    skipped: Counter[str],
    candidates: pd.DataFrame,
    config: PortfolioConfig,
) -> dict[str, Any]:
    """Calculate trade-level, daily, capacity, and drawdown metrics."""

    initial_capital = float(config.initial_capital_twd)
    if daily_equity.empty:
        final_equity = initial_capital
        max_drawdown_twd = 0.0
        max_drawdown_pct = 0.0
    else:
        equity = pd.to_numeric(daily_equity["end_equity"], errors="coerce").fillna(initial_capital)
        final_equity = float(equity.iloc[-1])
        running_max = equity.cummax()
        drawdown = equity - running_max
        drawdown_pct = drawdown / running_max.replace(0, np.nan)
        max_drawdown_twd = float(drawdown.min())
        max_drawdown_pct = float(drawdown_pct.min())

    total_net_pnl = final_equity - initial_capital
    trade_net_pnl = pd.to_numeric(trades.get("net_pnl", pd.Series(dtype=float)), errors="coerce").fillna(0.0)
    trade_net_return = pd.to_numeric(trades.get("net_return", pd.Series(dtype=float)), errors="coerce")
    gains = trade_net_pnl[trade_net_pnl > 0].sum()
    losses = abs(trade_net_pnl[trade_net_pnl < 0].sum())
    profit_factor = profit_factor_from_pnl(gains, losses)
    daily_return = pd.to_numeric(daily_equity.get("daily_return", pd.Series(dtype=float)), errors="coerce").dropna()
    daily_mean = float(daily_return.mean()) if not daily_return.empty else np.nan
    daily_median = float(daily_return.median()) if not daily_return.empty else np.nan
    daily_sharpe = (
        float(daily_return.mean() / daily_return.std(ddof=0) * np.sqrt(252))
        if len(daily_return) > 1 and daily_return.std(ddof=0) > 0
        else np.nan
    )
    start = pd.to_datetime(daily_equity.get("date", pd.Series(dtype="datetime64[ns]")), errors="coerce").min()
    end = pd.to_datetime(daily_equity.get("date", pd.Series(dtype="datetime64[ns]")), errors="coerce").max()
    annualized_return = annualized_return_from_equity(initial_capital, final_equity, start, end)
    exited_days = daily_equity[pd.to_numeric(daily_equity.get("realized_pnl", 0), errors="coerce").fillna(0.0) != 0]
    selected_days = daily_equity[pd.to_numeric(daily_equity.get("positions_entered", 0), errors="coerce").fillna(0) > 0]
    candidate_count = int(len(candidates))
    trade_count = int(len(trades))
    utilization = pd.to_numeric(selected_days.get("capital_utilization", pd.Series(dtype=float)), errors="coerce")

    metrics = {
        "candidate_signals": candidate_count,
        "trades": trade_count,
        "trade_fill_rate": trade_count / candidate_count if candidate_count else np.nan,
        "total_net_pnl": float(total_net_pnl),
        "final_equity": float(final_equity),
        "total_return": final_equity / initial_capital - 1.0 if initial_capital else np.nan,
        "annualized_return": annualized_return,
        "max_drawdown_twd": max_drawdown_twd,
        "max_drawdown_pct": max_drawdown_pct,
        "daily_return_mean": daily_mean,
        "daily_return_median": daily_median,
        "daily_sharpe_like": daily_sharpe,
        "trade_win_rate": float((trade_net_pnl > 0).mean()) if trade_count else np.nan,
        "day_win_rate": float((pd.to_numeric(exited_days["realized_pnl"], errors="coerce") > 0).mean())
        if not exited_days.empty
        else np.nan,
        "avg_net_return_per_trade": float(trade_net_return.mean()) if trade_count else np.nan,
        "median_net_return_per_trade": float(trade_net_return.median()) if trade_count else np.nan,
        "profit_factor": profit_factor,
        "average_positions_per_signal_day": float(selected_days["positions_entered"].mean())
        if not selected_days.empty
        else 0.0,
        "capital_utilization": float(utilization.mean()) if not utilization.empty else 0.0,
        "skipped_signals_due_to_capital": int(skipped.get("capital", 0)),
        "skipped_due_to_board_lot": int(skipped.get("board_lot", 0)),
        "skipped_due_to_risk_caps": int(skipped.get("risk_cap", 0)),
        "skipped_due_to_max_positions": int(skipped.get("max_positions_per_day", 0)),
        "skipped_due_to_monthly_symbol_cap": int(skipped.get("monthly_symbol_cap", 0)),
    }
    metrics["portfolio_score"] = portfolio_score(metrics)
    return metrics


def calculate_monthly_returns(daily_equity: pd.DataFrame) -> pd.DataFrame:
    """Compound daily equity rows into month-level returns."""

    columns = ["month", "start_equity", "end_equity", "monthly_return", "net_pnl", "positions_entered"]
    if daily_equity.empty:
        return pd.DataFrame(columns=columns)
    frame = daily_equity.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["month"] = frame["date"].dt.to_period("M").astype(str)
    rows = []
    for month, group in frame.groupby("month", sort=True):
        start_equity = float(group["start_equity"].iloc[0])
        end_equity = float(group["end_equity"].iloc[-1])
        rows.append(
            {
                "month": month,
                "start_equity": start_equity,
                "end_equity": end_equity,
                "monthly_return": end_equity / start_equity - 1.0 if start_equity else np.nan,
                "net_pnl": end_equity - start_equity,
                "positions_entered": int(pd.to_numeric(group["positions_entered"], errors="coerce").sum()),
            }
        )
    return pd.DataFrame(rows, columns=columns)


def build_portfolio_report(
    *,
    candidates: pd.DataFrame,
    trades: pd.DataFrame,
    daily_equity: pd.DataFrame,
    grid: pd.DataFrame,
    monthly_returns: pd.DataFrame,
    config: PortfolioConfig,
    metrics: dict[str, Any],
    db_path: Path,
    oos_trades_path: Path,
) -> str:
    """Build the Markdown portfolio report."""

    top_grid = grid.head(DEFAULT_REPORT_TOP_ROWS) if not grid.empty else grid
    sector_exposure = exposure_summary(trades, "sector")
    industry_exposure = exposure_summary(trades, "industry")
    symbol_exposure = exposure_summary(trades, "symbol").head(20)
    regime_line = "enabled" if config.not_bear_regime else "disabled"
    weak_sector_line = "enabled" if config.avoid_weak_sectors else "disabled"
    verdict = portfolio_verdict(metrics)

    lines = [
        "# Closed-Limit-Up Overnight Portfolio Simulation",
        "",
        "## Executive Answer",
        "",
        verdict,
        "",
        "## Inputs",
        "",
        f"OOS trades: `{oos_trades_path}`",
        f"Database: `{db_path}`",
        f"Best-strategy candidate signals after filter: {len(candidates):,}",
        "",
        "## Selected Portfolio Settings",
        "",
        markdown_table(pd.DataFrame([config_to_row(config)])),
        "",
        "## Portfolio Results",
        "",
        markdown_table(pd.DataFrame([metrics])),
        "",
        "## Interpretation",
        "",
        (
            f"The selected run used {config.initial_capital_twd:,.0f} TWD initial capital, "
            f"{config.max_positions_per_day} max positions per day, `{config.ranking_method}` ranking, "
            f"and {config.fixed_notional_twd:,.0f} TWD fixed target notional. "
            f"The not-bear regime filter was {regime_line}; weak-sector avoidance was {weak_sector_line}."
        ),
        "",
        "## Top Grid Configurations",
        "",
        markdown_table(top_grid),
        "",
        "## Monthly Returns",
        "",
        markdown_table(monthly_returns.tail(24)),
        "",
        "## Exposure By Sector",
        "",
        markdown_table(sector_exposure),
        "",
        "## Exposure By Industry",
        "",
        markdown_table(industry_exposure.head(20)),
        "",
        "## Top Symbol Exposure",
        "",
        markdown_table(symbol_exposure),
        "",
        "## Live Paper-Trading Recommendation",
        "",
        live_recommendation(metrics, config),
        "",
    ]
    return "\n".join(lines)


def exposure_summary(trades: pd.DataFrame, group_column: str) -> pd.DataFrame:
    """Summarize selected trade exposure by sector, industry, or symbol."""

    columns = [group_column, "trades", "buy_notional", "net_pnl", "avg_net_return", "trade_share", "pnl_share"]
    if trades.empty or group_column not in trades.columns:
        return pd.DataFrame(columns=columns)
    total_trades = len(trades)
    total_pnl = pd.to_numeric(trades["net_pnl"], errors="coerce").fillna(0.0).sum()
    rows = []
    for value, group in trades.groupby(group_column, dropna=False, observed=False):
        net_pnl = pd.to_numeric(group["net_pnl"], errors="coerce").fillna(0.0).sum()
        rows.append(
            {
                group_column: value,
                "trades": len(group),
                "buy_notional": pd.to_numeric(group["buy_notional"], errors="coerce").fillna(0.0).sum(),
                "net_pnl": net_pnl,
                "avg_net_return": pd.to_numeric(group["net_return"], errors="coerce").mean(),
                "trade_share": len(group) / total_trades if total_trades else np.nan,
                "pnl_share": net_pnl / total_pnl if total_pnl else np.nan,
            }
        )
    return pd.DataFrame(rows, columns=columns).sort_values("net_pnl", ascending=False).reset_index(drop=True)


def rank_candidates_for_day(candidates: pd.DataFrame, ranking_method: RankingMethod) -> pd.DataFrame:
    """Sort same-day signals by the selected ranking method."""

    frame = candidates.copy()
    if ranking_method == "random":
        return frame.sort_values(["_random_rank", "symbol"], ascending=[False, True])
    rank_column = ranking_method
    if rank_column not in frame.columns:
        frame[rank_column] = np.nan
    frame["_rank_value"] = pd.to_numeric(frame[rank_column], errors="coerce").fillna(-np.inf)
    return frame.sort_values(["_rank_value", "symbol"], ascending=[False, True])


def split_positions_by_exit_date(
    open_positions: list[dict[str, Any]],
    current_date: pd.Timestamp,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    exiting = []
    still_open = []
    for position in open_positions:
        if pd.Timestamp(position["exit_date"]).normalize() <= current_date:
            exiting.append(position)
        else:
            still_open.append(position)
    return exiting, still_open


def finalize_trade_columns(trades: pd.DataFrame, config: PortfolioConfig) -> pd.DataFrame:
    """Add config fields and stable output column order."""

    output = trades.copy()
    output["portfolio_config_hash"] = config_hash(config)
    output["config_json"] = json.dumps(asdict(config), ensure_ascii=False, sort_keys=True)
    output["holding_days"] = (
        pd.to_datetime(output["exit_date"], errors="coerce") - pd.to_datetime(output["entry_date"], errors="coerce")
    ).dt.days
    columns = [
        "portfolio_trade_id",
        "portfolio_config_hash",
        "source_trade_id",
        "event_id",
        "symbol",
        "name",
        "market",
        "sector",
        "industry",
        "signal_date",
        "entry_date",
        "exit_date",
        "holding_days",
        "entry_price",
        "exit_price",
        "shares",
        "ranking_method",
        "rank_order",
        "rank_value",
        "buy_notional",
        "sell_notional",
        "gross_pnl",
        "net_pnl",
        "gross_return",
        "net_return",
        "buy_commission",
        "sell_commission",
        "sell_tax",
        "slippage_cost",
        "total_cost",
        "fill_quality_score",
        "day0_turnover_twd",
        "volume_ratio_20d",
        "consecutive_limit_up_count",
        "bull_regime",
        "bear_regime",
        "weak_market_day",
        "index_data_available",
        "config_json",
    ]
    for column in columns:
        if column not in output.columns:
            output[column] = np.nan
    return output[columns].sort_values(["signal_date", "rank_order", "symbol"]).reset_index(drop=True)


def config_to_row(config: PortfolioConfig) -> dict[str, Any]:
    row = asdict(config)
    if row["sector_allowlist"] is not None:
        row["sector_allowlist"] = ",".join(row["sector_allowlist"])
    return row


def config_from_grid_row(row: pd.Series, *, base_config: PortfolioConfig) -> PortfolioConfig:
    """Create a simulation config from a grid-result row."""

    if row.empty:
        return base_config
    return replace(
        base_config,
        initial_capital_twd=float(row["initial_capital_twd"]),
        max_positions_per_day=int(row["max_positions_per_day"]),
        ranking_method=str(row["ranking_method"]),
        fixed_notional_twd=float(row["fixed_notional_twd"]),
        not_bear_regime=bool(row["not_bear_regime"]),
        avoid_weak_sectors=bool(row["avoid_weak_sectors"]),
    )


def select_best_grid_row(grid: pd.DataFrame) -> pd.Series:
    if grid.empty:
        return pd.Series(dtype=object)
    return grid.sort_values(["portfolio_score", "total_net_pnl"], ascending=[False, False]).iloc[0]


def config_hash(config: PortfolioConfig) -> str:
    payload = json.dumps(asdict(config), sort_keys=True, default=str)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


def make_portfolio_trade_id(candidate: pd.Series, config: PortfolioConfig, *, shares: int, rank_order: int) -> str:
    raw = "|".join(
        [
            str(candidate.get("trade_id", "")),
            str(candidate.get("symbol", "")),
            str(candidate.get("signal_date", "")),
            str(shares),
            str(rank_order),
            config_hash(config),
        ]
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def calculate_board_lot_shares_from_notional(notional_twd: float, entry_price: float) -> int:
    if notional_twd <= 0 or entry_price <= 0:
        return 0
    raw_shares = int(notional_twd // entry_price)
    return int((raw_shares // BOARD_LOT_SIZE) * BOARD_LOT_SIZE)


def skip(reason: str) -> dict[str, Any]:
    return {"selected": False, "trade": None, "skip_reason": reason}


def ranking_value(candidate: pd.Series, ranking_method: RankingMethod) -> float:
    if ranking_method == "random":
        return safe_float(candidate.get("_random_rank"))
    return safe_float(candidate.get(ranking_method))


def safe_float(value: Any, default: float = np.nan) -> float:
    try:
        if pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def minmax_score(values: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    min_value = numeric.min(skipna=True)
    max_value = numeric.max(skipna=True)
    if pd.isna(min_value) or pd.isna(max_value) or max_value == min_value:
        return pd.Series(0.0, index=values.index)
    return ((numeric - min_value) / (max_value - min_value) * 100.0).fillna(0.0)


def first_nonblank(frame: pd.DataFrame, columns: list[str]) -> pd.Series:
    result = pd.Series(pd.NA, index=frame.index, dtype="object")
    for column in columns:
        if column not in frame.columns:
            continue
        candidate = frame[column]
        mask = result.isna() | (result.astype("string").str.strip() == "")
        result.loc[mask] = candidate.loc[mask]
    return result.astype("string").fillna("").str.strip()


def clean_group_values(values: pd.Series | None, missing_label: str) -> pd.Series:
    if values is None:
        return pd.Series(dtype="string")
    cleaned = values.astype("string").fillna("").str.strip()
    return cleaned.mask(cleaned == "", missing_label)


def text_series(frame: pd.DataFrame, column: str, default: str = "") -> pd.Series:
    if column not in frame.columns:
        return pd.Series(default, index=frame.index, dtype="string")
    return frame[column].astype("string").fillna(default).str.strip()


def bool_series(frame: pd.DataFrame, column: str, default: bool = False) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(default, index=frame.index, dtype=bool)
    return frame[column].fillna(default).astype(bool)


def profit_factor_from_pnl(gains: float, losses: float) -> float:
    if losses == 0 and gains > 0:
        return float("inf")
    if losses == 0:
        return np.nan
    return float(gains / losses)


def annualized_return_from_equity(
    initial_capital: float,
    final_equity: float,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> float:
    if initial_capital <= 0 or pd.isna(start) or pd.isna(end) or end <= start:
        return np.nan
    years = max((end - start).days / 365.25, 1 / 365.25)
    if final_equity <= 0:
        return -1.0
    return float((final_equity / initial_capital) ** (1.0 / years) - 1.0)


def portfolio_score(metrics: dict[str, Any]) -> float:
    annualized = safe_float(metrics.get("annualized_return"), 0.0)
    sharpe = safe_float(metrics.get("daily_sharpe_like"), 0.0)
    drawdown = abs(safe_float(metrics.get("max_drawdown_pct"), 0.0))
    trade_fill_rate = safe_float(metrics.get("trade_fill_rate"), 0.0)
    return float(annualized + 0.05 * sharpe + 0.10 * trade_fill_rate - 0.5 * drawdown)


def validate_config(config: PortfolioConfig) -> None:
    if config.initial_capital_twd <= 0:
        raise ValueError("initial_capital_twd must be positive")
    if not 0 < config.max_gross_exposure_pct <= 1:
        raise ValueError("max_gross_exposure_pct must be in (0, 1]")
    if config.max_positions_per_day <= 0:
        raise ValueError("max_positions_per_day must be positive")
    if config.fixed_notional_twd <= 0:
        raise ValueError("fixed_notional_twd must be positive")
    if config.sizing_method not in {"fixed", "equal_weight"}:
        raise ValueError("sizing_method must be fixed or equal_weight")


def empty_candidates() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "_diagnostic_row_id",
            "trade_id",
            "event_id",
            "symbol",
            "market",
            "signal_date",
            "entry_date",
            "exit_date",
            "entry_price",
            "exit_price",
            "day0_close",
            "day0_turnover_twd",
            "volume_ratio_20d",
            "fill_quality_score",
            "consecutive_limit_up_count",
            "sector",
            "industry",
            "bear_regime",
            "index_data_available",
            "composite_score",
        ]
    )


def empty_portfolio_trades() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "portfolio_trade_id",
            "portfolio_config_hash",
            "source_trade_id",
            "event_id",
            "symbol",
            "name",
            "market",
            "sector",
            "industry",
            "signal_date",
            "entry_date",
            "exit_date",
            "entry_price",
            "exit_price",
            "shares",
            "buy_notional",
            "sell_notional",
            "gross_pnl",
            "net_pnl",
            "gross_return",
            "net_return",
        ]
    )


def empty_daily_equity(config: PortfolioConfig) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "date": pd.NaT,
                "start_equity": config.initial_capital_twd,
                "equity_after_exits": config.initial_capital_twd,
                "end_equity": config.initial_capital_twd,
                "cash": config.initial_capital_twd,
                "realized_pnl": 0.0,
                "exit_cash_flow": 0.0,
                "entry_costs_paid": 0.0,
                "gross_entry_notional": 0.0,
                "positions_entered": 0,
                "open_positions_end": 0,
                "gross_exposure_end": 0.0,
                "capital_utilization": 0.0,
                "daily_return": 0.0,
            }
        ]
    )


def portfolio_verdict(metrics: dict[str, Any]) -> str:
    pnl = safe_float(metrics.get("total_net_pnl"), 0.0)
    drawdown_pct = abs(safe_float(metrics.get("max_drawdown_pct"), 0.0))
    fill_rate = safe_float(metrics.get("trade_fill_rate"), 0.0)
    if pnl > 0 and drawdown_pct < 0.25 and fill_rate > 0.25:
        return (
            "Strict verdict: the edge survives the tested capital constraints in this daily-data portfolio "
            "simulation. This is still a paper-trading candidate, not a live deployment decision, because "
            "actual closing-auction fill priority is not modeled."
        )
    if pnl > 0:
        return (
            "Strict verdict: the strategy remains profitable, but capital allocation or drawdown constraints "
            "materially reduce the usable edge. Treat it as a constrained paper-trading candidate."
        )
    return "Strict verdict: the portfolio version does not survive capital constraints under the selected settings."


def live_recommendation(metrics: dict[str, Any], config: PortfolioConfig) -> str:
    return (
        "Recommended next live-paper settings: use the selected configuration above, keep per-symbol and "
        "industry caps active, and record every unfilled Day0 close order. The next confidence upgrade is "
        "closing-auction/order-book data, because daily OHLCV cannot verify queue priority at limit-up."
    )


def markdown_table(frame: pd.DataFrame, *, max_rows: int = 40) -> str:
    if frame.empty:
        return "_No rows._"
    view = frame.head(max_rows).copy().fillna("")
    for column in view.columns:
        if pd.api.types.is_float_dtype(view[column]):
            view[column] = view[column].map(format_float)
    headers = [str(column) for column in view.columns]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for _, row in view.iterrows():
        lines.append("| " + " | ".join(escape_markdown_cell(row[column]) for column in view.columns) + " |")
    return "\n".join(lines)


def format_float(value: Any) -> str:
    if pd.isna(value):
        return ""
    number = float(value)
    if abs(number) >= 1000:
        return f"{number:,.2f}"
    return f"{number:.4f}"


def escape_markdown_cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def parse_bool(value: str | bool) -> bool:
    if isinstance(value, bool):
        return value
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y"}:
        return True
    if normalized in {"0", "false", "no", "n"}:
        return False
    raise argparse.ArgumentTypeError(f"Expected boolean value, got {value!r}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Simulate capital allocation for closed-limit-up overnight trades")
    parser.add_argument("--db", type=Path, default=get_settings().db_path)
    parser.add_argument(
        "--oos-trades",
        type=Path,
        default=get_settings().project_root / "reports" / "walk_forward_closed_limit_up_overnight_oos_trades.csv",
    )
    parser.add_argument("--output-dir", type=Path, default=get_settings().project_root / "reports")
    parser.add_argument("--initial-capital-twd", type=float, default=1_000_000.0)
    parser.add_argument("--max-gross-exposure-pct", type=float, default=1.0)
    parser.add_argument("--max-positions-per-day", type=int, default=3)
    parser.add_argument("--sizing-method", choices=["fixed", "equal_weight"], default="fixed")
    parser.add_argument("--fixed-notional-twd", type=float, default=100_000.0)
    parser.add_argument(
        "--ranking-method",
        choices=[
            "fill_quality_score",
            "day0_turnover_twd",
            "volume_ratio_20d",
            "consecutive_limit_up_count",
            "random",
            "composite_score",
        ],
        default="fill_quality_score",
    )
    parser.add_argument("--max-notional-per-symbol-pct", type=float, default=0.20)
    parser.add_argument("--max-notional-per-sector-pct", type=float, default=0.70)
    parser.add_argument("--max-notional-per-industry-pct", type=float, default=0.35)
    parser.add_argument("--max-trades-per-symbol-per-month", type=int, default=5)
    parser.add_argument("--not-bear-regime", type=parse_bool, default=False)
    parser.add_argument("--avoid-weak-sectors", type=parse_bool, default=False)
    parser.add_argument("--run-grid", type=parse_bool, default=True)
    args = parser.parse_args()

    base_config = PortfolioConfig(
        initial_capital_twd=args.initial_capital_twd,
        max_gross_exposure_pct=args.max_gross_exposure_pct,
        max_positions_per_day=args.max_positions_per_day,
        sizing_method=args.sizing_method,
        fixed_notional_twd=args.fixed_notional_twd,
        ranking_method=args.ranking_method,
        max_notional_per_symbol_pct=args.max_notional_per_symbol_pct,
        max_notional_per_sector_pct=args.max_notional_per_sector_pct,
        max_notional_per_industry_pct=args.max_notional_per_industry_pct,
        max_trades_per_symbol_per_month=args.max_trades_per_symbol_per_month,
        not_bear_regime=args.not_bear_regime,
        avoid_weak_sectors=args.avoid_weak_sectors,
    )
    trades, daily_equity, grid, monthly_returns, report_path = run_closed_limit_up_portfolio_simulation(
        db_path=args.db,
        oos_trades_path=args.oos_trades,
        output_dir=args.output_dir,
        base_config=base_config,
        run_grid_search=args.run_grid,
    )
    print(f"Wrote {len(trades)} selected trades to {args.output_dir / PORTFOLIO_TRADES_OUTPUT}")
    print(f"Wrote {len(daily_equity)} daily equity rows to {args.output_dir / PORTFOLIO_EQUITY_OUTPUT}")
    print(f"Wrote {len(grid)} grid rows to {args.output_dir / PORTFOLIO_GRID_OUTPUT}")
    print(f"Wrote {len(monthly_returns)} monthly rows to {args.output_dir / PORTFOLIO_MONTHLY_OUTPUT}")
    print(f"Wrote report to {report_path}")
    if not grid.empty:
        display_columns = [
            "initial_capital_twd",
            "max_positions_per_day",
            "ranking_method",
            "fixed_notional_twd",
            "not_bear_regime",
            "avoid_weak_sectors",
            "trades",
            "total_net_pnl",
            "annualized_return",
            "max_drawdown_pct",
            "portfolio_score",
        ]
        print("\nTop portfolio grid rows")
        print(grid[display_columns].head(10).to_string(index=False))


if __name__ == "__main__":
    main()
