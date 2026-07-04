"""Symbol-level concentration and repeat-event diagnostics."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from config.settings import get_settings
from src.db import get_connection, init_db
from src.reports.diagnose_closed_limit_up_execution import load_and_filter_oos_trades
from src.reports.diagnose_sector_concentration import calculate_metrics, markdown_table


SYMBOL_SUMMARY_OUTPUT = "closed_limit_up_symbol_summary.csv"
SYMBOL_CONCENTRATION_OUTPUT = "closed_limit_up_symbol_concentration.csv"
HOTNESS_SUMMARY_OUTPUT = "closed_limit_up_symbol_hotness_summary.csv"
COOLDOWN_TESTS_OUTPUT = "closed_limit_up_symbol_cooldown_tests.csv"
REMOVE_TOP_TESTS_OUTPUT = "closed_limit_up_symbol_remove_top_tests.csv"
REPORT_OUTPUT = "closed_limit_up_symbol_dependency_report.md"

SUMMARY_COLUMNS = [
    "symbol",
    "name",
    "sector",
    "industry",
    "trades",
    "avg_net_return",
    "median_net_return",
    "win_rate",
    "profit_factor",
    "total_net_pnl",
    "max_drawdown",
    "first_trade_date",
    "last_trade_date",
    "active_quarters",
    "max_trades_single_quarter",
    "trade_share",
    "pnl_share",
]

CONCENTRATION_COLUMNS = [
    "symbols",
    "trades",
    "net_pnl",
    "top_1_symbol_trade_share",
    "top_5_symbol_trade_share",
    "top_10_symbol_trade_share",
    "top_20_symbol_trade_share",
    "top_1_symbol_pnl_share",
    "top_5_symbol_pnl_share",
    "top_10_symbol_pnl_share",
    "top_20_symbol_pnl_share",
    "hhi_by_symbol_trades",
    "hhi_by_symbol_pnl",
]

PERFORMANCE_COLUMNS = [
    "scenario",
    "trades",
    "trade_share",
    "avg_net_return",
    "median_net_return",
    "win_rate",
    "profit_factor",
    "total_net_pnl",
    "pnl_share",
    "max_drawdown",
]

HOTNESS_ORDER = ["first_seen", "repeat_within_20d", "repeat_21_60d", "repeat_61_252d", "old_repeat"]


def generate_symbol_dependency_diagnostic(
    *,
    db_path: Path | str,
    oos_trades_path: Path | str,
    output_dir: Path | str | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, Path]:
    """Generate symbol-level concentration and repeat-event reports."""

    init_db(db_path)
    report_dir = Path(output_dir) if output_dir is not None else get_settings().project_root / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)

    trades = load_and_filter_oos_trades(oos_trades_path)
    diagnostic = enrich_symbol_context(trades, db_path=db_path)
    trading_dates = load_trading_dates(db_path=db_path, market="TPEX", trades=diagnostic)
    diagnostic = add_repeat_event_features(diagnostic, trading_dates=trading_dates)
    symbol_summary = summarize_symbols(diagnostic)
    concentration = calculate_symbol_concentration(symbol_summary)
    hotness_summary = summarize_hotness_buckets(diagnostic)
    cooldown_tests = run_cooldown_stress_tests(diagnostic, trading_dates=trading_dates)
    remove_top_tests = run_remove_top_symbol_tests(diagnostic, symbol_summary)
    report_text = build_symbol_dependency_report(
        diagnostic=diagnostic,
        symbol_summary=symbol_summary,
        concentration=concentration,
        hotness_summary=hotness_summary,
        cooldown_tests=cooldown_tests,
        remove_top_tests=remove_top_tests,
        db_path=Path(db_path),
        oos_trades_path=Path(oos_trades_path),
    )

    symbol_summary.to_csv(report_dir / SYMBOL_SUMMARY_OUTPUT, index=False)
    concentration.to_csv(report_dir / SYMBOL_CONCENTRATION_OUTPUT, index=False)
    hotness_summary.to_csv(report_dir / HOTNESS_SUMMARY_OUTPUT, index=False)
    cooldown_tests.to_csv(report_dir / COOLDOWN_TESTS_OUTPUT, index=False)
    remove_top_tests.to_csv(report_dir / REMOVE_TOP_TESTS_OUTPUT, index=False)
    report_path = report_dir / REPORT_OUTPUT
    report_path.write_text(report_text, encoding="utf-8")
    return symbol_summary, concentration, hotness_summary, cooldown_tests, remove_top_tests, report_path


def enrich_symbol_context(trades: pd.DataFrame, *, db_path: Path | str) -> pd.DataFrame:
    """Join best-strategy OOS trades to daily names, events, and sector map."""

    if trades.empty:
        return empty_diagnostic_trades()
    frame = trades.copy()
    frame["symbol"] = frame["symbol"].astype("string").str.strip()
    frame["market"] = frame["market"].astype("string").str.upper().str.strip()
    frame["signal_date"] = pd.to_datetime(frame["signal_date"], errors="coerce").dt.normalize()
    keys = frame[["_diagnostic_row_id", "symbol", "market", "signal_date", "event_id"]].copy()
    keys["signal_date"] = pd.to_datetime(keys["signal_date"], errors="coerce").dt.date

    with get_connection(db_path, read_only=True) as conn:
        conn.register("_symbol_keys", keys)
        context = conn.execute(
            """
            SELECT
                k._diagnostic_row_id,
                dp.name AS daily_price_name,
                m.name AS mapped_name,
                m.sector,
                m.industry,
                ec.event_type AS event_candidate_type,
                ec.next_trade_date AS event_next_trade_date
            FROM _symbol_keys k
            LEFT JOIN daily_prices dp
              ON dp.symbol = k.symbol
             AND dp.market = k.market
             AND dp.trade_date = k.signal_date
            LEFT JOIN event_candidates ec
              ON ec.event_id = k.event_id
            LEFT JOIN stock_sector_map m
              ON m.symbol = k.symbol
             AND m.market = k.market
            """
        ).fetch_df()

    joined = frame.merge(context, on="_diagnostic_row_id", how="left")
    joined["name"] = coalesce_text_columns(joined, ["mapped_name", "daily_price_name", "name", "symbol"])
    joined["sector"] = clean_group_values(joined.get("sector"), missing_label="MISSING_SECTOR")
    joined["industry"] = clean_group_values(joined.get("industry"), missing_label="MISSING_INDUSTRY")
    for column in ["signal_date", "entry_date", "exit_date", "day1_trade_date", "event_next_trade_date"]:
        if column in joined.columns:
            joined[column] = pd.to_datetime(joined[column], errors="coerce").dt.normalize()
    return select_diagnostic_columns(joined)


def load_trading_dates(*, db_path: Path | str, market: str, trades: pd.DataFrame) -> list[pd.Timestamp]:
    """Load market trading dates from daily_prices, falling back to OOS signal dates."""

    if trades.empty:
        return []
    start = pd.to_datetime(trades["signal_date"], errors="coerce").min()
    end = pd.to_datetime(trades["signal_date"], errors="coerce").max()
    with get_connection(db_path, read_only=True) as conn:
        dates = conn.execute(
            """
            SELECT DISTINCT trade_date
            FROM daily_prices
            WHERE market = ?
              AND trade_date >= ?
              AND trade_date <= ?
            ORDER BY trade_date
            """,
            [market, pd.Timestamp(start).date(), pd.Timestamp(end).date()],
        ).fetch_df()
    if dates.empty:
        return sorted(pd.to_datetime(trades["signal_date"], errors="coerce").dropna().dt.normalize().unique())
    return sorted(pd.to_datetime(dates["trade_date"], errors="coerce").dropna().dt.normalize().unique())


def add_repeat_event_features(trades: pd.DataFrame, *, trading_dates: list[pd.Timestamp]) -> pd.DataFrame:
    """Compute symbol repeat features and hotness buckets."""

    if trades.empty:
        return trades.copy()
    frame = trades.copy().sort_values(["symbol", "signal_date", "trade_id"]).reset_index(drop=True)
    frame["signal_date"] = pd.to_datetime(frame["signal_date"], errors="coerce").dt.normalize()
    trading_index = {pd.Timestamp(date).normalize(): index for index, date in enumerate(trading_dates)}
    frame["signal_trading_day_index"] = frame["signal_date"].map(trading_index)
    rows: list[pd.DataFrame] = []
    for _symbol, group in frame.groupby("symbol", sort=False, dropna=False):
        group = group.sort_values(["signal_date", "trade_id"]).copy()
        prior_dates: list[pd.Timestamp] = []
        days_since: list[float] = []
        prior_20: list[int] = []
        prior_60: list[int] = []
        prior_252: list[int] = []
        buckets: list[str] = []
        for current in group["signal_date"]:
            if not prior_dates:
                days_since.append(np.nan)
                prior_20.append(0)
                prior_60.append(0)
                prior_252.append(0)
                buckets.append("first_seen")
            else:
                deltas = np.array([(current - previous).days for previous in prior_dates], dtype=float)
                positive_or_zero = deltas[deltas >= 0]
                last_delta = float((current - prior_dates[-1]).days)
                days_since.append(last_delta)
                prior_20.append(int(((positive_or_zero >= 0) & (positive_or_zero <= 20)).sum()))
                prior_60.append(int(((positive_or_zero >= 0) & (positive_or_zero <= 60)).sum()))
                prior_252.append(int(((positive_or_zero >= 0) & (positive_or_zero <= 252)).sum()))
                if last_delta <= 20:
                    buckets.append("repeat_within_20d")
                elif last_delta <= 60:
                    buckets.append("repeat_21_60d")
                elif last_delta <= 252:
                    buckets.append("repeat_61_252d")
                else:
                    buckets.append("old_repeat")
            prior_dates.append(current)
        group["days_since_previous_signal_same_symbol"] = days_since
        group["prior_signals_same_symbol_20d"] = prior_20
        group["prior_signals_same_symbol_60d"] = prior_60
        group["prior_signals_same_symbol_252d"] = prior_252
        group["symbol_hotness_bucket"] = buckets
        rows.append(group)
    output = pd.concat(rows, ignore_index=True)
    output["symbol_hotness_bucket"] = pd.Categorical(output["symbol_hotness_bucket"], categories=HOTNESS_ORDER, ordered=True)
    return output.sort_values(["signal_date", "symbol", "trade_id"]).reset_index(drop=True)


def summarize_symbols(trades: pd.DataFrame) -> pd.DataFrame:
    """Create symbol-level performance and activity summary."""

    if trades.empty:
        return pd.DataFrame(columns=SUMMARY_COLUMNS)
    frame = trades.copy()
    frame["signal_date"] = pd.to_datetime(frame["signal_date"], errors="coerce").dt.normalize()
    frame["quarter"] = frame["signal_date"].dt.to_period("Q").astype(str)
    total_trades = len(frame)
    total_pnl = pd.to_numeric(frame["net_pnl"], errors="coerce").fillna(0.0).sum()
    rows: list[dict[str, Any]] = []
    for symbol, group in frame.groupby("symbol", dropna=False, observed=False):
        metrics = calculate_metrics(group)
        quarter_counts = group.groupby("quarter", dropna=False).size()
        rows.append(
            {
                "symbol": str(symbol),
                "name": most_common_text(group.get("name")),
                "sector": most_common_text(group.get("sector")),
                "industry": most_common_text(group.get("industry")),
                "trades": metrics["trades"],
                "avg_net_return": metrics["avg_net_return"],
                "median_net_return": metrics["median_net_return"],
                "win_rate": metrics["win_rate"],
                "profit_factor": metrics["profit_factor"],
                "total_net_pnl": metrics["total_net_pnl"],
                "max_drawdown": metrics["max_drawdown"],
                "first_trade_date": group["signal_date"].min().date(),
                "last_trade_date": group["signal_date"].max().date(),
                "active_quarters": int(group["quarter"].nunique(dropna=True)),
                "max_trades_single_quarter": int(quarter_counts.max()) if not quarter_counts.empty else 0,
                "trade_share": metrics["trades"] / total_trades if total_trades else np.nan,
                "pnl_share": metrics["total_net_pnl"] / total_pnl if total_pnl else np.nan,
            }
        )
    return pd.DataFrame(rows, columns=SUMMARY_COLUMNS).sort_values(
        ["total_net_pnl", "trades"],
        ascending=[False, False],
    ).reset_index(drop=True)


def calculate_symbol_concentration(symbol_summary: pd.DataFrame) -> pd.DataFrame:
    """Compute symbol-level concentration metrics."""

    if symbol_summary.empty:
        return pd.DataFrame(columns=CONCENTRATION_COLUMNS)
    trades = pd.to_numeric(symbol_summary["trades"], errors="coerce").fillna(0.0)
    pnl = pd.to_numeric(symbol_summary["total_net_pnl"], errors="coerce").fillna(0.0)
    total_trades = trades.sum()
    total_pnl = pnl.sum()
    trade_shares = trades / total_trades if total_trades else trades * np.nan
    abs_pnl = pnl.abs()
    abs_pnl_shares = abs_pnl / abs_pnl.sum() if abs_pnl.sum() else abs_pnl * np.nan
    by_trades = symbol_summary.assign(_share=trade_shares).sort_values("_share", ascending=False)
    by_pnl = symbol_summary.assign(_pnl=pnl).sort_values("_pnl", ascending=False)
    row = {
        "symbols": int(symbol_summary["symbol"].nunique(dropna=False)),
        "trades": int(total_trades),
        "net_pnl": float(total_pnl),
        "top_1_symbol_trade_share": top_n_sum(by_trades["_share"], 1),
        "top_5_symbol_trade_share": top_n_sum(by_trades["_share"], 5),
        "top_10_symbol_trade_share": top_n_sum(by_trades["_share"], 10),
        "top_20_symbol_trade_share": top_n_sum(by_trades["_share"], 20),
        "top_1_symbol_pnl_share": top_n_pnl_share(by_pnl["_pnl"], total_pnl, 1),
        "top_5_symbol_pnl_share": top_n_pnl_share(by_pnl["_pnl"], total_pnl, 5),
        "top_10_symbol_pnl_share": top_n_pnl_share(by_pnl["_pnl"], total_pnl, 10),
        "top_20_symbol_pnl_share": top_n_pnl_share(by_pnl["_pnl"], total_pnl, 20),
        "hhi_by_symbol_trades": float((trade_shares**2).sum()),
        "hhi_by_symbol_pnl": float((abs_pnl_shares**2).sum()),
    }
    return pd.DataFrame([row], columns=CONCENTRATION_COLUMNS)


def summarize_hotness_buckets(trades: pd.DataFrame) -> pd.DataFrame:
    """Summarize returns by symbol hotness bucket."""

    if trades.empty:
        return pd.DataFrame(columns=["symbol_hotness_bucket", "trades", "avg_net_return", "median_net_return", "win_rate", "profit_factor", "net_pnl"])
    rows = []
    for bucket, group in trades.groupby("symbol_hotness_bucket", dropna=False, observed=False):
        metrics = calculate_metrics(group)
        rows.append(
            {
                "symbol_hotness_bucket": str(bucket),
                "trades": metrics["trades"],
                "avg_net_return": metrics["avg_net_return"],
                "median_net_return": metrics["median_net_return"],
                "win_rate": metrics["win_rate"],
                "profit_factor": metrics["profit_factor"],
                "net_pnl": metrics["total_net_pnl"],
            }
        )
    order = {bucket: index for index, bucket in enumerate(HOTNESS_ORDER)}
    return pd.DataFrame(rows).sort_values(
        by="symbol_hotness_bucket",
        key=lambda values: values.map(order).fillna(999),
    ).reset_index(drop=True)


def run_cooldown_stress_tests(trades: pd.DataFrame, *, trading_dates: list[pd.Timestamp]) -> pd.DataFrame:
    """Run symbol cooldown stress tests."""

    scenarios: dict[str, pd.DataFrame] = {
        "no_cooldown": trades.copy(),
        "cooldown_5_trading_days": apply_trading_day_cooldown(trades, trading_dates=trading_dates, cooldown_days=5),
        "cooldown_10_trading_days": apply_trading_day_cooldown(trades, trading_dates=trading_dates, cooldown_days=10),
        "cooldown_20_trading_days": apply_trading_day_cooldown(trades, trading_dates=trading_dates, cooldown_days=20),
        "cooldown_60_trading_days": apply_trading_day_cooldown(trades, trading_dates=trading_dates, cooldown_days=60),
        "max_1_trade_per_symbol_per_month": apply_period_trade_cap(trades, period="M", max_trades=1),
        "max_2_trades_per_symbol_per_quarter": apply_period_trade_cap(trades, period="Q", max_trades=2),
    }
    return summarize_scenarios(scenarios, baseline=trades)


def run_remove_top_symbol_tests(trades: pd.DataFrame, symbol_summary: pd.DataFrame) -> pd.DataFrame:
    """Run remove-top-symbol stress tests."""

    top_by_pnl = symbol_summary.sort_values("total_net_pnl", ascending=False)["symbol"].tolist()
    top_by_trades = symbol_summary.sort_values(["trades", "total_net_pnl"], ascending=[False, False])["symbol"].tolist()
    scenarios = {
        "trade_all": trades.copy(),
        "remove_top_1_symbol_by_pnl": trades[~trades["symbol"].isin(top_by_pnl[:1])],
        "remove_top_5_symbols_by_pnl": trades[~trades["symbol"].isin(top_by_pnl[:5])],
        "remove_top_10_symbols_by_pnl": trades[~trades["symbol"].isin(top_by_pnl[:10])],
        "remove_top_20_symbols_by_pnl": trades[~trades["symbol"].isin(top_by_pnl[:20])],
        "remove_top_1_symbol_by_trade_count": trades[~trades["symbol"].isin(top_by_trades[:1])],
        "remove_top_5_symbols_by_trade_count": trades[~trades["symbol"].isin(top_by_trades[:5])],
        "remove_top_10_symbols_by_trade_count": trades[~trades["symbol"].isin(top_by_trades[:10])],
    }
    return summarize_scenarios(scenarios, baseline=trades)


def apply_trading_day_cooldown(
    trades: pd.DataFrame,
    *,
    trading_dates: list[pd.Timestamp],
    cooldown_days: int,
) -> pd.DataFrame:
    """Keep trades only if the same symbol was not kept within cooldown trading days."""

    if trades.empty:
        return trades.copy()
    trading_index = {pd.Timestamp(date).normalize(): index for index, date in enumerate(trading_dates)}
    frame = trades.copy().sort_values(["signal_date", "symbol", "trade_id"]).reset_index(drop=True)
    frame["_td_index"] = pd.to_datetime(frame["signal_date"], errors="coerce").dt.normalize().map(trading_index)
    if frame["_td_index"].isna().any():
        fallback_dates = sorted(pd.to_datetime(frame["signal_date"], errors="coerce").dropna().dt.normalize().unique())
        fallback_index = {date: index for index, date in enumerate(fallback_dates)}
        frame["_td_index"] = pd.to_datetime(frame["signal_date"], errors="coerce").dt.normalize().map(fallback_index)
    keep = []
    last_kept_by_symbol: dict[str, float] = {}
    for _, row in frame.iterrows():
        symbol = str(row["symbol"])
        current_index = float(row["_td_index"])
        last_index = last_kept_by_symbol.get(symbol)
        if last_index is not None and current_index - last_index <= cooldown_days:
            keep.append(False)
            continue
        keep.append(True)
        last_kept_by_symbol[symbol] = current_index
    return frame.loc[keep].drop(columns=["_td_index"]).reset_index(drop=True)


def apply_period_trade_cap(trades: pd.DataFrame, *, period: str, max_trades: int) -> pd.DataFrame:
    """Limit each symbol to N trades per calendar month or quarter."""

    if trades.empty:
        return trades.copy()
    frame = trades.copy().sort_values(["signal_date", "symbol", "trade_id"]).reset_index(drop=True)
    frame["_period"] = pd.to_datetime(frame["signal_date"], errors="coerce").dt.to_period(period).astype(str)
    frame["_rank"] = frame.groupby(["symbol", "_period"], dropna=False).cumcount() + 1
    return frame[frame["_rank"] <= max_trades].drop(columns=["_period", "_rank"]).reset_index(drop=True)


def summarize_scenarios(scenarios: dict[str, pd.DataFrame], *, baseline: pd.DataFrame) -> pd.DataFrame:
    """Summarize a mapping of scenario name to trade subset."""

    total_trades = len(baseline)
    total_pnl = pd.to_numeric(baseline.get("net_pnl"), errors="coerce").fillna(0.0).sum()
    rows = []
    for scenario, frame in scenarios.items():
        metrics = calculate_metrics(frame)
        rows.append(
            {
                "scenario": scenario,
                "trades": metrics["trades"],
                "trade_share": metrics["trades"] / total_trades if total_trades else np.nan,
                "avg_net_return": metrics["avg_net_return"],
                "median_net_return": metrics["median_net_return"],
                "win_rate": metrics["win_rate"],
                "profit_factor": metrics["profit_factor"],
                "total_net_pnl": metrics["total_net_pnl"],
                "pnl_share": metrics["total_net_pnl"] / total_pnl if total_pnl else np.nan,
                "max_drawdown": metrics["max_drawdown"],
            }
        )
    return pd.DataFrame(rows, columns=PERFORMANCE_COLUMNS)


def top_n_sum(values: pd.Series, n: int) -> float:
    return float(pd.to_numeric(values, errors="coerce").fillna(0.0).head(n).sum())


def top_n_pnl_share(values: pd.Series, total_pnl: float, n: int) -> float:
    if not total_pnl:
        return np.nan
    return float(pd.to_numeric(values, errors="coerce").fillna(0.0).head(n).sum() / total_pnl)


def most_common_text(values: pd.Series | None) -> str:
    if values is None:
        return ""
    cleaned = values.astype("string").fillna("").str.strip()
    cleaned = cleaned[cleaned.str.len() > 0]
    if cleaned.empty:
        return ""
    return str(cleaned.mode().iloc[0])


def coalesce_text_columns(frame: pd.DataFrame, columns: list[str]) -> pd.Series:
    result = pd.Series("", index=frame.index, dtype="string")
    for column in columns:
        if column not in frame.columns:
            continue
        values = frame[column].astype("string").fillna("").str.strip()
        result = result.mask(result.str.len() == 0, values)
    return result.fillna("").astype("string")


def clean_group_values(values: pd.Series | None, *, missing_label: str) -> pd.Series:
    if values is None:
        return pd.Series([missing_label], dtype="string")
    cleaned = values.astype("string").fillna("").str.strip()
    return cleaned.mask(cleaned.str.len() == 0, missing_label).astype("string")


def select_diagnostic_columns(frame: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "_diagnostic_row_id",
        "window_id",
        "selected_rank",
        "config_hash",
        "trade_id",
        "event_id",
        "symbol",
        "market",
        "name",
        "sector",
        "industry",
        "signal_date",
        "entry_date",
        "exit_date",
        "net_return",
        "net_pnl",
        "buy_notional",
        "day0_close",
        "day0_turnover_twd",
        "volume_ratio_20d",
        "consecutive_limit_up_count",
        "event_candidate_type",
        "event_next_trade_date",
    ]
    for column in columns:
        if column not in frame.columns:
            frame[column] = np.nan
    return frame[columns].sort_values(["signal_date", "symbol", "trade_id"]).reset_index(drop=True)


def empty_diagnostic_trades() -> pd.DataFrame:
    return pd.DataFrame(columns=select_diagnostic_columns(pd.DataFrame()).columns)


def build_symbol_dependency_report(
    *,
    diagnostic: pd.DataFrame,
    symbol_summary: pd.DataFrame,
    concentration: pd.DataFrame,
    hotness_summary: pd.DataFrame,
    cooldown_tests: pd.DataFrame,
    remove_top_tests: pd.DataFrame,
    db_path: Path,
    oos_trades_path: Path,
) -> str:
    """Build Markdown report text."""

    lines = [
        "# Closed-Limit-Up Symbol Dependency Diagnostic",
        "",
        f"Source OOS trades: `{oos_trades_path}`",
        f"Database: `{db_path}`",
        "",
        "## Executive Answer",
        "",
        symbol_dependency_verdict(concentration, cooldown_tests, remove_top_tests),
        "",
        "## Coverage",
        "",
        f"Best-strategy OOS trades: {len(diagnostic):,}",
        f"Unique symbols: {symbol_summary['symbol'].nunique(dropna=False) if not symbol_summary.empty else 0:,}",
        "",
        "## Symbol Concentration",
        "",
        markdown_table(concentration),
        "",
        "## Repeat Hotness",
        "",
        markdown_table(hotness_summary),
        "",
        "## Cooldown Tests",
        "",
        markdown_table(cooldown_tests),
        "",
        "## Remove Top Symbol Tests",
        "",
        markdown_table(remove_top_tests),
        "",
        "## Top Symbols By PnL",
        "",
        markdown_table(symbol_summary.head(30)),
        "",
    ]
    return "\n".join(lines)


def symbol_dependency_verdict(
    concentration: pd.DataFrame,
    cooldown_tests: pd.DataFrame,
    remove_top_tests: pd.DataFrame,
) -> str:
    if concentration.empty:
        return "No symbol rows were available."
    top10_trade = float(concentration["top_10_symbol_trade_share"].iloc[0])
    hhi_trades = float(concentration["hhi_by_symbol_trades"].iloc[0])
    remove_top10 = remove_top_tests[remove_top_tests["scenario"].eq("remove_top_10_symbols_by_pnl")]
    cooldown20 = cooldown_tests[cooldown_tests["scenario"].eq("cooldown_20_trading_days")]
    remove_top10_positive = (not remove_top10.empty) and float(remove_top10["total_net_pnl"].iloc[0]) > 0
    cooldown20_positive = (not cooldown20.empty) and float(cooldown20["total_net_pnl"].iloc[0]) > 0
    if top10_trade < 0.35 and hhi_trades < 0.05 and remove_top10_positive and cooldown20_positive:
        return (
            "Strict verdict: the edge is not dominated by a small set of symbols. It survives removing the "
            "top PnL symbols and survives a 20-trading-day cooldown."
        )
    if remove_top10_positive and cooldown20_positive:
        return (
            "Strict verdict: the edge survives symbol stress tests, but repeat-symbol dependence is material. "
            "Use per-symbol exposure caps and cooldowns."
        )
    return (
        "Strict verdict: symbol dependency is too high. Do not treat the edge as broad until it survives "
        "top-symbol removal and cooldown constraints."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnose symbol concentration and repeat dependency")
    parser.add_argument("--db", type=Path, default=get_settings().db_path)
    parser.add_argument(
        "--oos-trades",
        type=Path,
        default=get_settings().project_root / "reports" / "walk_forward_closed_limit_up_overnight_oos_trades.csv",
    )
    parser.add_argument("--output-dir", type=Path, default=get_settings().project_root / "reports")
    args = parser.parse_args()

    symbol_summary, concentration, hotness, cooldown, remove_top, report_path = generate_symbol_dependency_diagnostic(
        db_path=args.db,
        oos_trades_path=args.oos_trades,
        output_dir=args.output_dir,
    )
    print(f"Wrote {len(symbol_summary)} rows to {args.output_dir / SYMBOL_SUMMARY_OUTPUT}")
    print(f"Wrote {len(concentration)} rows to {args.output_dir / SYMBOL_CONCENTRATION_OUTPUT}")
    print(f"Wrote {len(hotness)} rows to {args.output_dir / HOTNESS_SUMMARY_OUTPUT}")
    print(f"Wrote {len(cooldown)} rows to {args.output_dir / COOLDOWN_TESTS_OUTPUT}")
    print(f"Wrote {len(remove_top)} rows to {args.output_dir / REMOVE_TOP_TESTS_OUTPUT}")
    print(f"Wrote report to {report_path}")
    print("\nSymbol cooldown tests")
    print(cooldown.to_string(index=False))
    print("\nRemove top symbol tests")
    print(remove_top.to_string(index=False))


if __name__ == "__main__":
    main()
