"""Execution-risk diagnostic for the best closed-limit-up overnight strategy."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from config.settings import get_settings
from src.db import get_connection


BEST_MARKET = "TPEX"
BEST_FILL_ASSUMPTION = "moderate"
BEST_MIN_FILL_SCORE = 60.0
BEST_MIN_TURNOVER_TWD = 500_000_000.0
BEST_MIN_VOLUME_RATIO_20D = 1.5
BEST_MAX_CONSECUTIVE_LIMIT_UPS = 3
BEST_MIN_PRICE = 10.0
BEST_MAX_PRICE = 100.0

EXECUTION_BUCKET_ORDER = [
    "likely_hard_fill",
    "possible_fill",
    "likely_fillable",
    "very_fillable_proxy",
    "uncertain_daily_proxy",
]

DIAGNOSTIC_OUTPUT = "closed_limit_up_execution_diagnostic.csv"
BUCKET_SUMMARY_OUTPUT = "closed_limit_up_execution_bucket_summary.csv"
STRESS_TEST_OUTPUT = "closed_limit_up_execution_stress_tests.csv"
MONTE_CARLO_OUTPUT = "closed_limit_up_execution_monte_carlo.csv"
MARKDOWN_OUTPUT = "closed_limit_up_execution_report.md"


def generate_closed_limit_up_execution_diagnostic(
    *,
    db_path: Path | str,
    oos_trades_path: Path | str,
    output_dir: Path | str | None = None,
    monte_carlo_iterations: int = 1000,
    random_seed: int = 42,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, Path]:
    """Create CSV and Markdown execution diagnostics for the robust OOS strategy."""

    if monte_carlo_iterations <= 0:
        raise ValueError("monte_carlo_iterations must be positive")

    report_dir = Path(output_dir) if output_dir is not None else get_settings().project_root / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)

    trades = load_and_filter_oos_trades(oos_trades_path)
    diagnostic = enrich_with_database_context(trades, db_path=db_path)
    diagnostic = compute_execution_features(diagnostic)
    bucket_summary = summarize_execution_buckets(diagnostic)
    stress_tests = run_execution_stress_tests(diagnostic)
    monte_carlo = run_fill_haircut_monte_carlo(
        diagnostic,
        iterations=monte_carlo_iterations,
        random_seed=random_seed,
    )
    report_text = build_markdown_report(
        diagnostic=diagnostic,
        bucket_summary=bucket_summary,
        stress_tests=stress_tests,
        monte_carlo=monte_carlo,
        db_path=Path(db_path),
        oos_trades_path=Path(oos_trades_path),
    )

    diagnostic.to_csv(report_dir / DIAGNOSTIC_OUTPUT, index=False)
    bucket_summary.to_csv(report_dir / BUCKET_SUMMARY_OUTPUT, index=False)
    stress_tests.to_csv(report_dir / STRESS_TEST_OUTPUT, index=False)
    monte_carlo.to_csv(report_dir / MONTE_CARLO_OUTPUT, index=False)
    report_path = report_dir / MARKDOWN_OUTPUT
    report_path.write_text(report_text, encoding="utf-8")
    return diagnostic, bucket_summary, stress_tests, monte_carlo, report_path


def load_and_filter_oos_trades(oos_trades_path: Path | str) -> pd.DataFrame:
    """Load OOS trades and isolate the best robust strategy parameter family."""

    path = Path(oos_trades_path)
    if not path.exists():
        raise FileNotFoundError(f"OOS trades file does not exist: {path}")
    trades = pd.read_csv(path)
    if trades.empty:
        return trades

    required = {"symbol", "market", "signal_date", "entry_date", "net_return", "net_pnl"}
    missing = sorted(required - set(trades.columns))
    if missing:
        raise ValueError(f"OOS trades file is missing required columns: {missing}")

    frame = trades.copy()
    market_param = frame.get("market_param", frame["market"]).astype("string").str.upper()
    fill_assumption = frame.get("fill_assumption_param", frame.get("fill_assumption", "")).astype("string").str.lower()
    min_fill_score = numeric_or_default(frame, "min_fill_quality_score_param", frame.get("fill_quality_score", np.nan))
    min_turnover = numeric_or_default(frame, "min_turnover_twd_param", frame.get("day0_turnover_twd", np.nan))
    min_volume_ratio = numeric_or_default(
        frame,
        "min_volume_ratio_20d_param",
        frame.get("volume_ratio_20d", np.nan),
    )
    max_consecutive = numeric_or_default(
        frame,
        "max_consecutive_limit_ups_param",
        frame.get("consecutive_limit_up_count", np.nan),
    )
    day0_price = numeric_or_default(frame, "day0_close", frame.get("entry_price", np.nan))

    mask = (
        market_param.eq(BEST_MARKET)
        & fill_assumption.eq(BEST_FILL_ASSUMPTION)
        & (min_fill_score >= BEST_MIN_FILL_SCORE)
        & (min_turnover >= BEST_MIN_TURNOVER_TWD)
        & (min_volume_ratio >= BEST_MIN_VOLUME_RATIO_20D)
        & (max_consecutive <= BEST_MAX_CONSECUTIVE_LIMIT_UPS)
        & (day0_price >= BEST_MIN_PRICE)
        & (day0_price <= BEST_MAX_PRICE)
    )
    filtered = frame[mask.fillna(False)].copy().reset_index(drop=True)
    filtered["_diagnostic_row_id"] = np.arange(len(filtered))
    for column in ["signal_date", "entry_date", "exit_date", "day1_trade_date"]:
        if column in filtered.columns:
            filtered[column] = pd.to_datetime(filtered[column], errors="coerce").dt.normalize()
    return filtered


def enrich_with_database_context(trades: pd.DataFrame, *, db_path: Path | str) -> pd.DataFrame:
    """Join filtered trades to daily prices, events, and turnover history."""

    if trades.empty:
        return trades.copy()

    keys = trades[["_diagnostic_row_id", "symbol", "market", "signal_date", "event_id"]].copy()
    keys["signal_date"] = pd.to_datetime(keys["signal_date"], errors="coerce").dt.date
    symbols = trades[["symbol", "market"]].drop_duplicates().copy()

    with get_connection(db_path, read_only=True) as conn:
        conn.register("_execution_keys", keys)
        conn.register("_execution_symbols", symbols)
        day0 = conn.execute(
            """
            SELECT
                k._diagnostic_row_id,
                dp.open AS db_day0_open,
                dp.high AS db_day0_high,
                dp.low AS db_day0_low,
                dp.close AS db_day0_close,
                dp.volume_shares AS db_day0_volume_shares,
                dp.turnover_twd AS db_day0_turnover_twd,
                dp.trades AS db_day0_trades,
                dp.limit_up_price AS db_limit_up_price,
                dp.closed_limit_up AS db_closed_limit_up
            FROM _execution_keys k
            LEFT JOIN daily_prices dp
              ON dp.symbol = k.symbol
             AND dp.market = k.market
             AND dp.trade_date = k.signal_date
            """
        ).fetch_df()
        events = conn.execute(
            """
            SELECT
                k._diagnostic_row_id,
                ec.close_location AS db_close_location,
                ec.volume_ratio_20d AS db_volume_ratio_20d,
                ec.day0_turnover_twd AS db_event_day0_turnover_twd,
                ec.day0_volume_shares AS db_event_day0_volume_shares,
                ec.next_trade_date AS db_next_trade_date
            FROM _execution_keys k
            LEFT JOIN event_candidates ec
              ON ec.event_id = k.event_id
            """
        ).fetch_df()
        history = conn.execute(
            """
            SELECT
                dp.symbol,
                dp.market,
                dp.trade_date,
                dp.turnover_twd
            FROM daily_prices dp
            INNER JOIN _execution_symbols s
              ON s.symbol = dp.symbol
             AND s.market = dp.market
            ORDER BY dp.market, dp.symbol, dp.trade_date
            """
        ).fetch_df()

    enriched = trades.merge(day0, on="_diagnostic_row_id", how="left")
    enriched = enriched.merge(events, on="_diagnostic_row_id", how="left")
    turnover_percentiles = compute_turnover_percentile_by_symbol_252d(history)
    enriched["symbol"] = enriched["symbol"].astype("string")
    enriched["market"] = enriched["market"].astype("string").str.upper()
    turnover_percentiles["symbol"] = turnover_percentiles["symbol"].astype("string")
    turnover_percentiles["market"] = turnover_percentiles["market"].astype("string").str.upper()
    turnover_percentiles["trade_date"] = pd.to_datetime(turnover_percentiles["trade_date"], errors="coerce").dt.normalize()
    enriched["signal_date"] = pd.to_datetime(enriched["signal_date"], errors="coerce").dt.normalize()
    enriched = enriched.merge(
        turnover_percentiles,
        left_on=["symbol", "market", "signal_date"],
        right_on=["symbol", "market", "trade_date"],
        how="left",
    ).drop(columns=["trade_date"], errors="ignore")
    return enriched


def compute_execution_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Compute execution-risk proxy features and buckets."""

    output = frame.copy()
    if output.empty:
        return output

    output["day0_open_px"] = coalesce_numeric(output, ["db_day0_open", "day0_open"])
    output["day0_high_px"] = coalesce_numeric(output, ["db_day0_high", "day0_high"])
    output["day0_low_px"] = coalesce_numeric(output, ["db_day0_low", "day0_low"])
    output["day0_close_px"] = coalesce_numeric(output, ["db_day0_close", "day0_close", "entry_price"])
    output["day0_limit_up_price"] = coalesce_numeric(output, ["db_limit_up_price", "limit_up_price"])
    output["day0_turnover_twd"] = coalesce_numeric(
        output,
        ["db_day0_turnover_twd", "db_event_day0_turnover_twd", "day0_turnover_twd", "turnover_twd"],
    )
    output["day0_volume_shares"] = coalesce_numeric(
        output,
        ["db_day0_volume_shares", "db_event_day0_volume_shares", "day0_volume_shares"],
    )
    output["day0_trades"] = coalesce_numeric(output, ["db_day0_trades", "trades"])
    output["volume_ratio_20d"] = coalesce_numeric(output, ["db_volume_ratio_20d", "volume_ratio_20d"])
    output["consecutive_limit_up_count"] = coalesce_numeric(output, ["consecutive_limit_up_count"])
    output["prior_5d_return"] = coalesce_numeric(output, ["prior_5d_return"])
    output["prior_20d_return"] = coalesce_numeric(output, ["prior_20d_return"])
    output["next_open_gap"] = coalesce_numeric(output, ["day1_open_gap", "gross_return"])
    output["net_return"] = pd.to_numeric(output["net_return"], errors="coerce")
    output["net_pnl"] = pd.to_numeric(output["net_pnl"], errors="coerce")
    output["buy_notional"] = pd.to_numeric(output.get("buy_notional", np.nan), errors="coerce")

    output["day0_range_pct"] = np.where(
        output["day0_low_px"] > 0,
        output["day0_high_px"] / output["day0_low_px"] - 1.0,
        np.nan,
    )
    output["day0_open_to_close_return"] = np.where(
        output["day0_open_px"] > 0,
        output["day0_close_px"] / output["day0_open_px"] - 1.0,
        np.nan,
    )
    output["day0_high_equals_close"] = np.isclose(
        output["day0_high_px"],
        output["day0_close_px"],
        rtol=0.0,
        atol=1e-8,
        equal_nan=False,
    )
    output["day0_high_equals_low_equals_close"] = (
        np.isclose(output["day0_high_px"], output["day0_low_px"], rtol=0.0, atol=1e-8, equal_nan=False)
        & np.isclose(output["day0_low_px"], output["day0_close_px"], rtol=0.0, atol=1e-8, equal_nan=False)
    )
    output["day0_close_equals_limit_up"] = np.isclose(
        output["day0_close_px"],
        output["day0_limit_up_price"],
        rtol=0.0,
        atol=1e-8,
        equal_nan=False,
    )
    output["day0_turnover_per_trade"] = np.where(
        output["day0_trades"] > 0,
        output["day0_turnover_twd"] / output["day0_trades"],
        np.nan,
    )
    output["execution_risk_bucket"] = bucket_execution_risk(output)
    output["execution_risk_bucket"] = pd.Categorical(
        output["execution_risk_bucket"],
        categories=EXECUTION_BUCKET_ORDER,
        ordered=True,
    )
    return select_diagnostic_columns(output)


