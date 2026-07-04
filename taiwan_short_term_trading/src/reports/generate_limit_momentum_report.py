"""Generate a consolidated markdown research report for limit-momentum studies."""

from __future__ import annotations

import argparse
import math
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_MATPLOTLIB_CACHE_DIR = Path(tempfile.gettempdir()) / "taiwan_trading_matplotlib_cache"
_MATPLOTLIB_CACHE_DIR.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("MPLCONFIGDIR", str(_MATPLOTLIB_CACHE_DIR))
os.environ.setdefault("XDG_CACHE_HOME", str(_MATPLOTLIB_CACHE_DIR))

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from config.settings import get_settings
from src.db import get_connection


EVENT_TYPES = [
    "near_limit_8_9",
    "near_limit_9_10",
    "touched_limit_not_closed",
    "closed_limit_up",
    "failed_limit_up",
]

REPORT_CSV_FILES = {
    "event_study_daily": "event_study_daily.csv",
    "event_study_summary": "event_study_summary.csv",
    "feature_performance": "feature_performance_by_bucket.csv",
    "strategy_trades": "strategy_limit_momentum_trades.csv",
    "strategy_summary": "strategy_limit_momentum_summary.csv",
    "intraday_trades": "intraday_opening_trades.csv",
    "intraday_summary": "intraday_opening_summary.csv",
    "grid_results": "grid_search_results.csv",
    "grid_top_train": "grid_search_top_train.csv",
    "grid_top_test": "grid_search_top_test.csv",
    "walk_forward_results": "walk_forward_results.csv",
    "walk_forward_selected": "walk_forward_selected_configs.csv",
    "walk_forward_equity": "walk_forward_oos_equity_curve.csv",
}

PLOT_FILENAMES = {
    "event_count_by_year": "event_count_by_year.png",
    "next_open_return_by_event_type": "next_open_return_by_event_type.png",
    "avg_net_return_by_event_type": "avg_net_return_by_event_type.png",
    "best_oos_equity_curve": "best_oos_equity_curve.png",
    "drawdown_curve": "drawdown_curve.png",
    "tp_sl_heatmap": "tp_sl_heatmap.png",
}


@dataclass(frozen=True)
class ReportInputs:
    reports_dir: Path
    frames: dict[str, pd.DataFrame]
    missing_files: list[str]


def generate_limit_momentum_report(
    *,
    reports_dir: Path | str | None = None,
    db_path: Path | str | None = None,
    output_path: Path | str | None = None,
) -> Path:
    """Read generated CSV outputs and write a consolidated markdown report."""

    settings = get_settings()
    report_dir = Path(reports_dir) if reports_dir is not None else settings.project_root / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    output = Path(output_path) if output_path is not None else report_dir / "limit_momentum_research_report.md"
    db = Path(db_path) if db_path is not None else settings.db_path

    inputs = load_report_inputs(report_dir)
    plot_dir = report_dir / "plots"
    plot_dir.mkdir(parents=True, exist_ok=True)
    plots = create_report_plots(inputs.frames, plot_dir=plot_dir)

    markdown = build_markdown_report(
        inputs=inputs,
        plots=plots,
        db_path=db,
        output_path=output,
    )
    output.write_text(markdown, encoding="utf-8")
    return output


def load_report_inputs(reports_dir: Path) -> ReportInputs:
    frames: dict[str, pd.DataFrame] = {}
    missing: list[str] = []
    for key, filename in REPORT_CSV_FILES.items():
        path = reports_dir / filename
        if not path.exists():
            frames[key] = pd.DataFrame()
            missing.append(filename)
            continue
        try:
            frames[key] = pd.read_csv(path)
        except pd.errors.EmptyDataError:
            frames[key] = pd.DataFrame()
    return ReportInputs(reports_dir=reports_dir, frames=frames, missing_files=missing)


def create_report_plots(frames: dict[str, pd.DataFrame], *, plot_dir: Path) -> dict[str, Path]:
    paths = {name: plot_dir / filename for name, filename in PLOT_FILENAMES.items()}
    plot_event_count_by_year(frames["event_study_daily"], paths["event_count_by_year"])
    plot_next_open_return_by_event_type(frames["event_study_daily"], paths["next_open_return_by_event_type"])
    plot_avg_net_return_by_event_type(
        frames["strategy_trades"],
        frames["strategy_summary"],
        frames["intraday_trades"],
        paths["avg_net_return_by_event_type"],
    )
    plot_equity_curve(frames["walk_forward_equity"], frames["strategy_trades"], paths["best_oos_equity_curve"])
    plot_drawdown_curve(frames["walk_forward_equity"], frames["strategy_trades"], paths["drawdown_curve"])
    plot_tp_sl_heatmap(frames["grid_results"], frames["strategy_trades"], paths["tp_sl_heatmap"])
    return paths


