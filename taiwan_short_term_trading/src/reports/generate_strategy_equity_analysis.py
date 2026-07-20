"""Generate historical equity, PnL, and drawdown analysis for CLU profiles."""

from __future__ import annotations

import argparse
import os
import tempfile
from dataclasses import fields
from pathlib import Path
from typing import Any

os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "taiwan_trading_matplotlib"))

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from config.settings import get_settings
from src.backtests.expanded_clu_tournament import (
    ExpandedCLUConfig,
    broad_champion_config,
    build_expanded_universe,
    evaluate_expanded_config,
    expanded_config_hash,
    identify_train_weak_industries,
)
from src.backtests.focused_challenger_expansion import FocusedGridConfig, evaluate_focused_strategy
from src.backtests.portfolio_sim_closed_limit_up import markdown_table
from src.backtests.strategy_tournament import champion_config, evaluate_strategy, infer_tournament_dates, load_tournament_universe


INITIAL_CAPITAL_TWD = 1_000_000.0
STRATEGY_ANALYSIS_DIR_NAME = "strategy_analysis"

EQUITY_DAILY_OUTPUT = "strategy_equity_daily.csv"
DRAWDOWN_DAILY_OUTPUT = "strategy_drawdown_daily.csv"
MONTHLY_RETURNS_OUTPUT = "strategy_monthly_returns.csv"
PERFORMANCE_SUMMARY_OUTPUT = "strategy_performance_summary.csv"
REPORT_OUTPUT = "strategy_equity_analysis.md"
PAPER_PROFILE_SUMMARY_OUTPUT = "paper_profile_summary.csv"

PLOT_OUTPUTS = {
    "equity": "historical_equity_curves.png",
    "normalized_equity": "historical_normalized_equity_curves.png",
    "drawdown": "historical_drawdown_curves.png",
    "cumulative_pnl": "historical_cumulative_net_pnl.png",
    "monthly_heatmap": "historical_monthly_returns_heatmap.png",
    "rolling_3m": "historical_rolling_3m_returns.png",
    "rolling_6m": "historical_rolling_6m_returns.png",
    "paper_equity": "paper_equity_curves_by_profile.png",
    "paper_drawdown": "paper_drawdown_curves_by_profile.png",
}


def run_strategy_equity_analysis(
    *,
    db_path: Path | str,
    output_dir: Path | str | None = None,
    start: str | pd.Timestamp | None = None,
    end: str | pd.Timestamp | None = None,
    include_fdad7487: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, Path]:
    """Reconstruct exact strategy equity curves and write CSV, plots, and Markdown."""

    reports_root, report_dir = resolve_reports_and_analysis_dirs(output_dir)
    reports_root.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    start_ts, end_ts = infer_tournament_dates(db_path=db_path, start=start, end=end, output_dir=reports_root)

    focused_exact = read_csv_optional(reports_root / "focused_challenger_exact_results.csv")
    expanded_exact = read_csv_optional(reports_root / "expanded_clu_tournament_exact.csv")

    normal_universe = load_tournament_universe(db_path=db_path, start=start_ts, end=end_ts)
    expanded_universe = build_expanded_universe(db_path=db_path, start=start_ts, end=end_ts)
    weak_industries = identify_train_weak_industries(expanded_universe, broad_champion_config())

    strategy_results = [
        StrategyCurveResult(
            strategy="original_champion_tpex_500m",
            result=evaluate_strategy(champion_config(), normal_universe),
            summary_row=summary_row_for_original(reports_root),
        ),
        StrategyCurveResult(
            strategy="broad_challenger_097dd332",
            result=evaluate_focused_strategy(load_focused_config(focused_exact, "097dd332"), normal_universe),
            summary_row=summary_row(focused_exact, "focused_config_hash", "097dd332"),
        ),
        StrategyCurveResult(
            strategy="conservative_tpex_35adc734",
            result=evaluate_focused_strategy(load_focused_config(focused_exact, "35adc734"), normal_universe),
            summary_row=summary_row(focused_exact, "focused_config_hash", "35adc734"),
        ),
        StrategyCurveResult(
            strategy="expanded_theme_breadth_3eff3bfd",
            result=evaluate_expanded_config(
                load_expanded_config(expanded_exact, "3eff3bfd"),
                expanded_universe,
                weak_industries=weak_industries,
            ),
            summary_row=summary_row(expanded_exact, "expanded_config_hash", "3eff3bfd"),
        ),
    ]
    if include_fdad7487 and has_hash(expanded_exact, "expanded_config_hash", "fdad7487"):
        strategy_results.append(
            StrategyCurveResult(
                strategy="robustness_candidate_fdad7487",
                result=evaluate_expanded_config(
                    load_expanded_config(expanded_exact, "fdad7487"),
                    expanded_universe,
                    weak_industries=weak_industries,
                ),
                summary_row=summary_row(expanded_exact, "expanded_config_hash", "fdad7487"),
            )
        )

    max_exit = max(
        pd.to_datetime(item.result.daily_equity.get("date", pd.Series([end_ts])), errors="coerce").max()
        for item in strategy_results
    )
    if pd.isna(max_exit):
        max_exit = end_ts
    common_dates = load_common_dates(db_path=db_path, start=start_ts, end=max(end_ts, pd.Timestamp(max_exit)))

    expanded_daily = []
    drawdown_daily = []
    monthly_frames = []
    summary_rows = []
    diagnostics: list[dict[str, Any]] = []
    for item in strategy_results:
        daily = expand_daily_equity(item.result.daily_equity, common_dates, strategy=item.strategy)
        drawdown = calculate_drawdown_daily(daily)
        monthly = calculate_monthly_returns(daily)
        summary = calculate_performance_summary(
            strategy=item.strategy,
            trades=item.result.trades,
            daily=daily,
            drawdown=drawdown,
            monthly=monthly,
            summary_row=item.summary_row,
        )
        diagnostics.append(build_diagnostics(item.strategy, item.result.trades, daily, summary))
        expanded_daily.append(daily)
        drawdown_daily.append(drawdown)
        monthly_frames.append(monthly)
        summary_rows.append(summary)

    equity_daily = pd.concat(expanded_daily, ignore_index=True)
    drawdown = pd.concat(drawdown_daily, ignore_index=True)
    monthly_returns = pd.concat(monthly_frames, ignore_index=True)
    performance_summary = pd.DataFrame(summary_rows)

    equity_daily.to_csv(report_dir / EQUITY_DAILY_OUTPUT, index=False)
    drawdown.to_csv(report_dir / DRAWDOWN_DAILY_OUTPUT, index=False)
    monthly_returns.to_csv(report_dir / MONTHLY_RETURNS_OUTPUT, index=False)
    performance_summary.to_csv(report_dir / PERFORMANCE_SUMMARY_OUTPUT, index=False)

    plot_paths = generate_plots(
        equity_daily=equity_daily,
        drawdown=drawdown,
        monthly_returns=monthly_returns,
        report_dir=report_dir,
    )
    paper_summary, paper_plot_paths = refresh_paper_strategy_analysis(
        output_dir=report_dir,
        ledger_path=reports_root / "live_signals" / "closed_limit_up_paper_ledger.csv",
    )
    plot_paths.update(paper_plot_paths)

    report_text = build_report(
        start=start_ts,
        end=max(end_ts, pd.Timestamp(max_exit)),
        performance_summary=performance_summary,
        monthly_returns=monthly_returns,
        diagnostics=pd.DataFrame(diagnostics),
        plot_paths=plot_paths,
        paper_summary=paper_summary,
    )
    report_path = report_dir / REPORT_OUTPUT
    report_path.write_text(report_text, encoding="utf-8")
    return equity_daily, drawdown, monthly_returns, performance_summary, report_path