def bucket_execution_risk(frame: pd.DataFrame) -> pd.Series:
    """Classify daily bars into execution-risk proxy buckets."""

    range_pct = pd.to_numeric(frame["day0_range_pct"], errors="coerce")
    turnover = pd.to_numeric(frame["day0_turnover_twd"], errors="coerce")
    high_equals_close = frame["day0_high_equals_close"].fillna(False).astype(bool)
    close_equals_limit = frame["day0_close_equals_limit_up"].fillna(False).astype(bool)

    bucket = pd.Series("uncertain_daily_proxy", index=frame.index, dtype="string")
    bucket = bucket.mask(high_equals_close & close_equals_limit & (range_pct < 0.01), "likely_hard_fill")
    bucket = bucket.mask((range_pct >= 0.01) & (range_pct < 0.03), "possible_fill")
    bucket = bucket.mask((range_pct >= 0.03) & (turnover >= 500_000_000), "likely_fillable")
    bucket = bucket.mask((range_pct >= 0.05) & (turnover >= 1_000_000_000), "very_fillable_proxy")
    return bucket


def summarize_execution_buckets(diagnostic: pd.DataFrame) -> pd.DataFrame:
    """Summarize returns and contribution by execution bucket."""

    if diagnostic.empty:
        return pd.DataFrame(columns=bucket_summary_columns())
    total_trades = len(diagnostic)
    total_net_pnl = pd.to_numeric(diagnostic["net_pnl"], errors="coerce").fillna(0.0).sum()
    rows = []
    for bucket, group in diagnostic.groupby("execution_risk_bucket", dropna=False, observed=False):
        metrics = calculate_return_metrics(group)
        metrics.update(
            {
                "execution_risk_bucket": str(bucket),
                "trade_share": len(group) / total_trades if total_trades else np.nan,
                "net_pnl_share": metrics["total_net_pnl"] / total_net_pnl if total_net_pnl else np.nan,
                "avg_return_contribution": metrics["average_net_return"] * len(group) / total_trades if total_trades else np.nan,
            }
        )
        rows.append(metrics)
    return pd.DataFrame(rows, columns=bucket_summary_columns())