def build_markdown_report(
    *,
    inputs: ReportInputs,
    plots: dict[str, Path],
    db_path: Path,
    output_path: Path,
) -> str:
    frames = inputs.frames
    event_daily = frames["event_study_daily"]
    event_summary = frames["event_study_summary"]
    strategy_summary = frames["strategy_summary"]
    strategy_trades = frames["strategy_trades"]
    intraday_summary = frames["intraday_summary"]
    grid_results = frames["grid_results"]
    grid_top_train = frames["grid_top_train"]
    grid_top_test = frames["grid_top_test"]
    walk_forward_results = frames["walk_forward_results"]
    walk_forward_selected = frames["walk_forward_selected"]
    walk_forward_equity = frames["walk_forward_equity"]
    feature_performance = frames["feature_performance"]

    near_8_9 = event_type_row(event_summary, event_daily, "near_limit_8_9")
    answer = answer_key_question(near_8_9)
    robust_configs = robust_config_table(grid_top_test, walk_forward_selected)

    lines: list[str] = [
        "# Taiwan Limit-Momentum Research Report",
        "",
        f"Generated from `{inputs.reports_dir}`.",
        "",
        "## 1. Executive summary",
        "",
        executive_summary_text(frames, near_8_9, answer),
        "",
        "## 2. Data coverage",
        "",
        data_coverage_section(frames, db_path),
        "",
        "Generated output inventory:",
        "",
        markdown_table(output_inventory(inputs)),
        "",
        "## 3. Taiwan market assumptions and cost assumptions",
        "",
        market_assumptions_section(),
        "",
        "## 4. Event frequency by type",
        "",
        markdown_table(event_frequency_table(event_daily, event_summary)),
        "",
        image_markdown("Event count by year", plots["event_count_by_year"], output_path),
        "",
        "## 5. Event study",
        "",
        markdown_table(event_study_table(event_summary, event_daily)),
        "",
        image_markdown("Next-open return by event type", plots["next_open_return_by_event_type"], output_path),
        "",
        "Institutional and margin feature bucket performance:",
        "",
        markdown_table(select_columns(feature_performance, [
            "feature_name",
            "feature_bucket",
            "event_type",
            "event_count",
            "mean_next_open_return",
            "mean_next_close_return",
            "mean_approx_net_open_to_close_return",
        ], top_n=20)),
        "",
        "## 6. Do +8-9% non-limit stocks continue upward after next open?",
        "",
        answer,
        "",
        markdown_table(pd.DataFrame([near_8_9]) if near_8_9 else pd.DataFrame()),
        "",
        "## 7. Strategy backtest results",
        "",
        "Daily open-to-close approximation:",
        "",
        markdown_table(daily_open_to_close_table(strategy_summary, strategy_trades)),
        "",
        "Stop/take-profit daily OHLC approximation:",
        "",
        markdown_table(stop_take_profit_table(strategy_summary)),
        "",
        "Intraday opening continuation:",
        "",
        markdown_table(intraday_table(intraday_summary)),
        "",
        image_markdown("Average net return by event type", plots["avg_net_return_by_event_type"], output_path),
        "",
        "## 8. Grid search results",
        "",
        "Top train-ranked configurations:",
        "",
        markdown_table(grid_table(grid_top_train if not grid_top_train.empty else grid_results, top_n=10)),
        "",
        "Top test-ranked configurations:",
        "",
        markdown_table(grid_table(grid_top_test if not grid_top_test.empty else grid_results, top_n=10)),
        "",
        image_markdown("TP/SL heatmap", plots["tp_sl_heatmap"], output_path),
        "",
        "## 9. Walk-forward results",
        "",
        markdown_table(walk_forward_table(walk_forward_results, walk_forward_selected)),
        "",
        image_markdown("Best OOS equity curve", plots["best_oos_equity_curve"], output_path),
        "",
        image_markdown("Drawdown curve", plots["drawdown_curve"], output_path),
        "",
        "## 10. Best-performing robust configs",
        "",
        markdown_table(robust_configs),
        "",
        "## 11. Failure modes",
        "",
        failure_modes_section(event_daily, strategy_trades, grid_results, walk_forward_results),
        "",
        "## 12. Liquidity and capacity analysis",
        "",
        liquidity_capacity_section(event_daily, strategy_trades, grid_results),
        "",
        "## 13. Recommended next experiments",
        "",
        recommended_next_experiments(),
        "",
    ]
    return "\n".join(lines)


def executive_summary_text(frames: dict[str, pd.DataFrame], near_8_9: dict[str, Any], answer: str) -> str:
    daily_events = len(frames["event_study_daily"]) if not frames["event_study_daily"].empty else 0
    daily_trades = len(frames["strategy_trades"]) if not frames["strategy_trades"].empty else 0
    intraday_trades = len(frames["intraday_trades"]) if not frames["intraday_trades"].empty else 0
    oos_rows = len(frames["walk_forward_results"]) if not frames["walk_forward_results"].empty else 0
    near_count = near_8_9.get("event_count") if near_8_9 else None
    near_net = near_8_9.get("mean_approx_net_next_open_return") if near_8_9 else None

    bullets = [
        f"- Event study rows available: {daily_events:,}.",
        f"- Daily strategy trades available: {daily_trades:,}; intraday trades available: {intraday_trades:,}.",
        f"- Walk-forward selected test rows available: {oos_rows:,}.",
    ]
    if near_count is not None:
        bullets.append(
            "- `near_limit_8_9` observations: "
            f"{format_integer(near_count)} with average approx-net next-open return {format_percent(near_net)}."
        )
    bullets.append("- Key-question read: " + first_sentence(answer))
    return "\n".join(bullets)