def resolve_reports_and_analysis_dirs(output_dir: Path | str | None = None) -> tuple[Path, Path]:
    """Return the reports root and strategy-analysis output directory.

    Passing ``reports`` writes to ``reports/strategy_analysis``. Passing an
    explicit ``reports/strategy_analysis`` path writes there while still reading
    source tournament files from the parent ``reports`` directory.
    """

    base = Path(output_dir) if output_dir is not None else get_settings().project_root / "reports"
    base = base.expanduser()
    if base.name == STRATEGY_ANALYSIS_DIR_NAME:
        return base.parent, base
    return base, base / STRATEGY_ANALYSIS_DIR_NAME


class StrategyCurveResult:
    """Small container for one reconstructed strategy."""

    def __init__(self, *, strategy: str, result: Any, summary_row: dict[str, Any]) -> None:
        self.strategy = strategy
        self.result = result
        self.summary_row = summary_row


def read_csv_optional(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def summary_row(frame: pd.DataFrame, hash_column: str, candidate_hash: str) -> dict[str, Any]:
    if frame.empty or hash_column not in frame.columns:
        return {}
    matches = frame[frame[hash_column].astype(str).eq(candidate_hash)]
    if matches.empty:
        return {}
    return matches.iloc[0].to_dict()


def summary_row_for_original(report_dir: Path) -> dict[str, Any]:
    frame = read_csv_optional(report_dir / "strategy_tournament_results.csv")
    if frame.empty or "strategy_name" not in frame.columns:
        return {}
    matches = frame[frame["strategy_name"].astype(str).eq("champion_tpex_closed_limit_up_overnight")]
    return matches.iloc[0].to_dict() if not matches.empty else {}


def has_hash(frame: pd.DataFrame, hash_column: str, candidate_hash: str) -> bool:
    return not frame.empty and hash_column in frame.columns and frame[hash_column].astype(str).eq(candidate_hash).any()


def load_focused_config(exact: pd.DataFrame, candidate_hash: str) -> FocusedGridConfig:
    row = summary_row(exact, "focused_config_hash", candidate_hash)
    if not row:
        raise FileNotFoundError(f"Focused exact config not found for {candidate_hash}")
    return FocusedGridConfig(
        market=str(row["market"]),  # type: ignore[arg-type]
        min_turnover_twd=float(row["min_turnover_twd"]),
        min_volume_ratio_20d=float(row["min_volume_ratio_20d"]),
        market_regime_filter=str(row["market_regime_filter"]),  # type: ignore[arg-type]
        ranking_method=str(row["ranking_method"]),  # type: ignore[arg-type]
        momentum_cap=str(row.get("momentum_cap", "none")),  # type: ignore[arg-type]
        weak_sector_handling=str(row.get("weak_sector_handling", "avoid_healthcare_materials")),  # type: ignore[arg-type]
    )


def load_expanded_config(exact: pd.DataFrame, candidate_hash: str) -> ExpandedCLUConfig:
    row = summary_row(exact, "expanded_config_hash", candidate_hash)
    if not row:
        raise FileNotFoundError(f"Expanded exact config not found for {candidate_hash}")
    payload: dict[str, Any] = {}
    field_map = {field.name: field for field in fields(ExpandedCLUConfig)}
    for name in field_map:
        if name not in row:
            continue
        value = row[name]
        if pd.isna(value):
            payload[name] = None
            continue
        current = getattr(ExpandedCLUConfig(), name)
        if isinstance(current, bool):
            payload[name] = parse_bool(value)
        elif isinstance(current, int):
            payload[name] = int(float(value))
        elif isinstance(current, float):
            payload[name] = float(value)
        else:
            payload[name] = str(value)
    return ExpandedCLUConfig(**payload)


def parse_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def load_common_dates(*, db_path: Path | str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DatetimeIndex:
    from src.db import get_connection

    with get_connection(db_path, read_only=True) as conn:
        dates = conn.execute(
            """
            SELECT DISTINCT trade_date
            FROM daily_prices
            WHERE trade_date >= ? AND trade_date <= ?
            ORDER BY trade_date
            """,
            [start.date(), end.date()],
        ).fetch_df()
    if dates.empty:
        return pd.DatetimeIndex(pd.bdate_range(start, end))
    return pd.DatetimeIndex(pd.to_datetime(dates["trade_date"], errors="coerce").dropna().dt.normalize().unique()).sort_values()


def expand_daily_equity(daily_equity: pd.DataFrame, dates: pd.DatetimeIndex, *, strategy: str) -> pd.DataFrame:
    """Expand simulator equity rows to the common trading calendar."""

    columns = [
        "strategy",
        "date",
        "start_equity",
        "end_equity",
        "daily_net_pnl",
        "daily_return",
        "cumulative_net_pnl",
        "positions_entered",
        "gross_entry_notional",
        "capital_utilization",
    ]
    if len(dates) == 0:
        return pd.DataFrame(columns=columns)

    frame = daily_equity.copy()
    if frame.empty or "date" not in frame.columns:
        equity = pd.Series(INITIAL_CAPITAL_TWD, index=dates)
        positions = pd.Series(0, index=dates, dtype=float)
        notional = pd.Series(0.0, index=dates)
        utilization = pd.Series(0.0, index=dates)
    else:
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
        frame = frame.dropna(subset=["date"]).sort_values("date")
        grouped = frame.groupby("date", as_index=True).agg(
            end_equity=("end_equity", "last"),
            positions_entered=("positions_entered", "sum"),
            gross_entry_notional=("gross_entry_notional", "sum"),
            capital_utilization=("capital_utilization", "mean"),
        )
        equity = pd.to_numeric(grouped["end_equity"], errors="coerce").reindex(dates).ffill().fillna(INITIAL_CAPITAL_TWD)
        positions = pd.to_numeric(grouped["positions_entered"], errors="coerce").reindex(dates).fillna(0)
        notional = pd.to_numeric(grouped["gross_entry_notional"], errors="coerce").reindex(dates).fillna(0.0)
        utilization = pd.to_numeric(grouped["capital_utilization"], errors="coerce").reindex(dates).fillna(0.0)

    start_equity = equity.shift(1).fillna(INITIAL_CAPITAL_TWD)
    daily_net_pnl = equity - start_equity
    output = pd.DataFrame(
        {
            "strategy": strategy,
            "date": dates,
            "start_equity": start_equity.to_numpy(dtype=float),
            "end_equity": equity.to_numpy(dtype=float),
            "daily_net_pnl": daily_net_pnl.to_numpy(dtype=float),
            "daily_return": np.where(start_equity.to_numpy(dtype=float) != 0, daily_net_pnl / start_equity, np.nan),
            "cumulative_net_pnl": equity.to_numpy(dtype=float) - INITIAL_CAPITAL_TWD,
            "positions_entered": positions.to_numpy(dtype=int),
            "gross_entry_notional": notional.to_numpy(dtype=float),
            "capital_utilization": utilization.to_numpy(dtype=float),
        }
    )
    return output[columns]


def calculate_drawdown_daily(daily: pd.DataFrame) -> pd.DataFrame:
    frame = daily[["strategy", "date", "end_equity"]].copy()
    equity = pd.to_numeric(frame["end_equity"], errors="coerce").fillna(INITIAL_CAPITAL_TWD)
    frame["running_peak_equity"] = equity.cummax()
    frame["drawdown_twd"] = equity - frame["running_peak_equity"]
    frame["drawdown_pct"] = frame["drawdown_twd"] / frame["running_peak_equity"].replace(0, np.nan)
    return frame


def calculate_monthly_returns(daily: pd.DataFrame) -> pd.DataFrame:
    frame = daily.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["month"] = frame["date"].dt.to_period("M").astype(str)
    rows = []
    for month, group in frame.groupby("month", sort=True):
        start_equity = float(group["start_equity"].iloc[0])
        end_equity = float(group["end_equity"].iloc[-1])
        rows.append(
            {
                "strategy": str(group["strategy"].iloc[0]),
                "month": month,
                "start_equity": start_equity,
                "end_equity": end_equity,
                "monthly_return": end_equity / start_equity - 1.0 if start_equity else np.nan,
                "net_pnl": end_equity - start_equity,
                "positions_entered": int(pd.to_numeric(group["positions_entered"], errors="coerce").fillna(0).sum()),
            }
        )
    output = pd.DataFrame(rows)
    if output.empty:
        return output
    output["rolling_3m_return"] = (
        output.groupby("strategy")["monthly_return"].transform(lambda values: (1.0 + values).rolling(3).apply(np.prod, raw=True) - 1.0)
    )
    output["rolling_6m_return"] = (
        output.groupby("strategy")["monthly_return"].transform(lambda values: (1.0 + values).rolling(6).apply(np.prod, raw=True) - 1.0)
    )
    output["year"] = pd.PeriodIndex(output["month"], freq="M").year
    return output


def calculate_performance_summary(
    *,
    strategy: str,
    trades: pd.DataFrame,
    daily: pd.DataFrame,
    drawdown: pd.DataFrame,
    monthly: pd.DataFrame,
    summary_row: dict[str, Any],
) -> dict[str, Any]:
    start_date = pd.to_datetime(daily["date"], errors="coerce").min()
    end_date = pd.to_datetime(daily["date"], errors="coerce").max()
    final_equity = float(pd.to_numeric(daily["end_equity"], errors="coerce").iloc[-1])
    elapsed_years = max((end_date - start_date).days / 365.25, 1 / 365.25)
    total_return = final_equity / INITIAL_CAPITAL_TWD - 1.0
    dd_duration = longest_drawdown_duration(drawdown)
    positive_months = int((pd.to_numeric(monthly.get("monthly_return", pd.Series(dtype=float)), errors="coerce") > 0).sum())
    total_months = int(len(monthly))
    quarterly = period_summary(daily, "Q")
    positive_quarters = int((pd.to_numeric(quarterly.get("period_return", pd.Series(dtype=float)), errors="coerce") > 0).sum())
    total_quarters = int(len(quarterly))
    duplicates = duplicate_trade_count(trades)
    summary_final_equity = safe_float(summary_row.get("final_equity"), np.nan)
    summary_maxdd = safe_float(summary_row.get("max_drawdown_pct"), np.nan)
    reconstructed_maxdd = float(pd.to_numeric(drawdown["drawdown_pct"], errors="coerce").min())
    return {
        "strategy": strategy,
        "trades": int(len(trades)),
        "active_days": int((pd.to_numeric(daily["positions_entered"], errors="coerce").fillna(0) > 0).sum()),
        "start_date": start_date.date().isoformat(),
        "end_date": end_date.date().isoformat(),
        "elapsed_years": elapsed_years,
        "final_equity": final_equity,
        "total_return": total_return,
        "cagr": (final_equity / INITIAL_CAPITAL_TWD) ** (1.0 / elapsed_years) - 1.0,
        "max_drawdown_twd": float(pd.to_numeric(drawdown["drawdown_twd"], errors="coerce").min()),
        "max_drawdown_pct": reconstructed_maxdd,
        "longest_drawdown_trading_days": dd_duration["trading_days"],
        "longest_drawdown_calendar_days": dd_duration["calendar_days"],
        "positive_months": positive_months,
        "total_months": total_months,
        "positive_quarters": positive_quarters,
        "total_quarters": total_quarters,
        "worst_month": worst_period(monthly, "month", "monthly_return"),
        "worst_month_return": float(monthly["monthly_return"].min()) if not monthly.empty else np.nan,
        "best_month": best_period(monthly, "month", "monthly_return"),
        "best_month_return": float(monthly["monthly_return"].max()) if not monthly.empty else np.nan,
        "latest_rolling_3m_return": latest_value(monthly, "rolling_3m_return"),
        "latest_rolling_6m_return": latest_value(monthly, "rolling_6m_return"),
        "summary_final_equity": summary_final_equity,
        "summary_final_equity_diff": final_equity - summary_final_equity if not pd.isna(summary_final_equity) else np.nan,
        "summary_max_drawdown_pct": summary_maxdd,
        "summary_max_drawdown_pct_diff": reconstructed_maxdd - summary_maxdd if not pd.isna(summary_maxdd) else np.nan,
        "duplicate_symbol_date_trades": duplicates,
        "large_daily_move_count": int((pd.to_numeric(daily["daily_return"], errors="coerce").abs() > 0.25).sum()),
    }


def period_summary(daily: pd.DataFrame, period: str) -> pd.DataFrame:
    frame = daily.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["period"] = frame["date"].dt.to_period(period).astype(str)
    rows = []
    for label, group in frame.groupby("period", sort=True):
        start_equity = float(group["start_equity"].iloc[0])
        end_equity = float(group["end_equity"].iloc[-1])
        rows.append({"period": label, "period_return": end_equity / start_equity - 1.0 if start_equity else np.nan})
    return pd.DataFrame(rows)


def longest_drawdown_duration(drawdown: pd.DataFrame) -> dict[str, int]:
    frame = drawdown.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame = frame.dropna(subset=["date"]).sort_values("date")
    in_drawdown = pd.to_numeric(frame["drawdown_pct"], errors="coerce").fillna(0.0) < -1e-12
    longest_trading = 0
    longest_calendar = 0
    current_start: pd.Timestamp | None = None
    current_trading = 0
    last_date: pd.Timestamp | None = None
    for date, active in zip(frame["date"], in_drawdown, strict=True):
        if active:
            if current_start is None:
                current_start = pd.Timestamp(date)
                current_trading = 0
            current_trading += 1
            last_date = pd.Timestamp(date)
        elif current_start is not None:
            longest_trading = max(longest_trading, current_trading)
            longest_calendar = max(longest_calendar, int((last_date - current_start).days) if last_date is not None else 0)
            current_start = None
            current_trading = 0
            last_date = None
    if current_start is not None:
        longest_trading = max(longest_trading, current_trading)
        longest_calendar = max(longest_calendar, int((last_date - current_start).days) if last_date is not None else 0)
    return {"trading_days": int(longest_trading), "calendar_days": int(longest_calendar)}


def duplicate_trade_count(trades: pd.DataFrame) -> int:
    keys = [column for column in ["symbol", "market", "signal_date"] if column in trades.columns]
    if not keys or trades.empty:
        return 0
    return int(trades.duplicated(subset=keys, keep=False).sum())


def worst_period(frame: pd.DataFrame, label_column: str, value_column: str) -> str:
    if frame.empty or value_column not in frame.columns:
        return ""
    return str(frame.sort_values(value_column).iloc[0][label_column])


def best_period(frame: pd.DataFrame, label_column: str, value_column: str) -> str:
    if frame.empty or value_column not in frame.columns:
        return ""
    return str(frame.sort_values(value_column, ascending=False).iloc[0][label_column])


def latest_value(frame: pd.DataFrame, column: str) -> float:
    if frame.empty or column not in frame.columns:
        return np.nan
    values = pd.to_numeric(frame[column], errors="coerce").dropna()
    return float(values.iloc[-1]) if not values.empty else np.nan


def build_diagnostics(strategy: str, trades: pd.DataFrame, daily: pd.DataFrame, summary: dict[str, Any]) -> dict[str, Any]:
    monthly_jump_share = largest_positive_month_share(daily)
    pnl = pd.to_numeric(daily["daily_net_pnl"], errors="coerce").fillna(0.0)
    total_pnl = pnl.sum()
    top_10_day_share = float(pnl.nlargest(10).sum() / total_pnl) if total_pnl > 0 else np.nan
    return {
        "strategy": strategy,
        "largest_positive_month_pnl_share": monthly_jump_share,
        "top_10_positive_day_pnl_share": top_10_day_share,
        "duplicate_symbol_date_trades": summary["duplicate_symbol_date_trades"],
        "large_daily_move_count": summary["large_daily_move_count"],
        "summary_final_equity_diff": summary["summary_final_equity_diff"],
        "summary_max_drawdown_pct_diff": summary["summary_max_drawdown_pct_diff"],
        "longest_drawdown_trading_days": summary["longest_drawdown_trading_days"],
    }


def largest_positive_month_share(daily: pd.DataFrame) -> float:
    monthly = calculate_monthly_returns(daily)
    if monthly.empty:
        return np.nan
    gains = pd.to_numeric(monthly["net_pnl"], errors="coerce").fillna(0.0)
    total_gain = gains[gains > 0].sum()
    return float(gains.max() / total_gain) if total_gain > 0 else np.nan


def generate_plots(
    *,
    equity_daily: pd.DataFrame,
    drawdown: pd.DataFrame,
    monthly_returns: pd.DataFrame,
    report_dir: Path,
) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    paths["equity"] = plot_lines(
        equity_daily,
        y="end_equity",
        title="Historical Equity Curves",
        ylabel="Equity (TWD)",
        output_path=report_dir / PLOT_OUTPUTS["equity"],
    )
    normalized = equity_daily.copy()
    normalized["normalized_equity"] = normalized["end_equity"] / INITIAL_CAPITAL_TWD
    paths["normalized_equity"] = plot_lines(
        normalized,
        y="normalized_equity",
        title="Historical Normalized Equity Curves",
        ylabel="Equity (start = 1.0)",
        output_path=report_dir / PLOT_OUTPUTS["normalized_equity"],
    )
    dd = drawdown.copy()
    dd["drawdown_percent"] = pd.to_numeric(dd["drawdown_pct"], errors="coerce") * 100.0
    paths["drawdown"] = plot_lines(
        dd.rename(columns={"drawdown_percent": "value"}),
        y="value",
        title="Historical Drawdown Curves",
        ylabel="Drawdown (%)",
        output_path=report_dir / PLOT_OUTPUTS["drawdown"],
    )
    paths["cumulative_pnl"] = plot_lines(
        equity_daily,
        y="cumulative_net_pnl",
        title="Historical Cumulative Net PnL",
        ylabel="Cumulative Net PnL (TWD)",
        output_path=report_dir / PLOT_OUTPUTS["cumulative_pnl"],
    )
    paths["monthly_heatmap"] = plot_monthly_heatmap(monthly_returns, report_dir / PLOT_OUTPUTS["monthly_heatmap"])
    paths["rolling_3m"] = plot_monthly_lines(
        monthly_returns,
        y="rolling_3m_return",
        title="Rolling 3-Month Returns",
        ylabel="Rolling 3M Return (%)",
        output_path=report_dir / PLOT_OUTPUTS["rolling_3m"],
    )
    paths["rolling_6m"] = plot_monthly_lines(
        monthly_returns,
        y="rolling_6m_return",
        title="Rolling 6-Month Returns",
        ylabel="Rolling 6M Return (%)",
        output_path=report_dir / PLOT_OUTPUTS["rolling_6m"],
    )
    return paths


def plot_lines(frame: pd.DataFrame, *, y: str, title: str, ylabel: str, output_path: Path) -> Path:
    fig, ax = plt.subplots(figsize=(13, 7))
    for strategy, group in frame.groupby("strategy", sort=False):
        group = group.copy()
        group["date"] = pd.to_datetime(group["date"], errors="coerce")
        ax.plot(group["date"], pd.to_numeric(group[y], errors="coerce"), label=strategy, linewidth=1.8)
    ax.set_title(title)
    ax.set_ylabel(ylabel)
    ax.set_xlabel("Date")
    ax.grid(True, alpha=0.25)
    ax.legend(loc="best", fontsize=8)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    return output_path


def plot_monthly_lines(frame: pd.DataFrame, *, y: str, title: str, ylabel: str, output_path: Path) -> Path:
    fig, ax = plt.subplots(figsize=(13, 7))
    if not frame.empty:
        for strategy, group in frame.groupby("strategy", sort=False):
            dates = pd.PeriodIndex(group["month"], freq="M").to_timestamp()
            values = pd.to_numeric(group[y], errors="coerce") * 100.0
            ax.plot(dates, values, label=strategy, linewidth=1.8)
    ax.axhline(0, color="black", linewidth=0.8, alpha=0.5)
    ax.set_title(title)
    ax.set_ylabel(ylabel)
    ax.set_xlabel("Month")
    ax.grid(True, alpha=0.25)
    ax.legend(loc="best", fontsize=8)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    return output_path


def plot_monthly_heatmap(monthly: pd.DataFrame, output_path: Path) -> Path:
    pivot = monthly.pivot_table(index="strategy", columns="month", values="monthly_return", aggfunc="sum").fillna(0.0)
    fig_width = max(13, 0.35 * max(len(pivot.columns), 1))
    fig_height = max(5, 0.55 * max(len(pivot.index), 1) + 2)
    fig, ax = plt.subplots(figsize=(fig_width, fig_height))
    if pivot.empty:
        ax.text(0.5, 0.5, "No monthly returns", ha="center", va="center")
        ax.axis("off")
    else:
        values = pivot.to_numpy(dtype=float) * 100.0
        vmax = max(1.0, float(np.nanmax(np.abs(values))))
        image = ax.imshow(values, aspect="auto", cmap="RdYlGn", vmin=-vmax, vmax=vmax)
        ax.set_xticks(np.arange(len(pivot.columns)))
        ax.set_xticklabels(pivot.columns, rotation=90, fontsize=7)
        ax.set_yticks(np.arange(len(pivot.index)))
        ax.set_yticklabels(pivot.index, fontsize=8)
        ax.set_title("Historical Monthly Returns Heatmap (%)")
        cbar = fig.colorbar(image, ax=ax)
        cbar.set_label("Monthly return (%)")
    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    return output_path


def refresh_paper_strategy_analysis(
    *,
    output_dir: Path | str | None = None,
    ledger_path: Path | str | None = None,
) -> tuple[pd.DataFrame, dict[str, Path]]:
    """Refresh the lightweight forward-paper strategy-analysis artifacts."""

    reports_root, analysis_dir = resolve_reports_and_analysis_dirs(output_dir)
    analysis_dir.mkdir(parents=True, exist_ok=True)
    ledger = Path(ledger_path) if ledger_path is not None else reports_root / "live_signals" / "closed_limit_up_paper_ledger.csv"
    paper_equity, paper_drawdown, paper_summary = build_paper_curve_frames(ledger)
    paper_summary.to_csv(analysis_dir / PAPER_PROFILE_SUMMARY_OUTPUT, index=False)

    paths: dict[str, Path] = {}
    if paper_equity.empty:
        paths["paper_equity"] = plot_placeholder(
            analysis_dir / PLOT_OUTPUTS["paper_equity"],
            title="Forward Paper Equity Curves By Profile",
            message="No evaluated paper ledger rows yet",
        )
        paths["paper_drawdown"] = plot_placeholder(
            analysis_dir / PLOT_OUTPUTS["paper_drawdown"],
            title="Forward Paper Drawdown Curves By Profile",
            message="No evaluated paper ledger rows yet",
        )
    else:
        paths["paper_equity"] = plot_lines(
            paper_equity,
            y="end_equity",
            title="Forward Paper Equity Curves By Profile",
            ylabel="Paper Equity (TWD, start = 1M)",
            output_path=analysis_dir / PLOT_OUTPUTS["paper_equity"],
        )
        dd = paper_drawdown.copy()
        dd["drawdown_percent"] = pd.to_numeric(dd["drawdown_pct"], errors="coerce") * 100.0
        paths["paper_drawdown"] = plot_lines(
            dd.rename(columns={"drawdown_percent": "value"}),
            y="value",
            title="Forward Paper Drawdown Curves By Profile",
            ylabel="Drawdown (%)",
            output_path=analysis_dir / PLOT_OUTPUTS["paper_drawdown"],
        )
    return paper_summary, paths


def generate_paper_equity_plot(*, report_dir: Path) -> Path | None:
    """Backward-compatible wrapper for older callers."""

    _summary, paths = refresh_paper_strategy_analysis(output_dir=report_dir)
    return paths.get("paper_equity")


def build_paper_curve_frames(ledger_path: Path | str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Build forward-paper equity, drawdown, and profile summary frames."""

    equity_columns = ["strategy", "date", "end_equity", "daily_net_pnl", "daily_return", "cumulative_net_pnl"]
    drawdown_columns = ["strategy", "date", "end_equity", "running_peak_equity", "drawdown_twd", "drawdown_pct"]
    summary_columns = [
        "profile_name",
        "evaluated_trades",
        "start_date",
        "end_date",
        "final_equity",
        "net_pnl",
        "avg_net_return",
        "median_net_return",
        "win_rate",
        "profit_factor",
        "max_drawdown_twd",
        "max_drawdown_pct",
    ]
    path = Path(ledger_path)
    if not path.exists():
        return (
            pd.DataFrame(columns=equity_columns),
            pd.DataFrame(columns=drawdown_columns),
            pd.DataFrame(columns=summary_columns),
        )
    try:
        ledger = pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return (
            pd.DataFrame(columns=equity_columns),
            pd.DataFrame(columns=drawdown_columns),
            pd.DataFrame(columns=summary_columns),
        )
    if ledger.empty or "profile_name" not in ledger.columns or "status" not in ledger.columns:
        return (
            pd.DataFrame(columns=equity_columns),
            pd.DataFrame(columns=drawdown_columns),
            pd.DataFrame(columns=summary_columns),
        )

    evaluated = ledger[ledger["status"].astype("string").eq("evaluated")].copy()
    if evaluated.empty:
        return (
            pd.DataFrame(columns=equity_columns),
            pd.DataFrame(columns=drawdown_columns),
            pd.DataFrame(columns=summary_columns),
        )

    date_columns = [column for column in ["day1_trade_date", "evaluation_date", "signal_date"] if column in evaluated.columns]
    if date_columns:
        evaluated["date"] = pd.to_datetime(evaluated[date_columns].bfill(axis=1).iloc[:, 0], errors="coerce").dt.normalize()
    else:
        evaluated["date"] = pd.NaT
    evaluated = evaluated.dropna(subset=["date"])
    if evaluated.empty:
        return (
            pd.DataFrame(columns=equity_columns),
            pd.DataFrame(columns=drawdown_columns),
            pd.DataFrame(columns=summary_columns),
        )

    equity_rows = []
    drawdown_rows = []
    summary_rows = []
    for profile, group in evaluated.groupby("profile_name", sort=True):
        dates = pd.DatetimeIndex(pd.date_range(group["date"].min(), group["date"].max(), freq="D"))
        net_pnl_by_date = pd.to_numeric(group["net_pnl"], errors="coerce").fillna(0.0).groupby(group["date"]).sum()
        daily_pnl = net_pnl_by_date.reindex(dates).fillna(0.0)
        equity = INITIAL_CAPITAL_TWD + daily_pnl.cumsum()
        start_equity = equity.shift(1).fillna(INITIAL_CAPITAL_TWD)
        daily_return = np.where(start_equity.to_numpy(dtype=float) != 0, daily_pnl.to_numpy(dtype=float) / start_equity, np.nan)
        equity_frame = pd.DataFrame(
            {
                "strategy": str(profile),
                "date": dates,
                "end_equity": equity.to_numpy(dtype=float),
                "daily_net_pnl": daily_pnl.to_numpy(dtype=float),
                "daily_return": daily_return,
                "cumulative_net_pnl": equity.to_numpy(dtype=float) - INITIAL_CAPITAL_TWD,
            }
        )
        running_peak = equity.cummax()
        drawdown_frame = pd.DataFrame(
            {
                "strategy": str(profile),
                "date": dates,
                "end_equity": equity.to_numpy(dtype=float),
                "running_peak_equity": running_peak.to_numpy(dtype=float),
                "drawdown_twd": (equity - running_peak).to_numpy(dtype=float),
                "drawdown_pct": ((equity - running_peak) / running_peak.replace(0, np.nan)).to_numpy(dtype=float),
            }
        )
        profile_pnl = pd.to_numeric(group["net_pnl"], errors="coerce").fillna(0.0)
        profile_returns = pd.to_numeric(group["net_return"], errors="coerce") if "net_return" in group.columns else pd.Series(dtype=float)
        gains = float(profile_pnl[profile_pnl > 0].sum())
        losses = float(abs(profile_pnl[profile_pnl < 0].sum()))
        summary_rows.append(
            {
                "profile_name": str(profile),
                "evaluated_trades": int(len(group)),
                "start_date": dates.min().date().isoformat(),
                "end_date": dates.max().date().isoformat(),
                "final_equity": float(equity.iloc[-1]),
                "net_pnl": float(profile_pnl.sum()),
                "avg_net_return": float(profile_returns.mean()) if not profile_returns.dropna().empty else np.nan,
                "median_net_return": float(profile_returns.median()) if not profile_returns.dropna().empty else np.nan,
                "win_rate": float((profile_pnl > 0).mean()) if len(profile_pnl) else np.nan,
                "profit_factor": profit_factor_from_pnl(gains, losses),
                "max_drawdown_twd": float(drawdown_frame["drawdown_twd"].min()),
                "max_drawdown_pct": float(drawdown_frame["drawdown_pct"].min()),
            }
        )
        equity_rows.append(equity_frame[equity_columns])
        drawdown_rows.append(drawdown_frame[drawdown_columns])

    return (
        pd.concat(equity_rows, ignore_index=True) if equity_rows else pd.DataFrame(columns=equity_columns),
        pd.concat(drawdown_rows, ignore_index=True) if drawdown_rows else pd.DataFrame(columns=drawdown_columns),
        pd.DataFrame(summary_rows, columns=summary_columns),
    )


def plot_placeholder(output_path: Path, *, title: str, message: str) -> Path:
    fig, ax = plt.subplots(figsize=(12, 6))
    ax.set_title(title)
    ax.text(0.5, 0.5, message, ha="center", va="center", fontsize=12)
    ax.axis("off")
    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    return output_path


def paper_curve_summary(ledger_path: Path) -> pd.DataFrame:
    _equity, _drawdown, summary = build_paper_curve_frames(ledger_path)
    return summary


def build_report(
    *,
    start: pd.Timestamp,
    end: pd.Timestamp,
    performance_summary: pd.DataFrame,
    monthly_returns: pd.DataFrame,
    diagnostics: pd.DataFrame,
    plot_paths: dict[str, Path],
    paper_summary: pd.DataFrame,
) -> str:
    mismatches = performance_summary[
        (pd.to_numeric(performance_summary["summary_final_equity_diff"], errors="coerce").abs() > 1.0)
        | (pd.to_numeric(performance_summary["summary_max_drawdown_pct_diff"], errors="coerce").abs() > 0.0005)
    ].copy()
    smoother = performance_summary.sort_values(["max_drawdown_pct", "longest_drawdown_trading_days"], ascending=[False, True])
    smoothest = str(smoother.iloc[0]["strategy"]) if not smoother.empty else ""
    longest = performance_summary.sort_values("longest_drawdown_trading_days", ascending=False)
    longest_name = str(longest.iloc[0]["strategy"]) if not longest.empty else ""
    compare_line = compare_expanded_vs_broad(monthly_returns)
    continuity = suspicious_continuity_summary(performance_summary)

    lines = [
        "# Strategy Equity Analysis",
        "",
        f"Common OOS window: `{start.date().isoformat()}` to `{end.date().isoformat()}`",
        f"Starting equity per strategy: `{INITIAL_CAPITAL_TWD:,.0f} TWD`",
        "",
        "## Generated Files",
        "",
        *[f"- `{path}`" for path in plot_paths.values()],
        f"- `{EQUITY_DAILY_OUTPUT}`",
        f"- `{DRAWDOWN_DAILY_OUTPUT}`",
        f"- `{MONTHLY_RETURNS_OUTPUT}`",
        f"- `{PERFORMANCE_SUMMARY_OUTPUT}`",
        "",
        "## Performance Summary",
        "",
        markdown_table(
            performance_summary[
                [
                    "strategy",
                    "trades",
                    "final_equity",
                    "cagr",
                    "max_drawdown_pct",
                    "longest_drawdown_trading_days",
                    "positive_months",
                    "total_months",
                    "positive_quarters",
                    "total_quarters",
                    "worst_month",
                    "worst_month_return",
                    "best_month",
                    "best_month_return",
                ]
            ],
            max_rows=20,
        ),
        "",
        "## Answers",
        "",
        f"- Equity consistency: {equity_consistency_answer(diagnostics)}",
        f"- Smoothest growth: `{smoothest}` has the least severe daily reconstructed drawdown, paired with drawdown duration checks.",
        f"- Longest flat/drawdown period: `{longest_name}` has the longest reconstructed drawdown stretch.",
        f"- 3eff3bfd versus 097dd332: {compare_line}",
        f"- Max drawdown reproduction: {drawdown_reproduction_answer(performance_summary)}",
        f"- Suspicious discontinuities / duplicate trades / capital accounting: {continuity}",
        f"- Historical versus forward paper: {paper_comparison_answer(performance_summary, paper_summary)}",
        "",
        "## Summary Metric Mismatches",
        "",
        markdown_table(
            mismatches[
                [
                    "strategy",
                    "final_equity",
                    "summary_final_equity",
                    "summary_final_equity_diff",
                    "max_drawdown_pct",
                    "summary_max_drawdown_pct",
                    "summary_max_drawdown_pct_diff",
                ]
            ]
            if not mismatches.empty
            else mismatches
        ),
        "",
        "## Diagnostics",
        "",
        markdown_table(diagnostics, max_rows=20),
        "",
        "## Forward Paper Summary",
        "",
        markdown_table(paper_summary),
        "",
        "Strict note: historical backtests validate the modeled price edge and capital path only. Forward paper results still do not prove live executability until manual Day0 close fill observations are imported.",
        "",
    ]
    return "\n".join(lines)


def compare_expanded_vs_broad(monthly: pd.DataFrame) -> str:
    if monthly.empty:
        return "No monthly data was available."
    pivot = monthly.pivot_table(index="month", columns="strategy", values="monthly_return", aggfunc="sum")
    left = "expanded_theme_breadth_3eff3bfd"
    right = "broad_challenger_097dd332"
    if left not in pivot.columns or right not in pivot.columns:
        return "Could not compare because one of the two curves was missing."
    spread = pivot[left].fillna(0.0) - pivot[right].fillna(0.0)
    win_months = int((spread > 0).sum())
    total = int(len(spread))
    avg_spread = float(spread.mean())
    return f"`{left}` beat `{right}` in {win_months}/{total} months, with average monthly return spread {avg_spread:.2%}."


def equity_consistency_answer(diagnostics: pd.DataFrame) -> str:
    if diagnostics.empty:
        return "No diagnostics were available."
    rows = []
    for row in diagnostics.to_dict("records"):
        jump_share = safe_float(row.get("largest_positive_month_pnl_share"), np.nan)
        day_share = safe_float(row.get("top_10_positive_day_pnl_share"), np.nan)
        if pd.isna(jump_share):
            phrase = "no positive months"
        elif jump_share > 0.35 or day_share > 0.45:
            phrase = "meaningfully jump-driven"
        else:
            phrase = "broadly rising rather than one-jump driven"
        rows.append(f"{row['strategy']}: {phrase}")
    return "; ".join(rows) + "."


def drawdown_reproduction_answer(summary: pd.DataFrame) -> str:
    diffs = pd.to_numeric(summary["summary_max_drawdown_pct_diff"], errors="coerce").abs().dropna()
    if diffs.empty:
        return "No source summary max-drawdown rows were available for comparison."
    max_diff = float(diffs.max())
    if max_diff <= 0.0005:
        return f"Reconstructed daily equity reproduces source max drawdowns within {max_diff:.4%}."
    return f"Mismatch flagged: largest max-drawdown difference is {max_diff:.4%}."


def suspicious_continuity_summary(summary: pd.DataFrame) -> str:
    duplicate_count = int(pd.to_numeric(summary["duplicate_symbol_date_trades"], errors="coerce").fillna(0).sum())
    large_moves = int(pd.to_numeric(summary["large_daily_move_count"], errors="coerce").fillna(0).sum())
    mismatches = int(
        (
            pd.to_numeric(summary["summary_final_equity_diff"], errors="coerce").abs().fillna(0.0) > 1.0
        ).sum()
    )
    parts = []
    parts.append(f"{duplicate_count} duplicate same-symbol/same-date trade rows")
    parts.append(f"{large_moves} daily return moves above 25%")
    parts.append(f"{mismatches} final-equity mismatches versus source summaries")
    return ", ".join(parts) + "."


def paper_comparison_answer(historical: pd.DataFrame, paper: pd.DataFrame) -> str:
    if paper.empty:
        return "No evaluated forward paper ledger rows were available."
    paper_rows = int(paper["evaluated_trades"].sum())
    best = paper.sort_values("net_pnl", ascending=False).iloc[0]
    historical_best = historical.sort_values("final_equity", ascending=False).iloc[0]
    return (
        f"Forward paper is still tiny at {paper_rows} evaluated profile rows; "
        f"best paper profile so far is `{best['profile_name']}`, while the historical leader is `{historical_best['strategy']}`. "
        "Treat the forward curves as price-edge smoke tests, not promotion evidence."
    )


def profit_factor_from_pnl(gains: float, losses: float) -> float:
    if losses == 0 and gains > 0:
        return float("inf")
    if losses == 0:
        return np.nan
    return float(gains / losses)


def safe_float(value: Any, default: float = np.nan) -> float:
    try:
        if pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate historical CLU strategy equity analysis")
    parser.add_argument("--db", type=Path, default=get_settings().db_path)
    parser.add_argument("--output-dir", type=Path, default=get_settings().project_root / "reports")
    parser.add_argument("--paper-only", action="store_true", help="Only refresh forward-paper charts and summary.")
    parser.add_argument(
        "--ledger",
        type=Path,
        help="Paper ledger path for --paper-only. Defaults to <reports-root>/live_signals/closed_limit_up_paper_ledger.csv.",
    )
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--skip-fdad7487", action="store_true")
    args = parser.parse_args()

    if args.paper_only:
        summary, paths = refresh_paper_strategy_analysis(output_dir=args.output_dir, ledger_path=args.ledger)
        _reports_root, analysis_dir = resolve_reports_and_analysis_dirs(args.output_dir)
        print(f"Refreshed paper strategy analysis under {analysis_dir}")
        for path in paths.values():
            print(f"- {path}")
        print(markdown_table(summary, max_rows=20))
        return

    _equity, _drawdown, _monthly, summary, report_path = run_strategy_equity_analysis(
        db_path=args.db,
        output_dir=args.output_dir,
        start=args.start,
        end=args.end,
        include_fdad7487=not args.skip_fdad7487,
    )
    print(f"Wrote strategy equity analysis to {report_path}")
    print(markdown_table(summary[["strategy", "trades", "final_equity", "cagr", "max_drawdown_pct"]], max_rows=20))


if __name__ == "__main__":
    main()