def run_execution_stress_tests(diagnostic: pd.DataFrame) -> pd.DataFrame:
    """Run deterministic execution stress scenarios."""

    scenarios = {
        "baseline_best_strategy": diagnostic.index == diagnostic.index,
        "remove_likely_hard_fill": diagnostic["execution_risk_bucket"].astype("string") != "likely_hard_fill",
        "remove_hard_and_possible_fill": ~diagnostic["execution_risk_bucket"].astype("string").isin(
            ["likely_hard_fill", "possible_fill"]
        ),
        "keep_only_likely_and_very_fillable": diagnostic["execution_risk_bucket"].astype("string").isin(
            ["likely_fillable", "very_fillable_proxy"]
        ),
    }
    rows: list[dict[str, Any]] = []
    baseline_count = len(diagnostic)
    baseline_pnl = pd.to_numeric(diagnostic["net_pnl"], errors="coerce").fillna(0.0).sum()
    for scenario, mask in scenarios.items():
        group = diagnostic[mask].copy()
        metrics = calculate_return_metrics(group)
        metrics.update(
            {
                "scenario": scenario,
                "kept_trades": len(group),
                "removed_trades": baseline_count - len(group),
                "kept_trade_share": len(group) / baseline_count if baseline_count else np.nan,
                "kept_pnl_share": metrics["total_net_pnl"] / baseline_pnl if baseline_pnl else np.nan,
            }
        )
        rows.append(metrics)
    return pd.DataFrame(rows, columns=stress_test_columns())