def data_coverage_section(frames: dict[str, pd.DataFrame], db_path: Path) -> str:
    parts: list[str] = []
    db_coverage = load_db_coverage(db_path)
    if not db_coverage.empty:
        parts.extend(["DuckDB `daily_prices` coverage:", "", markdown_table(db_coverage), ""])

    event_daily = frames["event_study_daily"]
    if not event_daily.empty:
        coverage = {
            "event_rows": len(event_daily),
            "symbols": event_daily["symbol"].nunique() if "symbol" in event_daily.columns else np.nan,
            "markets": ", ".join(sorted(event_daily["market"].dropna().astype(str).unique()))
            if "market" in event_daily.columns
            else "n/a",
            "start": min_date(event_daily, "trade_date"),
            "end": max_date(event_daily, "trade_date"),
            "day1_observations": int(pd.to_numeric(event_daily.get("next_close_return"), errors="coerce").notna().sum()),
        }
        parts.extend(["Event-study CSV coverage:", "", markdown_table(pd.DataFrame([coverage])), ""])
    if not parts:
        return "No coverage data is available yet. Run the daily collectors, event study, and strategy backtests first."
    return "\n".join(parts).strip()


def load_db_coverage(db_path: Path) -> pd.DataFrame:
    if not db_path.exists():
        return pd.DataFrame()
    try:
        with get_connection(db_path, read_only=True) as conn:
            return conn.execute(
                """
                SELECT
                    market,
                    COUNT(*) AS rows,
                    COUNT(DISTINCT symbol) AS symbols,
                    MIN(trade_date) AS start_date,
                    MAX(trade_date) AS end_date,
                    SUM(turnover_twd) AS total_turnover_twd
                FROM daily_prices
                GROUP BY market
                ORDER BY market
                """
            ).fetch_df()
    except Exception:
        return pd.DataFrame()


def market_assumptions_section() -> str:
    return "\n".join(
        [
            "- Taiwan equity price limits are treated as the standard +/-10% rule with the existing project tick-size approximation.",
            "- Daily strategy entries buy at Day 1 open and exit at Day 1 close, take-profit, or stop-loss depending on the run.",
            "- Intraday strategy entries use Day 1 opening-window confirmation and 1-minute bars when imported.",
            "- Default commission is `0.1425%` per side with configurable broker discount; the default discount multiplier is `0.28`.",
            "- Qualified day-trade sell tax defaults to `0.15%`; normal sell tax can be modeled separately at `0.30%`.",
            "- Default slippage is `5` bps per side, plus configurable minimum commission.",
            "- Position sizing is fixed notional rounded down to 1,000-share board lots unless later strategy modules override it.",
        ]
    )


def event_frequency_table(event_daily: pd.DataFrame, event_summary: pd.DataFrame) -> pd.DataFrame:
    if not event_daily.empty and "event_type" in event_daily.columns:
        frame = event_daily.copy()
        frame["year"] = pd.to_datetime(frame.get("trade_date"), errors="coerce").dt.year
        grouped = (
            frame.groupby("event_type", dropna=False)
            .agg(
                event_count=("event_type", "size"),
                symbols=("symbol", "nunique") if "symbol" in frame.columns else ("event_type", "size"),
                first_date=("trade_date", "min") if "trade_date" in frame.columns else ("event_type", "first"),
                last_date=("trade_date", "max") if "trade_date" in frame.columns else ("event_type", "first"),
            )
            .reset_index()
        )
        return order_event_types(grouped)
    return select_summary_rows(event_summary, "by_event_type", [
        "event_type",
        "event_count",
        "day1_observation_count",
        "avg_day0_return",
        "median_day0_turnover_twd",
    ])


def event_study_table(event_summary: pd.DataFrame, event_daily: pd.DataFrame) -> pd.DataFrame:
    summary = select_summary_rows(
        event_summary,
        "by_event_type",
        [
            "event_type",
            "event_count",
            "day1_observation_count",
            "avg_day0_return",
            "mean_next_open_return",
            "p_value_next_open_return_gt_0",
            "mean_next_close_return",
            "p_value_next_close_return_gt_0",
            "mean_open_to_close_return",
            "mean_approx_net_next_open_return",
            "mean_approx_net_next_close_return",
            "win_next_open_rate",
            "win_next_close_rate",
            "hit_plus_2_intraday_rate",
        ],
    )
    if not summary.empty:
        return order_event_types(summary)
    if event_daily.empty:
        return pd.DataFrame()
    rows = []
    for event_type, group in event_daily.groupby("event_type", dropna=False):
        rows.append(
            {
                "event_type": event_type,
                "event_count": len(group),
                "mean_next_open_return": _mean(group, "next_open_return"),
                "mean_next_close_return": _mean(group, "next_close_return"),
                "mean_open_to_close_return": _mean(group, "open_to_close_return"),
                "mean_approx_net_next_open_return": _mean(group, "approx_net_next_open_return"),
                "mean_approx_net_next_close_return": _mean(group, "approx_net_next_close_return"),
                "win_next_open_rate": _mean(group, "win_next_open"),
                "win_next_close_rate": _mean(group, "win_next_close"),
            }
        )
    return order_event_types(pd.DataFrame(rows))


