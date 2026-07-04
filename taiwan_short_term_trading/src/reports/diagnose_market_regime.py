"""Market-regime validation for the closed-limit-up overnight strategy."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from config.settings import get_settings
from src.db import get_connection, init_db
from src.reports.diagnose_closed_limit_up_execution import load_and_filter_oos_trades


REGIME_TRADES_OUTPUT = "closed_limit_up_market_regime_trades.csv"
REGIME_SUMMARY_OUTPUT = "closed_limit_up_market_regime_summary.csv"
REGIME_STRESS_OUTPUT = "closed_limit_up_market_regime_stress_tests.csv"
REGIME_REPORT_OUTPUT = "closed_limit_up_market_regime_report.md"

SUMMARY_COLUMNS = [
    "summary_level",
    "group",
    "trades",
    "avg_net_return",
    "median_net_return",
    "win_rate",
    "profit_factor",
    "total_net_pnl",
    "max_drawdown",
]

STRESS_COLUMNS = [
    "scenario",
    "trades",
    "trade_share",
    "avg_net_return",
    "median_net_return",
    "win_rate",
    "profit_factor",
    "total_net_pnl",
    "net_pnl_share",
    "max_drawdown",
]


def generate_market_regime_diagnostic(
    *,
    db_path: Path | str,
    oos_trades_path: Path | str,
    output_dir: Path | str | None = None,
    index_symbol: str = "TAIEX",
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, Path]:
    """Generate market-regime diagnostic files for the best OOS strategy."""

    report_dir = Path(output_dir) if output_dir is not None else get_settings().project_root / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    init_db(db_path)

    trades = load_and_filter_oos_trades(oos_trades_path)
    regime_trades = join_taiex_regime_features(trades, db_path=db_path, index_symbol=index_symbol)
    summary = summarize_market_regimes(regime_trades)
    stress = run_market_regime_stress_tests(regime_trades)
    report_text = build_market_regime_report(
        regime_trades=regime_trades,
        summary=summary,
        stress=stress,
        db_path=Path(db_path),
        oos_trades_path=Path(oos_trades_path),
        index_symbol=index_symbol,
    )

    regime_trades.to_csv(report_dir / REGIME_TRADES_OUTPUT, index=False)
    summary.to_csv(report_dir / REGIME_SUMMARY_OUTPUT, index=False)
    stress.to_csv(report_dir / REGIME_STRESS_OUTPUT, index=False)
    report_path = report_dir / REGIME_REPORT_OUTPUT
    report_path.write_text(report_text, encoding="utf-8")
    return regime_trades, summary, stress, report_path


def join_taiex_regime_features(
    trades: pd.DataFrame,
    *,
    db_path: Path | str,
    index_symbol: str = "TAIEX",
) -> pd.DataFrame:
    """Join best-strategy OOS trades to Day0 TAIEX regime features."""

    if trades.empty:
        return empty_regime_trades()

    frame = trades.copy()
    frame["signal_date"] = pd.to_datetime(frame["signal_date"], errors="coerce").dt.normalize()
    start = frame["signal_date"].min() - pd.Timedelta(days=120)
    end = frame["signal_date"].max()
    index_features = load_taiex_features(db_path=db_path, index_symbol=index_symbol, start=start, end=end)
    if index_features.empty:
        raise ValueError(
            f"No {index_symbol.upper()} rows found in index_daily_prices for {start.date()} to {end.date()}. "
            "Import CSV with collect_market_context import-index-csv or use collect-taiex-public first."
        )

    joined = frame.merge(
        index_features,
        left_on="signal_date",
        right_on="trade_date",
        how="left",
    ).drop(columns=["trade_date"], errors="ignore")
    joined["index_data_available"] = joined["taiex_close"].notna()
    joined["bull_regime"] = joined["taiex_close_above_ma20"].fillna(False).astype(bool) & joined[
        "taiex_close_above_ma60"
    ].fillna(False).astype(bool)
    joined["bear_regime"] = (~joined["taiex_close_above_ma20"].fillna(False).astype(bool)) & (
        ~joined["taiex_close_above_ma60"].fillna(False).astype(bool)
    ) & joined["index_data_available"]
    joined["correction_regime"] = pd.to_numeric(joined["taiex_drawdown_from_60d_high"], errors="coerce") <= -0.05
    joined["strong_market_day"] = pd.to_numeric(joined["taiex_day0_return"], errors="coerce") >= 0.005
    joined["weak_market_day"] = pd.to_numeric(joined["taiex_day0_return"], errors="coerce") <= -0.005
    joined["taiex_day0_return_bucket"] = bucket_return(joined["taiex_day0_return"], short_horizon=True)
    joined["taiex_5d_return_bucket"] = bucket_return(joined["taiex_5d_return"], short_horizon=False)
    joined["taiex_20d_return_bucket"] = bucket_return(joined["taiex_20d_return"], short_horizon=False)
    joined["year"] = joined["signal_date"].dt.year
    joined["quarter"] = joined["signal_date"].dt.to_period("Q").astype(str)
    return select_regime_trade_columns(joined)


def load_taiex_features(
    *,
    db_path: Path | str,
    index_symbol: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> pd.DataFrame:
    """Load TAIEX rows and compute 5D/20D returns from historical closes."""

    with get_connection(db_path, read_only=True) as conn:
        frame = conn.execute(
            """
            SELECT
                trade_date,
                open,
                high,
                low,
                close,
                volume,
                turnover_twd,
                daily_return,
                ma5,
                ma20,
                ma60,
                close_above_ma20,
                close_above_ma60,
                drawdown_from_60d_high,
                source
            FROM index_daily_prices
            WHERE UPPER(index_symbol) = ?
              AND trade_date >= ?
              AND trade_date <= ?
            ORDER BY trade_date
            """,
            [index_symbol.upper(), pd.Timestamp(start).date(), pd.Timestamp(end).date()],
        ).fetch_df()
    if frame.empty:
        return empty_index_features()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce").dt.normalize()
    frame = frame.sort_values("trade_date").reset_index(drop=True)
    close = pd.to_numeric(frame["close"], errors="coerce")
    frame["taiex_5d_return"] = close / close.shift(5) - 1.0
    frame["taiex_20d_return"] = close / close.shift(20) - 1.0
    frame = frame.rename(
        columns={
            "open": "taiex_open",
            "high": "taiex_high",
            "low": "taiex_low",
            "close": "taiex_close",
            "volume": "taiex_volume",
            "turnover_twd": "taiex_turnover_twd",
            "daily_return": "taiex_day0_return",
            "ma5": "taiex_ma5",
            "ma20": "taiex_ma20",
            "ma60": "taiex_ma60",
            "close_above_ma20": "taiex_close_above_ma20",
            "close_above_ma60": "taiex_close_above_ma60",
            "drawdown_from_60d_high": "taiex_drawdown_from_60d_high",
            "source": "taiex_source",
        }
    )
    return frame[index_feature_columns()]


def summarize_market_regimes(regime_trades: pd.DataFrame) -> pd.DataFrame:
    """Create grouped market-regime performance summaries."""

    if regime_trades.empty:
        return pd.DataFrame(columns=SUMMARY_COLUMNS)
    group_specs = [
        ("overall", None),
        ("by_bull_regime", "bull_regime"),
        ("by_bear_regime", "bear_regime"),
        ("by_correction_regime", "correction_regime"),
        ("by_weak_market_day", "weak_market_day"),
        ("by_strong_market_day", "strong_market_day"),
        ("by_taiex_day0_return_bucket", "taiex_day0_return_bucket"),
        ("by_taiex_5d_return_bucket", "taiex_5d_return_bucket"),
        ("by_taiex_20d_return_bucket", "taiex_20d_return_bucket"),
        ("by_year", "year"),
        ("by_quarter", "quarter"),
        ("by_index_data_available", "index_data_available"),
    ]
    rows: list[dict[str, Any]] = []
    for level, column in group_specs:
        if column is None:
            metrics = calculate_metrics(regime_trades)
            rows.append({"summary_level": level, "group": "all", **metrics})
            continue
        for value, group in regime_trades.groupby(column, dropna=False, observed=False):
            rows.append({"summary_level": level, "group": str(value), **calculate_metrics(group)})
    return pd.DataFrame(rows, columns=SUMMARY_COLUMNS)


def run_market_regime_stress_tests(regime_trades: pd.DataFrame) -> pd.DataFrame:
    """Run requested market-regime stress scenarios."""

    if regime_trades.empty:
        return pd.DataFrame(columns=STRESS_COLUMNS)
    total_trades = len(regime_trades)
    total_pnl = pd.to_numeric(regime_trades["net_pnl"], errors="coerce").fillna(0.0).sum()
    scenarios = {
        "trade_all_regimes": pd.Series(True, index=regime_trades.index),
        "trade_only_bull_regime": regime_trades["bull_regime"].fillna(False).astype(bool),
        "trade_only_not_bear_regime": ~regime_trades["bear_regime"].fillna(False).astype(bool),
        "trade_only_taiex_day0_return_ge_0": pd.to_numeric(regime_trades["taiex_day0_return"], errors="coerce") >= 0,
        "avoid_correction_regime": ~regime_trades["correction_regime"].fillna(False).astype(bool),
        "avoid_weak_market_day": ~regime_trades["weak_market_day"].fillna(False).astype(bool),
        "bull_regime_and_avoid_weak_market_day": regime_trades["bull_regime"].fillna(False).astype(bool)
        & (~regime_trades["weak_market_day"].fillna(False).astype(bool)),
    }
    rows: list[dict[str, Any]] = []
    for scenario, mask in scenarios.items():
        group = regime_trades[mask.fillna(False)]
        metrics = calculate_metrics(group)
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
                "net_pnl_share": metrics["total_net_pnl"] / total_pnl if total_pnl else np.nan,
                "max_drawdown": metrics["max_drawdown"],
            }
        )
    return pd.DataFrame(rows, columns=STRESS_COLUMNS)


def calculate_metrics(frame: pd.DataFrame) -> dict[str, float]:
    if frame.empty:
        return {
            "trades": 0,
            "avg_net_return": np.nan,
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
        "avg_net_return": float(net_return.mean()),
        "median_net_return": float(net_return.median()),
        "win_rate": float((net_pnl > 0).mean()),
        "profit_factor": profit_factor,
        "total_net_pnl": float(net_pnl.sum()),
        "max_drawdown": max_drawdown_by_exit_date(frame),
    }


def max_drawdown_by_exit_date(frame: pd.DataFrame) -> float:
    if frame.empty:
        return 0.0
    dated = frame.copy()
    dated["_pnl_date"] = pd.to_datetime(dated.get("exit_date", dated["entry_date"]), errors="coerce").dt.normalize()
    daily = dated.groupby("_pnl_date", dropna=False)["net_pnl"].sum().sort_index()
    equity = pd.concat([pd.Series([0.0]), daily.reset_index(drop=True)], ignore_index=True).cumsum()
    drawdown = equity - equity.cummax()
    return float(drawdown.min())


def build_market_regime_report(
    *,
    regime_trades: pd.DataFrame,
    summary: pd.DataFrame,
    stress: pd.DataFrame,
    db_path: Path,
    oos_trades_path: Path,
    index_symbol: str,
) -> str:
    """Build Markdown report text."""

    bear = summary[(summary["summary_level"] == "by_bear_regime") & (summary["group"] == "True")]
    correction = summary[(summary["summary_level"] == "by_correction_regime") & (summary["group"] == "True")]
    bull = summary[(summary["summary_level"] == "by_bull_regime") & (summary["group"] == "True")]
    verdict = market_regime_verdict(stress, bear, correction, bull)
    lines = [
        "# Closed-Limit-Up Market Regime Diagnostic",
        "",
        f"Source OOS trades: `{oos_trades_path}`",
        f"Database: `{db_path}`",
        f"Index symbol: `{index_symbol.upper()}`",
        "",
        "## Executive Answer",
        "",
        verdict,
        "",
        "## Coverage",
        "",
        f"Best-strategy OOS trades after parameter filter: {len(regime_trades):,}",
        f"Trades with matching index data: {int(regime_trades['index_data_available'].sum()) if not regime_trades.empty else 0:,}",
        "",
        "## Stress Tests",
        "",
        markdown_table(stress),
        "",
        "## Regime Summary",
        "",
        markdown_table(summary),
        "",
        "## Interpretation",
        "",
        regime_interpretation(stress, bear, correction, bull),
        "",
    ]
    return "\n".join(lines)


def market_regime_verdict(stress: pd.DataFrame, bear: pd.DataFrame, correction: pd.DataFrame, bull: pd.DataFrame) -> str:
    if stress.empty:
        return "No market-regime rows were available."
    all_row = stress[stress["scenario"].eq("trade_all_regimes")]
    not_bear = stress[stress["scenario"].eq("trade_only_not_bear_regime")]
    avoid_weak = stress[stress["scenario"].eq("avoid_weak_market_day")]
    bull_only = stress[stress["scenario"].eq("trade_only_bull_regime")]
    all_pnl = float(all_row["total_net_pnl"].iloc[0]) if not all_row.empty else np.nan
    not_bear_pnl = float(not_bear["total_net_pnl"].iloc[0]) if not not_bear.empty else np.nan
    avoid_weak_pnl = float(avoid_weak["total_net_pnl"].iloc[0]) if not avoid_weak.empty else np.nan
    bull_count = int(bull_only["trades"].iloc[0]) if not bull_only.empty else 0
    bear_positive = (not bear.empty) and float(bear["total_net_pnl"].iloc[0]) > 0
    correction_positive = (not correction.empty) and float(correction["total_net_pnl"].iloc[0]) > 0
    if all_pnl > 0 and bear_positive and correction_positive:
        return (
            "Strict verdict: the edge remains positive outside clean bull regimes, including bearish/correction "
            "samples where available. A market-regime filter may improve comfort, but the daily evidence does "
            "not show the edge is merely a bull-market artifact."
        )
    if all_pnl > 0 and not_bear_pnl > 0 and avoid_weak_pnl > 0:
        return (
            "Strict verdict: the edge is positive overall and remains positive under simple defensive filters. "
            f"Bull-only has {bull_count} trades, so use it only if it does not kill too much sample size."
        )
    return (
        "Strict verdict: market-regime sensitivity is material. Do not add this to live candidates without "
        "a defensive regime filter and more index coverage."
    )


def regime_interpretation(stress: pd.DataFrame, bear: pd.DataFrame, correction: pd.DataFrame, bull: pd.DataFrame) -> str:
    parts = []
    if not bull.empty:
        parts.append(
            f"Bull-regime trades: {int(bull['trades'].iloc[0])}, "
            f"avg net {float(bull['avg_net_return'].iloc[0]):.3%}, "
            f"PnL {float(bull['total_net_pnl'].iloc[0]):,.0f} TWD."
        )
    if not bear.empty:
        parts.append(
            f"Bear-regime trades: {int(bear['trades'].iloc[0])}, "
            f"avg net {float(bear['avg_net_return'].iloc[0]):.3%}, "
            f"PnL {float(bear['total_net_pnl'].iloc[0]):,.0f} TWD."
        )
    if not correction.empty:
        parts.append(
            f"Correction-regime trades: {int(correction['trades'].iloc[0])}, "
            f"avg net {float(correction['avg_net_return'].iloc[0]):.3%}, "
            f"PnL {float(correction['total_net_pnl'].iloc[0]):,.0f} TWD."
        )
    if not parts:
        return "No interpretable bull/bear/correction rows were available."
    return " ".join(parts)


def bucket_return(values: pd.Series, *, short_horizon: bool) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    if short_horizon:
        bins = [-np.inf, -0.01, -0.005, 0.0, 0.005, 0.01, np.inf]
        labels = ["<=-1%", "-1%_-0.5%", "-0.5%_0", "0_0.5%", "0.5_1%", ">1%"]
    else:
        bins = [-np.inf, -0.05, -0.02, 0.0, 0.02, 0.05, np.inf]
        labels = ["<=-5%", "-5%_-2%", "-2%_0", "0_2%", "2_5%", ">5%"]
    return pd.cut(numeric, bins=bins, labels=labels).astype("string").fillna("missing")


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


def select_regime_trade_columns(frame: pd.DataFrame) -> pd.DataFrame:
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
        "net_return",
        "net_pnl",
        "buy_notional",
        "taiex_close",
        "taiex_day0_return",
        "taiex_5d_return",
        "taiex_20d_return",
        "taiex_close_above_ma20",
        "taiex_close_above_ma60",
        "taiex_drawdown_from_60d_high",
        "bull_regime",
        "bear_regime",
        "correction_regime",
        "strong_market_day",
        "weak_market_day",
        "taiex_day0_return_bucket",
        "taiex_5d_return_bucket",
        "taiex_20d_return_bucket",
        "year",
        "quarter",
        "index_data_available",
        "taiex_source",
    ]
    for column in columns:
        if column not in frame.columns:
            frame[column] = np.nan
    return frame[columns].sort_values(["signal_date", "symbol", "trade_id"]).reset_index(drop=True)


def empty_regime_trades() -> pd.DataFrame:
    return pd.DataFrame(columns=select_regime_trade_columns(pd.DataFrame()).columns)


def index_feature_columns() -> list[str]:
    return [
        "trade_date",
        "taiex_open",
        "taiex_high",
        "taiex_low",
        "taiex_close",
        "taiex_volume",
        "taiex_turnover_twd",
        "taiex_day0_return",
        "taiex_ma5",
        "taiex_ma20",
        "taiex_ma60",
        "taiex_close_above_ma20",
        "taiex_close_above_ma60",
        "taiex_drawdown_from_60d_high",
        "taiex_source",
        "taiex_5d_return",
        "taiex_20d_return",
    ]


def empty_index_features() -> pd.DataFrame:
    return pd.DataFrame(columns=index_feature_columns())


def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnose closed-limit-up overnight returns by TAIEX regime")
    parser.add_argument("--db", type=Path, default=get_settings().db_path)
    parser.add_argument(
        "--oos-trades",
        type=Path,
        default=get_settings().project_root / "reports" / "walk_forward_closed_limit_up_overnight_oos_trades.csv",
    )
    parser.add_argument("--output-dir", type=Path, default=get_settings().project_root / "reports")
    parser.add_argument("--index-symbol", default="TAIEX")
    args = parser.parse_args()

    try:
        trades, summary, stress, report_path = generate_market_regime_diagnostic(
            db_path=args.db,
            oos_trades_path=args.oos_trades,
            output_dir=args.output_dir,
            index_symbol=args.index_symbol,
        )
    except ValueError as exc:
        raise SystemExit(f"Error: {exc}") from None
    print(f"Wrote {len(trades)} rows to {args.output_dir / REGIME_TRADES_OUTPUT}")
    print(f"Wrote {len(summary)} rows to {args.output_dir / REGIME_SUMMARY_OUTPUT}")
    print(f"Wrote {len(stress)} rows to {args.output_dir / REGIME_STRESS_OUTPUT}")
    print(f"Wrote report to {report_path}")
    print("\nMarket-regime stress tests")
    print(stress.to_string(index=False))


if __name__ == "__main__":
    main()