def run_fill_haircut_monte_carlo(
    diagnostic: pd.DataFrame,
    *,
    fill_rates: tuple[float, ...] = (0.25, 0.50, 0.75),
    iterations: int = 1000,
    random_seed: int = 42,
) -> pd.DataFrame:
    """Simulate random partial fills for each fill rate."""

    if iterations <= 0:
        raise ValueError("iterations must be positive")
    rng = np.random.default_rng(random_seed)
    net_pnl = pd.to_numeric(diagnostic.get("net_pnl", pd.Series(dtype=float)), errors="coerce").fillna(0.0).to_numpy()
    net_return = pd.to_numeric(diagnostic.get("net_return", pd.Series(dtype=float)), errors="coerce").fillna(0.0).to_numpy()
    buy_notional = pd.to_numeric(diagnostic.get("buy_notional", pd.Series(dtype=float)), errors="coerce").fillna(0.0).to_numpy()
    rows: list[dict[str, float]] = []
    n = len(net_pnl)
    for fill_rate in fill_rates:
        totals = np.zeros(iterations)
        weighted_returns = np.zeros(iterations)
        trade_counts = np.zeros(iterations)
        for i in range(iterations):
            if n == 0:
                mask = np.array([], dtype=bool)
            else:
                mask = rng.random(n) < fill_rate
            trade_counts[i] = mask.sum()
            totals[i] = net_pnl[mask].sum()
            notional = buy_notional[mask].sum()
            weighted_returns[i] = totals[i] / notional if notional > 0 else np.nan
            if not np.isfinite(weighted_returns[i]) and mask.any():
                weighted_returns[i] = net_return[mask].mean()
        clean_returns = pd.Series(weighted_returns).dropna()
        rows.append(
            {
                "fill_rate": fill_rate,
                "iterations": iterations,
                "mean_filled_trades": float(trade_counts.mean()),
                "median_filled_trades": float(np.median(trade_counts)),
                "mean_total_net_pnl": float(totals.mean()),
                "median_total_net_pnl": float(np.median(totals)),
                "p05_total_net_pnl": float(np.percentile(totals, 5)),
                "p95_total_net_pnl": float(np.percentile(totals, 95)),
                "mean_avg_net_return": float(clean_returns.mean()) if not clean_returns.empty else np.nan,
                "median_avg_net_return": float(clean_returns.median()) if not clean_returns.empty else np.nan,
                "p05_avg_net_return": float(clean_returns.quantile(0.05)) if not clean_returns.empty else np.nan,
                "p95_avg_net_return": float(clean_returns.quantile(0.95)) if not clean_returns.empty else np.nan,
            }
        )
    return pd.DataFrame(rows, columns=monte_carlo_columns())