def event_type_row(event_summary: pd.DataFrame, event_daily: pd.DataFrame, event_type: str) -> dict[str, Any]:
    table = event_study_table(event_summary, event_daily)
    if table.empty or "event_type" not in table.columns:
        return {}
    row = table[table["event_type"] == event_type]
    return row.iloc[0].to_dict() if not row.empty else {}


def answer_key_question(row: dict[str, Any]) -> str:
    if not row:
        return (
            "The current generated outputs do not contain enough `near_limit_8_9` event-study data to answer this "
            "question. Run `run-daily-study` after collecting daily prices."
        )
    event_count = numeric_value(row.get("event_count"))
    next_open = numeric_value(row.get("mean_next_open_return"))
    next_close = numeric_value(row.get("mean_next_close_return"))
    net_next_open = numeric_value(row.get("mean_approx_net_next_open_return"))
    net_next_close = numeric_value(row.get("mean_approx_net_next_close_return"))
    p_open = numeric_value(row.get("p_value_next_open_return_gt_0"))

    if math.isnan(next_open) or math.isnan(next_close):
        return "`near_limit_8_9` exists in the outputs, but next-day return observations are missing."

    gross_positive = next_open > 0 and next_close > 0
    net_positive = not math.isnan(net_next_open) and not math.isnan(net_next_close) and net_next_open > 0 and net_next_close > 0
    statistical = not math.isnan(p_open) and p_open <= 0.05

    if gross_positive and net_positive and statistical:
        verdict = "Yes, the current sample shows positive next-open and next-close continuation after approximate costs."
    elif gross_positive and net_positive:
        verdict = "Tentatively yes, but statistical strength is not yet conclusive in the current outputs."
    elif gross_positive:
        verdict = "Gross continuation is positive, but the approximate after-cost result does not yet clear the bar."
    else:
        verdict = "No, the current outputs do not show positive continuation for this cohort."

    return (
        f"{verdict} The `near_limit_8_9` cohort has {format_integer(event_count)} events, "
        f"mean next-open return {format_percent(next_open)}, mean next-close return {format_percent(next_close)}, "
        f"approx-net next-open return {format_percent(net_next_open)}, and approx-net next-close return "
        f"{format_percent(net_next_close)}. The one-sided next-open p-value is {format_number(p_open)}."
    )


def daily_open_to_close_table(summary: pd.DataFrame, trades: pd.DataFrame) -> pd.DataFrame:
    if not summary.empty:
        close_only = summary[
            (summary.get("summary_level") == "overall")
            & (summary.get("path_assumption").astype("string").fillna("") == "close_only")
        ]
        if not close_only.empty:
            return select_columns(close_only, [
                "path_assumption",
                "number_of_trades",
                "win_rate",
                "average_gross_return",
                "average_net_return",
                "median_net_return",
                "total_net_pnl",
                "profit_factor",
                "max_drawdown",
            ])
    if trades.empty:
        return pd.DataFrame()
    return summarize_trades(trades, label="daily_trades")


def stop_take_profit_table(summary: pd.DataFrame) -> pd.DataFrame:
    if summary.empty:
        return pd.DataFrame()
    overall = summary[summary.get("summary_level") == "overall"]
    return select_columns(overall, [
        "path_assumption",
        "number_of_trades",
        "win_rate",
        "average_net_return",
        "median_net_return",
        "total_net_pnl",
        "profit_factor",
        "max_drawdown",
        "average_turnover",
    ])


def intraday_table(summary: pd.DataFrame) -> pd.DataFrame:
    if summary.empty:
        return pd.DataFrame()
    overall = summary[summary.get("summary_level") == "overall"]
    return select_columns(overall, [
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
    ])


def grid_table(frame: pd.DataFrame, *, top_n: int) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()
    sort_columns = [column for column in ["test_net_pnl", "train_rank_score", "test_avg_net_return"] if column in frame.columns]
    if sort_columns:
        frame = frame.sort_values(sort_columns, ascending=False, na_position="last")
    return select_columns(frame, [
        "event_types",
        "market",
        "path_assumption",
        "min_turnover_twd",
        "min_volume_ratio_20d",
        "min_close_location",
        "take_profit_pct",
        "stop_loss_pct",
        "require_investment_trust_buying",
        "avoid_margin_overcrowded",
        "train_trades",
        "train_avg_net_return",
        "train_max_drawdown",
        "test_trades",
        "test_avg_net_return",
        "test_net_pnl",
        "test_max_drawdown",
        "test_capacity_proxy_twd",
    ], top_n=top_n)


def walk_forward_table(results: pd.DataFrame, selected: pd.DataFrame) -> pd.DataFrame:
    source = selected if not selected.empty else results
    if source.empty:
        return pd.DataFrame()
    return select_columns(source, [
        "window_id",
        "selected_rank",
        "event_types",
        "market",
        "path_assumption",
        "score",
        "train_trades",
        "train_avg_net_return",
        "oos_trades",
        "oos_avg_net_return",
        "oos_net_pnl",
        "oos_max_drawdown",
        "oos_capacity_proxy_twd",
    ], top_n=20)


def robust_config_table(grid_top_test: pd.DataFrame, walk_forward_selected: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    if not walk_forward_selected.empty:
        grouped = (
            walk_forward_selected.groupby(["event_types", "market", "path_assumption"], dropna=False)
            .agg(
                selection_count=("window_id", "count") if "window_id" in walk_forward_selected.columns else ("event_types", "size"),
                mean_oos_return=("oos_avg_net_return", "mean") if "oos_avg_net_return" in walk_forward_selected.columns else ("event_types", "size"),
                total_oos_pnl=("oos_net_pnl", "sum") if "oos_net_pnl" in walk_forward_selected.columns else ("event_types", "size"),
                worst_oos_drawdown=("oos_max_drawdown", "min") if "oos_max_drawdown" in walk_forward_selected.columns else ("event_types", "size"),
            )
            .reset_index()
        )
        grouped["source"] = "walk_forward"
        rows.extend(grouped.to_dict("records"))

    if not grid_top_test.empty:
        top = grid_top_test.head(10).copy()
        top["source"] = "grid_top_test"
        rows.extend(
            select_columns(top, [
                "source",
                "event_types",
                "market",
                "path_assumption",
                "test_trades",
                "test_avg_net_return",
                "test_net_pnl",
                "test_max_drawdown",
            ]).to_dict("records")
        )
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).head(20)


def failure_modes_section(
    event_daily: pd.DataFrame,
    strategy_trades: pd.DataFrame,
    grid_results: pd.DataFrame,
    walk_forward_results: pd.DataFrame,
) -> str:
    bullets: list[str] = []
    if not event_daily.empty:
        low_day1 = int(pd.to_numeric(event_daily.get("next_close_return"), errors="coerce").isna().sum())
        if low_day1:
            bullets.append(f"- {low_day1:,} event rows are missing Day 1 close observations.")
        losers = (
            event_daily.assign(next_close_return=pd.to_numeric(event_daily["next_close_return"], errors="coerce"))
            .groupby("event_type")["next_close_return"]
            .mean()
            .sort_values()
            .head(3)
            if "next_close_return" in event_daily.columns
            else pd.Series(dtype=float)
        )
        if not losers.empty:
            bullets.append("- Weakest event types by next-close return: " + ", ".join(
                f"{idx} ({format_percent(value)})" for idx, value in losers.items()
            ))
    if not strategy_trades.empty and "exit_reason" in strategy_trades.columns:
        exit_counts = strategy_trades["exit_reason"].value_counts().head(5)
        bullets.append("- Most common daily-strategy exits: " + ", ".join(
            f"{reason}: {count}" for reason, count in exit_counts.items()
        ))
    if not grid_results.empty and "test_trades" in grid_results.columns:
        low_sample = int((pd.to_numeric(grid_results["test_trades"], errors="coerce").fillna(0) < 20).sum())
        bullets.append(f"- Grid-search configs with fewer than 20 test trades: {low_sample:,}.")
    if not walk_forward_results.empty and "oos_net_pnl" in walk_forward_results.columns:
        bad_oos = int((pd.to_numeric(walk_forward_results["oos_net_pnl"], errors="coerce").fillna(0) < 0).sum())
        bullets.append(f"- Walk-forward selected/config rows with negative OOS PnL: {bad_oos:,}.")
    bullets.extend(
        [
            "- Daily OHLC stop/take-profit tests do not know the true intraday path; pessimistic assumptions should carry more weight.",
            "- Public-data coverage and corporate-action handling should be audited before interpreting small edges.",
            "- Strategy variants with tiny sample counts are likely overfit even when net returns look attractive.",
        ]
    )
    return "\n".join(bullets)


def liquidity_capacity_section(event_daily: pd.DataFrame, strategy_trades: pd.DataFrame, grid_results: pd.DataFrame) -> str:
    rows: list[dict[str, Any]] = []
    if not event_daily.empty:
        rows.append(
            {
                "source": "event_study",
                "rows": len(event_daily),
                "median_turnover_twd": _median(event_daily, "day0_turnover_twd"),
                "avg_turnover_twd": _mean(event_daily, "day0_turnover_twd"),
                "median_capacity_5pct_twd": _median(event_daily, "day0_turnover_twd") * 0.05,
            }
        )
    if not strategy_trades.empty:
        rows.append(
            {
                "source": "strategy_trades",
                "rows": len(strategy_trades),
                "median_turnover_twd": _median(strategy_trades, "day0_turnover_twd"),
                "avg_turnover_twd": _mean(strategy_trades, "day0_turnover_twd"),
                "median_capacity_5pct_twd": _median(strategy_trades, "day0_turnover_twd") * 0.05,
            }
        )
    if not grid_results.empty and "test_capacity_proxy_twd" in grid_results.columns:
        rows.append(
            {
                "source": "grid_test_capacity",
                "rows": len(grid_results),
                "median_turnover_twd": np.nan,
                "avg_turnover_twd": _mean(grid_results, "test_avg_liquidity_twd"),
                "median_capacity_5pct_twd": _median(grid_results, "test_capacity_proxy_twd"),
            }
        )
    if not rows:
        return "No liquidity/capacity CSV outputs are available yet."
    return markdown_table(pd.DataFrame(rows))