def calculate_return_metrics(frame: pd.DataFrame) -> dict[str, float]:
    """Calculate standard return metrics for a trade subset."""

    if frame.empty:
        return {
            "trades": 0,
            "average_net_return": np.nan,
            "median_net_return": np.nan,
            "win_rate": np.nan,
            "profit_factor": np.nan,
            "total_net_pnl": 0.0,
            "max_drawdown": 0.0,
        }
    net_return = pd.to_numeric(frame["net_return"], errors="coerce")
    net_pnl = pd.to_numeric(frame["net_pnl"], errors="coerce").fillna(0.0)
    gains = net_pnl[net_pnl > 0].sum()
    losses = abs(net_pnl[net_pnl < 0].sum())
    if losses == 0 and gains > 0:
        profit_factor = float("inf")
    elif losses == 0:
        profit_factor = np.nan
    else:
        profit_factor = float(gains / losses)
    return {
        "trades": int(len(frame)),
        "average_net_return": float(net_return.mean()),
        "median_net_return": float(net_return.median()),
        "win_rate": float((net_pnl > 0).mean()),
        "profit_factor": profit_factor,
        "total_net_pnl": float(net_pnl.sum()),
        "max_drawdown": max_drawdown_by_exit_date(frame),
    }


def max_drawdown_by_exit_date(frame: pd.DataFrame) -> float:
    if frame.empty:
        return 0.0
    date_col = "exit_date" if "exit_date" in frame.columns else "entry_date"
    dated = frame.copy()
    dated["_pnl_date"] = pd.to_datetime(dated[date_col], errors="coerce").dt.normalize()
    daily = dated.groupby("_pnl_date", dropna=False)["net_pnl"].sum().sort_index()
    equity = pd.concat([pd.Series([0.0]), daily.reset_index(drop=True)], ignore_index=True).cumsum()
    drawdown = equity - equity.cummax()
    return float(drawdown.min())