def recommended_next_experiments() -> str:
    return "\n".join(
        [
            "- Re-run the event study by market, liquidity bucket, and sector to isolate where continuation survives costs.",
            "- Replace daily OHLC stop/take-profit assumptions with imported 1-minute bars for the same windows.",
            "- Add corporate-action and attention filters, including suspension, disposition stocks, and abnormal turnover flags.",
            "- Test opening auction imbalance, first-3-minute VWAP slope, and first pullback behavior for limit-up continuations.",
            "- Stress-test commission discounts, sale-tax assumptions, slippage, and participation-rate capacity limits.",
            "- Reserve a final untouched validation window after choosing configs from walk-forward analysis.",
        ]
    )


def plot_event_count_by_year(event_daily: pd.DataFrame, path: Path) -> None:
    if event_daily.empty or "event_type" not in event_daily.columns:
        placeholder_plot(path, "Event count by year", "No event-study daily data")
        return
    frame = event_daily.copy()
    frame["year"] = pd.to_datetime(frame.get("trade_date"), errors="coerce").dt.year
    pivot = frame.pivot_table(index="year", columns="event_type", values="event_id", aggfunc="count", fill_value=0)
    if pivot.empty:
        placeholder_plot(path, "Event count by year", "No valid event dates")
        return
    ax = pivot.plot(kind="bar", stacked=True, figsize=(9, 5), width=0.8)
    ax.set_title("Event Count by Year")
    ax.set_xlabel("Year")
    ax.set_ylabel("Events")
    ax.legend(title="Event type", fontsize=8)
    save_current_figure(path)


def plot_next_open_return_by_event_type(event_daily: pd.DataFrame, path: Path) -> None:
    if event_daily.empty or "next_open_return" not in event_daily.columns:
        placeholder_plot(path, "Next-open return by event type", "No event-study daily data")
        return
    grouped = (
        event_daily.assign(next_open_return=pd.to_numeric(event_daily["next_open_return"], errors="coerce"))
        .groupby("event_type")["next_open_return"]
        .mean()
        .reindex(EVENT_TYPES)
        .dropna()
    )
    if grouped.empty:
        placeholder_plot(path, "Next-open return by event type", "No next-open return observations")
        return
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.bar(grouped.index, grouped.values * 100.0, color="#3465a4")
    ax.axhline(0, color="black", linewidth=0.8)
    ax.set_title("Average Next-Open Return by Event Type")
    ax.set_xlabel("Event type")
    ax.set_ylabel("Return (%)")
    ax.tick_params(axis="x", rotation=25)
    save_current_figure(path)


def plot_avg_net_return_by_event_type(
    strategy_trades: pd.DataFrame,
    strategy_summary: pd.DataFrame,
    intraday_trades: pd.DataFrame,
    path: Path,
) -> None:
    source = strategy_trades if not strategy_trades.empty else intraday_trades
    if not source.empty and {"event_type", "net_return"}.issubset(source.columns):
        grouped = (
            source.assign(net_return=pd.to_numeric(source["net_return"], errors="coerce"))
            .groupby("event_type")["net_return"]
            .mean()
            .reindex(EVENT_TYPES)
            .dropna()
        )
    elif not strategy_summary.empty:
        rows = strategy_summary[strategy_summary.get("summary_level") == "by_event_type"].copy()
        if "path_assumption" in rows.columns:
            rows = rows[rows["path_assumption"].astype("string").fillna("") == "pessimistic"]
        grouped = rows.set_index("event_type")["average_net_return"].reindex(EVENT_TYPES).dropna() if not rows.empty else pd.Series(dtype=float)
    else:
        grouped = pd.Series(dtype=float)
    if grouped.empty:
        placeholder_plot(path, "Average net return by event type", "No strategy return data")
        return
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.bar(grouped.index, grouped.values * 100.0, color="#4e9a06")
    ax.axhline(0, color="black", linewidth=0.8)
    ax.set_title("Average Net Return by Event Type")
    ax.set_xlabel("Event type")
    ax.set_ylabel("Net return (%)")
    ax.tick_params(axis="x", rotation=25)
    save_current_figure(path)


def plot_equity_curve(walk_forward_equity: pd.DataFrame, strategy_trades: pd.DataFrame, path: Path) -> None:
    curve = equity_curve_source(walk_forward_equity, strategy_trades)
    if curve.empty:
        placeholder_plot(path, "Best OOS equity curve", "No equity data")
        return
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.plot(curve["trade_date"], curve["equity"], color="#204a87", linewidth=1.8)
    ax.set_title("Equity Curve")
    ax.set_xlabel("Trade date")
    ax.set_ylabel("Cumulative net PnL")
    ax.tick_params(axis="x", rotation=25)
    save_current_figure(path)


def plot_drawdown_curve(walk_forward_equity: pd.DataFrame, strategy_trades: pd.DataFrame, path: Path) -> None:
    curve = equity_curve_source(walk_forward_equity, strategy_trades)
    if curve.empty:
        placeholder_plot(path, "Drawdown curve", "No equity data")
        return
    drawdown = curve["equity"] - curve["equity"].cummax()
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.fill_between(curve["trade_date"], drawdown, 0, color="#a40000", alpha=0.35)
    ax.plot(curve["trade_date"], drawdown, color="#a40000", linewidth=1.2)
    ax.set_title("Drawdown Curve")
    ax.set_xlabel("Trade date")
    ax.set_ylabel("Drawdown")
    ax.tick_params(axis="x", rotation=25)
    save_current_figure(path)


def plot_tp_sl_heatmap(grid_results: pd.DataFrame, strategy_trades: pd.DataFrame, path: Path) -> None:
    source = grid_results.copy()
    metric = "test_avg_net_return"
    if source.empty and not strategy_trades.empty:
        source = strategy_trades.copy()
        metric = "net_return"
    needed = {"take_profit_pct", "stop_loss_pct", metric}
    if source.empty or not needed.issubset(source.columns):
        placeholder_plot(path, "TP/SL heatmap", "No TP/SL grid data")
        return
    source["take_profit_pct"] = pd.to_numeric(source["take_profit_pct"], errors="coerce")
    source["stop_loss_pct"] = pd.to_numeric(source["stop_loss_pct"], errors="coerce")
    source[metric] = pd.to_numeric(source[metric], errors="coerce")
    pivot = source.pivot_table(index="stop_loss_pct", columns="take_profit_pct", values=metric, aggfunc="mean")
    if pivot.empty:
        placeholder_plot(path, "TP/SL heatmap", "No TP/SL observations")
        return
    fig, ax = plt.subplots(figsize=(8, 5))
    image = ax.imshow(pivot.values * 100.0, cmap="RdYlGn", aspect="auto")
    ax.set_title("Average Net Return by TP/SL")
    ax.set_xlabel("Take profit")
    ax.set_ylabel("Stop loss")
    ax.set_xticks(range(len(pivot.columns)))
    ax.set_xticklabels([format_percent(value) for value in pivot.columns])
    ax.set_yticks(range(len(pivot.index)))
    ax.set_yticklabels([format_percent(value) for value in pivot.index])
    for y in range(len(pivot.index)):
        for x in range(len(pivot.columns)):
            value = pivot.iloc[y, x]
            if pd.notna(value):
                ax.text(x, y, format_percent(value), ha="center", va="center", fontsize=8)
    fig.colorbar(image, ax=ax, label="Net return (%)")
    save_current_figure(path)


def equity_curve_source(walk_forward_equity: pd.DataFrame, strategy_trades: pd.DataFrame) -> pd.DataFrame:
    if not walk_forward_equity.empty and {"trade_date", "overall_cumulative_net_pnl"}.issubset(walk_forward_equity.columns):
        frame = walk_forward_equity.copy()
        frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce")
        frame["equity"] = pd.to_numeric(frame["overall_cumulative_net_pnl"], errors="coerce")
        return frame.dropna(subset=["trade_date", "equity"]).sort_values("trade_date")
    if not strategy_trades.empty and {"entry_date", "net_pnl"}.issubset(strategy_trades.columns):
        frame = strategy_trades.copy()
        frame["entry_date"] = pd.to_datetime(frame["entry_date"], errors="coerce")
        daily = frame.groupby("entry_date")["net_pnl"].sum(numeric_only=True).reset_index()
        daily["equity"] = daily["net_pnl"].cumsum()
        return daily.rename(columns={"entry_date": "trade_date"}).dropna(subset=["trade_date", "equity"])
    return pd.DataFrame()


def placeholder_plot(path: Path, title: str, message: str) -> None:
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.set_title(title)
    ax.text(0.5, 0.5, message, ha="center", va="center", fontsize=12, transform=ax.transAxes)
    ax.set_xticks([])
    ax.set_yticks([])
    save_current_figure(path)


def save_current_figure(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    plt.tight_layout()
    plt.savefig(path, dpi=140, bbox_inches="tight")
    plt.close()


def output_inventory(inputs: ReportInputs) -> pd.DataFrame:
    rows = []
    for key, filename in REPORT_CSV_FILES.items():
        frame = inputs.frames[key]
        rows.append(
            {
                "output": filename,
                "status": "missing" if filename in inputs.missing_files else "available",
                "rows": len(frame),
                "columns": len(frame.columns),
            }
        )
    return pd.DataFrame(rows)


def select_summary_rows(summary: pd.DataFrame, summary_level: str, columns: list[str]) -> pd.DataFrame:
    if summary.empty or "summary_level" not in summary.columns:
        return pd.DataFrame()
    rows = summary[summary["summary_level"] == summary_level]
    return select_columns(rows, columns)


def select_columns(frame: pd.DataFrame, columns: list[str], *, top_n: int | None = None) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=[column for column in columns if column in frame.columns])
    selected = frame[[column for column in columns if column in frame.columns]].copy()
    if top_n is not None:
        selected = selected.head(top_n)
    return selected