def compute_turnover_percentile_by_symbol_252d(history: pd.DataFrame) -> pd.DataFrame:
    """Percentile of current turnover versus prior 252 rows for same symbol."""

    if history.empty:
        return pd.DataFrame(columns=["symbol", "market", "trade_date", "day0_turnover_percentile_by_symbol_252d"])
    frame = history.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce").dt.normalize()
    frame["turnover_twd"] = pd.to_numeric(frame["turnover_twd"], errors="coerce")
    frame = frame.sort_values(["market", "symbol", "trade_date"]).reset_index(drop=True)

    percentiles = np.full(len(frame), np.nan, dtype=float)
    for _, index in frame.groupby(["market", "symbol"], sort=False).groups.items():
        positions = np.asarray(index)
        values = frame.loc[positions, "turnover_twd"].to_numpy(dtype=float)
        for i, value in enumerate(values):
            start = max(0, i - 252)
            prior = values[start:i]
            prior = prior[np.isfinite(prior)]
            if prior.size and np.isfinite(value):
                percentiles[positions[i]] = float((prior <= value).mean())
    frame["day0_turnover_percentile_by_symbol_252d"] = percentiles
    return frame[["symbol", "market", "trade_date", "day0_turnover_percentile_by_symbol_252d"]]


def build_markdown_report(
    *,
    diagnostic: pd.DataFrame,
    bucket_summary: pd.DataFrame,
    stress_tests: pd.DataFrame,
    monte_carlo: pd.DataFrame,
    db_path: Path,
    oos_trades_path: Path,
) -> str:
    """Build a concise Markdown execution diagnostic report."""

    hard = bucket_row(bucket_summary, "likely_hard_fill")
    fillable = bucket_summary[
        bucket_summary["execution_risk_bucket"].isin(["likely_fillable", "very_fillable_proxy"])
    ]
    fillable_metrics = aggregate_summary_rows(fillable)
    stress_by_name = {row["scenario"]: row for _, row in stress_tests.iterrows()}
    remove_hard = stress_by_name.get("remove_likely_hard_fill", {})
    fillable_only = stress_by_name.get("keep_only_likely_and_very_fillable", {})
    verdict = execution_verdict(hard, remove_hard, fillable_only)

    lines = [
        "# Closed-Limit-Up Execution Diagnostic",
        "",
        f"Source OOS trades: `{oos_trades_path}`",
        f"Database: `{db_path}`",
        "",
        "## Executive Answer",
        "",
        verdict,
        "",
        "## Best Strategy Filter",
        "",
        "- Market: TPEX",
        "- Fill assumption: moderate",
        "- Minimum fill quality score: 60",
        "- Minimum turnover: 500M TWD",
        "- Minimum volume ratio 20D: 1.5",
        "- Consecutive limit-up count: <= 3",
        "- Price: 10 to 100 TWD",
        "",
        "## Execution Bucket Summary",
        "",
        markdown_table(bucket_summary),
        "",
        "## Profit Source",
        "",
        profit_source_text(hard, fillable_metrics),
        "",
        "## Stress Tests",
        "",
        markdown_table(stress_tests),
        "",
        "## Monte Carlo Partial-Fill Haircut",
        "",
        markdown_table(monte_carlo),
        "",
        "## Data Needed Next",
        "",
        "- Closing auction executable volume at limit-up price",
        "- Closing auction imbalance by price level",
        "- Best bid size and queue size at limit-up into the close",
        "- Timestamped order-book snapshots for the final 5 to 30 minutes",
        "- Actual matched volume at the closing auction",
        "- Broker/order queue priority or simulated queue-position fill probability",
        "- Opening auction imbalance and indicative open price on Day1",
        "- Whether the Day0 close print came from continuous trading or closing auction only",
        "",
    ]
    return "\n".join(lines)