def summarize_trades(trades: pd.DataFrame, *, label: str) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    net_pnl = pd.to_numeric(trades.get("net_pnl"), errors="coerce").fillna(0.0)
    net_return = pd.to_numeric(trades.get("net_return"), errors="coerce")
    return pd.DataFrame(
        [
            {
                "source": label,
                "number_of_trades": len(trades),
                "win_rate": float((net_pnl > 0).mean()) if len(trades) else np.nan,
                "average_net_return": float(net_return.mean()) if not net_return.dropna().empty else np.nan,
                "median_net_return": float(net_return.median()) if not net_return.dropna().empty else np.nan,
                "total_net_pnl": float(net_pnl.sum()),
                "max_drawdown": float((net_pnl.cumsum() - net_pnl.cumsum().cummax()).min()) if len(net_pnl) else np.nan,
            }
        ]
    )


def order_event_types(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty or "event_type" not in frame.columns:
        return frame
    output = frame.copy()
    output["_event_order"] = output["event_type"].map({event_type: index for index, event_type in enumerate(EVENT_TYPES)})
    output = output.sort_values(["_event_order", "event_type"], na_position="last").drop(columns=["_event_order"])
    return output.reset_index(drop=True)


def markdown_table(frame: pd.DataFrame, *, max_rows: int = 25) -> str:
    if frame is None or frame.empty:
        return "_No data available._"
    display = frame.head(max_rows).copy()
    display = display.rename(columns={column: str(column) for column in display.columns})
    headers = list(display.columns)
    rows = [[format_cell(value) for value in row] for row in display.itertuples(index=False, name=None)]
    widths = [
        max(len(str(header)), *(len(row[index]) for row in rows)) if rows else len(str(header))
        for index, header in enumerate(headers)
    ]
    header_line = "| " + " | ".join(str(header).ljust(widths[index]) for index, header in enumerate(headers)) + " |"
    separator = "| " + " | ".join("-" * widths[index] for index in range(len(headers))) + " |"
    body = ["| " + " | ".join(row[index].ljust(widths[index]) for index in range(len(headers))) + " |" for row in rows]
    if len(frame) > max_rows:
        body.append(f"| {'...'.ljust(widths[0])} | " + " | ".join("".ljust(width) for width in widths[1:]) + " |")
    return "\n".join([header_line, separator, *body])


def image_markdown(alt: str, path: Path, output_path: Path) -> str:
    try:
        rel = path.relative_to(output_path.parent)
    except ValueError:
        rel = path
    return f"![{alt}]({rel.as_posix()})"


def format_cell(value: Any) -> str:
    if pd.isna(value):
        return ""
    if isinstance(value, (pd.Timestamp,)):
        return value.date().isoformat()
    if isinstance(value, str):
        return value
    if isinstance(value, (bool, np.bool_)):
        return "true" if bool(value) else "false"
    if isinstance(value, (int, np.integer)):
        return f"{int(value):,}"
    if isinstance(value, (float, np.floating)):
        numeric = float(value)
        if math.isnan(numeric) or math.isinf(numeric):
            return str(numeric)
        if abs(numeric) < 1 and any(token in str(value) for token in ["."]):
            return f"{numeric:.4f}"
        if abs(numeric) >= 1000:
            return f"{numeric:,.0f}"
        return f"{numeric:.4f}"
    return str(value)


def format_percent(value: Any) -> str:
    numeric = numeric_value(value)
    if math.isnan(numeric):
        return "n/a"
    return f"{numeric * 100:.2f}%"


def format_number(value: Any) -> str:
    numeric = numeric_value(value)
    if math.isnan(numeric):
        return "n/a"
    return f"{numeric:.4f}"


def format_integer(value: Any) -> str:
    numeric = numeric_value(value)
    if math.isnan(numeric):
        return "n/a"
    return f"{int(round(numeric)):,}"


def numeric_value(value: Any) -> float:
    try:
        numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    except Exception:
        return float("nan")
    return float(numeric) if pd.notna(numeric) else float("nan")


def first_sentence(text: str) -> str:
    return text.split(". ")[0].strip() + ("." if ". " in text else "")


def min_date(frame: pd.DataFrame, column: str) -> str:
    if column not in frame.columns:
        return "n/a"
    values = pd.to_datetime(frame[column], errors="coerce").dropna()
    return values.min().date().isoformat() if not values.empty else "n/a"


def max_date(frame: pd.DataFrame, column: str) -> str:
    if column not in frame.columns:
        return "n/a"
    values = pd.to_datetime(frame[column], errors="coerce").dropna()
    return values.max().date().isoformat() if not values.empty else "n/a"


def _mean(frame: pd.DataFrame, column: str) -> float:
    if column not in frame.columns:
        return np.nan
    values = pd.to_numeric(frame[column], errors="coerce").dropna()
    return float(values.mean()) if not values.empty else np.nan


def _median(frame: pd.DataFrame, column: str) -> float:
    if column not in frame.columns:
        return np.nan
    values = pd.to_numeric(frame[column], errors="coerce").dropna()
    return float(values.median()) if not values.empty else np.nan


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the consolidated limit-momentum research report")
    parser.add_argument("--reports-dir", type=Path, default=get_settings().project_root / "reports")
    parser.add_argument("--db", type=Path, default=get_settings().db_path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    output = generate_limit_momentum_report(
        reports_dir=args.reports_dir,
        db_path=args.db,
        output_path=args.output,
    )
    print(f"Wrote research report to {output}")


if __name__ == "__main__":
    main()