def execution_verdict(hard: pd.Series, remove_hard: dict[str, Any], fillable_only: dict[str, Any]) -> str:
    hard_share = float(hard.get("net_pnl_share", 0.0)) if isinstance(hard, pd.Series) else 0.0
    remove_hard_pnl = float(remove_hard.get("total_net_pnl", np.nan))
    fillable_pnl = float(fillable_only.get("total_net_pnl", np.nan))
    if hard_share > 0.50 and remove_hard_pnl <= 0:
        return (
            "Strict verdict: the edge is too dependent on likely hard-fill locked names. "
            "Do not trade without order-book and closing-auction fill data."
        )
    if remove_hard_pnl > 0 and fillable_pnl > 0:
        return (
            "Strict verdict: the daily-data edge is not coming mostly from likely hard-fill names, "
            "and it remains positive after removing the hardest-fill bucket. It is still not proven "
            "live-executable until order-book and closing-auction data confirms Day0 close fills."
        )
    if remove_hard_pnl > 0:
        return (
            "Strict verdict: the strategy survives removing the hardest-fill bucket, but the cleanest "
            "likely-fillable subset is weak or negative. Continue research, do not trade live yet."
        )
    return "Strict verdict: execution stress tests do not support live trading from daily data."


def profit_source_text(hard: pd.Series, fillable_metrics: dict[str, float]) -> str:
    hard_trades = int(hard.get("trades", 0)) if isinstance(hard, pd.Series) else 0
    hard_pnl_share = hard.get("net_pnl_share", np.nan) if isinstance(hard, pd.Series) else np.nan
    return (
        f"Likely hard-fill trades: {hard_trades}, net PnL share {format_pct(hard_pnl_share)}. "
        f"Likely/very-fillable trades combined: {int(fillable_metrics.get('trades', 0))}, "
        f"total net PnL {format_number(fillable_metrics.get('total_net_pnl', np.nan))} TWD."
    )


def aggregate_summary_rows(rows: pd.DataFrame) -> dict[str, float]:
    if rows.empty:
        return {"trades": 0, "total_net_pnl": 0.0}
    return {
        "trades": float(rows["trades"].sum()),
        "total_net_pnl": float(rows["total_net_pnl"].sum()),
    }


def bucket_row(summary: pd.DataFrame, bucket: str) -> pd.Series:
    rows = summary[summary["execution_risk_bucket"].astype("string").eq(bucket)]
    if rows.empty:
        return pd.Series(dtype=float)
    return rows.iloc[0]


def markdown_table(frame: pd.DataFrame, *, max_rows: int = 30) -> str:
    if frame.empty:
        return "_No rows._"
    view = frame.head(max_rows).copy()
    for column in view.columns:
        if pd.api.types.is_float_dtype(view[column]):
            view[column] = view[column].map(format_float)
    view = view.fillna("")
    headers = [str(column) for column in view.columns]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for _, row in view.iterrows():
        values = [escape_markdown_cell(row[column]) for column in view.columns]
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def escape_markdown_cell(value: Any) -> str:
    text = str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def format_float(value: Any) -> str:
    if pd.isna(value):
        return ""
    number = float(value)
    if abs(number) >= 1000:
        return f"{number:,.2f}"
    return f"{number:.4f}"


def format_pct(value: Any) -> str:
    if pd.isna(value):
        return "n/a"
    return f"{float(value) * 100:.2f}%"


def format_number(value: Any) -> str:
    if pd.isna(value):
        return "n/a"
    return f"{float(value):,.0f}"


def select_diagnostic_columns(frame: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "window_id",
        "selected_rank",
        "config_hash",
        "trade_id",
        "event_id",
        "symbol",
        "market",
        "signal_date",
        "entry_date",
        "exit_date",
        "day0_open_px",
        "day0_high_px",
        "day0_low_px",
        "day0_close_px",
        "day0_limit_up_price",
        "day0_range_pct",
        "day0_open_to_close_return",
        "day0_high_equals_close",
        "day0_high_equals_low_equals_close",
        "day0_close_equals_limit_up",
        "day0_turnover_twd",
        "day0_volume_shares",
        "day0_trades",
        "day0_turnover_per_trade",
        "day0_turnover_percentile_by_symbol_252d",
        "volume_ratio_20d",
        "consecutive_limit_up_count",
        "prior_5d_return",
        "prior_20d_return",
        "next_open_gap",
        "net_return",
        "net_pnl",
        "buy_notional",
        "execution_risk_bucket",
        "fill_quality_score",
        "base_fill_reason",
    ]
    for column in columns:
        if column not in frame.columns:
            frame[column] = np.nan
    return frame[columns].sort_values(["signal_date", "market", "symbol", "trade_id"]).reset_index(drop=True)


def bucket_summary_columns() -> list[str]:
    return [
        "execution_risk_bucket",
        "trades",
        "trade_share",
        "average_net_return",
        "median_net_return",
        "win_rate",
        "profit_factor",
        "total_net_pnl",
        "net_pnl_share",
        "avg_return_contribution",
        "max_drawdown",
    ]


def stress_test_columns() -> list[str]:
    return [
        "scenario",
        "kept_trades",
        "removed_trades",
        "kept_trade_share",
        "kept_pnl_share",
        "trades",
        "average_net_return",
        "median_net_return",
        "win_rate",
        "profit_factor",
        "total_net_pnl",
        "max_drawdown",
    ]


def monte_carlo_columns() -> list[str]:
    return [
        "fill_rate",
        "iterations",
        "mean_filled_trades",
        "median_filled_trades",
        "mean_total_net_pnl",
        "median_total_net_pnl",
        "p05_total_net_pnl",
        "p95_total_net_pnl",
        "mean_avg_net_return",
        "median_avg_net_return",
        "p05_avg_net_return",
        "p95_avg_net_return",
    ]


def numeric_or_default(frame: pd.DataFrame, column: str, default: Any) -> pd.Series:
    if column in frame.columns:
        return pd.to_numeric(frame[column], errors="coerce")
    if isinstance(default, pd.Series):
        return pd.to_numeric(default, errors="coerce")
    return pd.Series(default, index=frame.index, dtype="float64")


def coalesce_numeric(frame: pd.DataFrame, columns: list[str]) -> pd.Series:
    result = pd.Series(np.nan, index=frame.index, dtype="float64")
    for column in columns:
        if column not in frame.columns:
            continue
        values = pd.to_numeric(frame[column], errors="coerce")
        result = result.where(result.notna(), values)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnose closed-limit-up overnight execution risk")
    parser.add_argument("--db", type=Path, default=get_settings().db_path, help="DuckDB path")
    parser.add_argument(
        "--oos-trades",
        type=Path,
        default=get_settings().project_root / "reports" / "walk_forward_closed_limit_up_overnight_oos_trades.csv",
    )
    parser.add_argument("--output-dir", type=Path, default=get_settings().project_root / "reports")
    parser.add_argument("--monte-carlo-iterations", type=int, default=1000)
    parser.add_argument("--random-seed", type=int, default=42)
    args = parser.parse_args()

    diagnostic, bucket_summary, stress_tests, monte_carlo, report_path = (
        generate_closed_limit_up_execution_diagnostic(
            db_path=args.db,
            oos_trades_path=args.oos_trades,
            output_dir=args.output_dir,
            monte_carlo_iterations=args.monte_carlo_iterations,
            random_seed=args.random_seed,
        )
    )

    print(f"Wrote {len(diagnostic)} rows to {args.output_dir / DIAGNOSTIC_OUTPUT}")
    print(f"Wrote {len(bucket_summary)} rows to {args.output_dir / BUCKET_SUMMARY_OUTPUT}")
    print(f"Wrote {len(stress_tests)} rows to {args.output_dir / STRESS_TEST_OUTPUT}")
    print(f"Wrote {len(monte_carlo)} rows to {args.output_dir / MONTE_CARLO_OUTPUT}")
    print(f"Wrote report to {report_path}")
    if not bucket_summary.empty:
        print("\nExecution bucket summary")
        print(
            bucket_summary[
                [
                    "execution_risk_bucket",
                    "trades",
                    "trade_share",
                    "average_net_return",
                    "median_net_return",
                    "profit_factor",
                    "total_net_pnl",
                    "net_pnl_share",
                ]
            ].to_string(index=False)
        )


if __name__ == "__main__":
    main()
