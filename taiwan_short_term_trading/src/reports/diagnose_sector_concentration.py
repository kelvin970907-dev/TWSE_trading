"""Sector and industry concentration diagnostics for closed-limit-up overnight OOS trades."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from config.settings import get_settings
from src.db import get_connection, init_db
from src.reports.diagnose_closed_limit_up_execution import load_and_filter_oos_trades


DIAGNOSTIC_OUTPUT = "closed_limit_up_sector_diagnostic_trades.csv"
SECTOR_SUMMARY_OUTPUT = "closed_limit_up_sector_summary.csv"
INDUSTRY_SUMMARY_OUTPUT = "closed_limit_up_industry_summary.csv"
CONCENTRATION_OUTPUT = "closed_limit_up_sector_concentration.csv"
STRESS_OUTPUT = "closed_limit_up_sector_stress_tests.csv"
REPORT_OUTPUT = "closed_limit_up_sector_report.md"

SUMMARY_METRIC_COLUMNS = [
    "trades",
    "avg_net_return",
    "median_net_return",
    "win_rate",
    "profit_factor",
    "net_pnl",
    "max_drawdown",
    "trade_share",
    "pnl_share",
    "top_symbols_by_trades",
    "top_symbols_by_pnl",
]

STRESS_COLUMNS = [
    "scenario",
    "trades",
    "trade_share",
    "avg_net_return",
    "median_net_return",
    "win_rate",
    "profit_factor",
    "net_pnl",
    "pnl_share",
    "max_drawdown",
]


def generate_sector_concentration_diagnostic(
    *,
    db_path: Path | str,
    oos_trades_path: Path | str,
    output_dir: Path | str | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, Path]:
    """Generate sector/industry concentration diagnostic files."""

    init_db(db_path)
    report_dir = Path(output_dir) if output_dir is not None else get_settings().project_root / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)

    trades = load_and_filter_oos_trades(oos_trades_path)
    diagnostic = join_sector_context(trades, db_path=db_path)
    sector_summary = summarize_by_group(diagnostic, group_column="sector", output_group_name="sector")
    industry_summary = summarize_by_group(diagnostic, group_column="industry", output_group_name="industry")
    concentration = calculate_concentration_diagnostics(sector_summary, industry_summary)
    stress_tests = run_sector_concentration_stress_tests(diagnostic, sector_summary, industry_summary)
    report_text = build_sector_report(
        diagnostic=diagnostic,
        sector_summary=sector_summary,
        industry_summary=industry_summary,
        concentration=concentration,
        stress_tests=stress_tests,
        db_path=Path(db_path),
        oos_trades_path=Path(oos_trades_path),
    )

    diagnostic.to_csv(report_dir / DIAGNOSTIC_OUTPUT, index=False)
    sector_summary.to_csv(report_dir / SECTOR_SUMMARY_OUTPUT, index=False)
    industry_summary.to_csv(report_dir / INDUSTRY_SUMMARY_OUTPUT, index=False)
    concentration.to_csv(report_dir / CONCENTRATION_OUTPUT, index=False)
    stress_tests.to_csv(report_dir / STRESS_OUTPUT, index=False)
    report_path = report_dir / REPORT_OUTPUT
    report_path.write_text(report_text, encoding="utf-8")
    return diagnostic, sector_summary, industry_summary, concentration, stress_tests, report_path


def join_sector_context(trades: pd.DataFrame, *, db_path: Path | str) -> pd.DataFrame:
    """Join filtered OOS trades to sector map and daily-price names."""

    if trades.empty:
        return empty_diagnostic_trades()

    frame = trades.copy()
    frame["symbol"] = frame["symbol"].astype("string").str.strip()
    frame["market"] = frame["market"].astype("string").str.upper().str.strip()
    frame["signal_date"] = pd.to_datetime(frame["signal_date"], errors="coerce").dt.normalize()
    keys = frame[["_diagnostic_row_id", "symbol", "market", "signal_date"]].copy()
    keys["signal_date"] = pd.to_datetime(keys["signal_date"], errors="coerce").dt.date

    with get_connection(db_path, read_only=True) as conn:
        conn.register("_sector_keys", keys)
        context = conn.execute(
            """
            SELECT
                k._diagnostic_row_id,
                dp.name AS daily_price_name,
                m.name AS mapped_name,
                m.sector,
                m.industry,
                m.source AS sector_source
            FROM _sector_keys k
            LEFT JOIN daily_prices dp
              ON dp.symbol = k.symbol
             AND dp.market = k.market
             AND dp.trade_date = k.signal_date
            LEFT JOIN stock_sector_map m
              ON m.symbol = k.symbol
             AND m.market = k.market
            """
        ).fetch_df()

    joined = frame.merge(context, on="_diagnostic_row_id", how="left")
    joined["name"] = coalesce_text_columns(joined, ["mapped_name", "daily_price_name", "name", "symbol"])
    joined["sector_missing"] = text_is_blank(joined.get("sector"))
    joined["industry_missing"] = text_is_blank(joined.get("industry"))
    joined["sector"] = clean_group_values(joined.get("sector"), missing_label="MISSING_SECTOR")
    joined["industry"] = clean_group_values(joined.get("industry"), missing_label="MISSING_INDUSTRY")
    joined["sector_source"] = clean_group_values(joined.get("sector_source"), missing_label="")
    for column in ["signal_date", "entry_date", "exit_date"]:
        if column in joined.columns:
            joined[column] = pd.to_datetime(joined[column], errors="coerce").dt.normalize()
    return select_diagnostic_columns(joined)


def summarize_by_group(frame: pd.DataFrame, *, group_column: str, output_group_name: str) -> pd.DataFrame:
    """Summarize trade performance by sector or industry."""

    columns = [output_group_name, *SUMMARY_METRIC_COLUMNS]
    if frame.empty:
        return pd.DataFrame(columns=columns)
    total_trades = len(frame)
    total_pnl = pd.to_numeric(frame["net_pnl"], errors="coerce").fillna(0.0).sum()
    rows: list[dict[str, Any]] = []
    for group_name, group in frame.groupby(group_column, dropna=False, observed=False):
        metrics = calculate_metrics(group)
        rows.append(
            {
                output_group_name: str(group_name),
                **metrics,
                "net_pnl": metrics["total_net_pnl"],
                "trade_share": metrics["trades"] / total_trades if total_trades else np.nan,
                "pnl_share": metrics["total_net_pnl"] / total_pnl if total_pnl else np.nan,
                "top_symbols_by_trades": top_symbols(group, sort_by="trades"),
                "top_symbols_by_pnl": top_symbols(group, sort_by="pnl"),
            }
        )
    output = pd.DataFrame(rows)
    if output.empty:
        return pd.DataFrame(columns=columns)
    output = output.drop(columns=["total_net_pnl"], errors="ignore")
    return output[columns].sort_values(["net_pnl", "trades"], ascending=[False, False]).reset_index(drop=True)


def calculate_concentration_diagnostics(
    sector_summary: pd.DataFrame,
    industry_summary: pd.DataFrame,
) -> pd.DataFrame:
    """Compute sector and industry concentration diagnostics."""

    rows = [
        concentration_row(sector_summary, level="sector", group_column="sector"),
        concentration_row(industry_summary, level="industry", group_column="industry"),
    ]
    return pd.DataFrame(rows)


def concentration_row(summary: pd.DataFrame, *, level: str, group_column: str) -> dict[str, float | int | str]:
    if summary.empty:
        return {
            "level": level,
            "groups": 0,
            "top_1_trade_share": np.nan,
            "top_3_trade_share": np.nan,
            "top_5_trade_share": np.nan,
            "top_1_pnl_share": np.nan,
            "top_3_pnl_share": np.nan,
            "top_5_pnl_share": np.nan,
            "hhi_by_trades": np.nan,
            "hhi_by_abs_pnl": np.nan,
        }
    trades = pd.to_numeric(summary["trades"], errors="coerce").fillna(0.0)
    pnl = pd.to_numeric(summary["net_pnl"], errors="coerce").fillna(0.0)
    trade_shares = trades / trades.sum() if trades.sum() else trades * np.nan
    abs_pnl = pnl.abs()
    abs_pnl_shares = abs_pnl / abs_pnl.sum() if abs_pnl.sum() else abs_pnl * np.nan
    by_trades = summary.assign(_share=trade_shares).sort_values("_share", ascending=False)
    by_pnl = summary.assign(_pnl=pnl).sort_values("_pnl", ascending=False)
    total_pnl = pnl.sum()
    return {
        "level": level,
        "groups": int(summary[group_column].nunique(dropna=False)),
        "top_1_trade_share": top_n_share(by_trades["_share"], 1),
        "top_3_trade_share": top_n_share(by_trades["_share"], 3),
        "top_5_trade_share": top_n_share(by_trades["_share"], 5),
        "top_1_pnl_share": top_n_pnl_share(by_pnl["_pnl"], total_pnl, 1),
        "top_3_pnl_share": top_n_pnl_share(by_pnl["_pnl"], total_pnl, 3),
        "top_5_pnl_share": top_n_pnl_share(by_pnl["_pnl"], total_pnl, 5),
        "hhi_by_trades": float((trade_shares**2).sum()),
        "hhi_by_abs_pnl": float((abs_pnl_shares**2).sum()),
    }


def run_sector_concentration_stress_tests(
    diagnostic: pd.DataFrame,
    sector_summary: pd.DataFrame,
    industry_summary: pd.DataFrame,
) -> pd.DataFrame:
    """Run requested concentration stress tests."""

    if diagnostic.empty:
        return pd.DataFrame(columns=STRESS_COLUMNS)
    top_sectors = sector_summary.sort_values("net_pnl", ascending=False)["sector"].tolist()
    top_industries = industry_summary.sort_values("net_pnl", ascending=False)["industry"].tolist()
    eligible_trade_count_sectors = sector_summary.loc[sector_summary["trades"] >= 30, "sector"].tolist()
    positive_median_sectors = sector_summary.loc[sector_summary["median_net_return"] > 0, "sector"].tolist()
    high_pf_sectors = sector_summary.loc[sector_summary["profit_factor"] > 1.5, "sector"].tolist()
    scenarios = {
        "trade_all": pd.Series(True, index=diagnostic.index),
        "remove_top_1_sector_by_pnl": ~diagnostic["sector"].isin(top_sectors[:1]),
        "remove_top_3_sectors_by_pnl": ~diagnostic["sector"].isin(top_sectors[:3]),
        "remove_top_1_industry_by_pnl": ~diagnostic["industry"].isin(top_industries[:1]),
        "remove_top_3_industries_by_pnl": ~diagnostic["industry"].isin(top_industries[:3]),
        "sectors_with_at_least_30_oos_trades": diagnostic["sector"].isin(eligible_trade_count_sectors),
        "sectors_with_positive_median_net_return": diagnostic["sector"].isin(positive_median_sectors),
        "sectors_with_profit_factor_gt_1_5": diagnostic["sector"].isin(high_pf_sectors),
    }
    total_trades = len(diagnostic)
    total_pnl = pd.to_numeric(diagnostic["net_pnl"], errors="coerce").fillna(0.0).sum()
    rows: list[dict[str, Any]] = []
    for scenario, mask in scenarios.items():
        group = diagnostic[mask.fillna(False)]
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
                "net_pnl": metrics["total_net_pnl"],
                "pnl_share": metrics["total_net_pnl"] / total_pnl if total_pnl else np.nan,
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
    if "exit_date" in dated.columns:
        date_values = dated["exit_date"]
    else:
        date_values = dated["entry_date"]
    dated["_pnl_date"] = pd.to_datetime(date_values, errors="coerce").dt.normalize()
    daily = dated.groupby("_pnl_date", dropna=False)["net_pnl"].sum().sort_index()
    equity = pd.concat([pd.Series([0.0]), daily.reset_index(drop=True)], ignore_index=True).cumsum()
    drawdown = equity - equity.cummax()
    return float(drawdown.min())


def top_symbols(frame: pd.DataFrame, *, sort_by: str, limit: int = 5) -> str:
    if frame.empty:
        return ""
    grouped = (
        frame.assign(display_name=coalesce_text_columns(frame, ["name", "symbol"]))
        .groupby(["symbol", "display_name"], dropna=False)
        .agg(trades=("symbol", "size"), pnl=("net_pnl", "sum"))
        .reset_index()
    )
    if sort_by == "trades":
        grouped = grouped.sort_values(["trades", "pnl"], ascending=[False, False])
    else:
        grouped = grouped.sort_values(["pnl", "trades"], ascending=[False, False])
    parts = []
    for _, row in grouped.head(limit).iterrows():
        if sort_by == "trades":
            parts.append(f"{row['symbol']} {row['display_name']} ({int(row['trades'])})")
        else:
            parts.append(f"{row['symbol']} {row['display_name']} ({float(row['pnl']):,.0f})")
    return "; ".join(parts)


def top_n_share(values: pd.Series, n: int) -> float:
    numeric = pd.to_numeric(values, errors="coerce").fillna(0.0)
    return float(numeric.head(n).sum())


def top_n_pnl_share(values: pd.Series, total_pnl: float, n: int) -> float:
    if not total_pnl:
        return np.nan
    numeric = pd.to_numeric(values, errors="coerce").fillna(0.0)
    return float(numeric.head(n).sum() / total_pnl)


def coalesce_text_columns(frame: pd.DataFrame, columns: list[str]) -> pd.Series:
    result = pd.Series("", index=frame.index, dtype="string")
    for column in columns:
        if column not in frame.columns:
            continue
        values = frame[column].astype("string").fillna("").str.strip()
        result = result.mask(result.str.len() == 0, values)
    return result.fillna("").astype("string")


def text_is_blank(values: pd.Series | None) -> pd.Series:
    if values is None:
        return pd.Series(True)
    return values.astype("string").fillna("").str.strip().str.len() == 0


def clean_group_values(values: pd.Series | None, *, missing_label: str) -> pd.Series:
    if values is None:
        return pd.Series([missing_label], dtype="string")
    cleaned = values.astype("string").fillna("").str.strip()
    return cleaned.mask(cleaned.str.len() == 0, missing_label).astype("string")


def select_diagnostic_columns(frame: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "window_id",
        "selected_rank",
        "config_hash",
        "trade_id",
        "event_id",
        "symbol",
        "market",
        "name",
        "signal_date",
        "entry_date",
        "exit_date",
        "net_return",
        "net_pnl",
        "buy_notional",
        "sector",
        "industry",
        "sector_missing",
        "industry_missing",
        "sector_source",
        "day0_close",
        "fill_quality_score",
        "day0_turnover_twd",
        "volume_ratio_20d",
        "consecutive_limit_up_count",
    ]
    for column in columns:
        if column not in frame.columns:
            frame[column] = np.nan
    return frame[columns].sort_values(["signal_date", "symbol", "trade_id"]).reset_index(drop=True)


def empty_diagnostic_trades() -> pd.DataFrame:
    return pd.DataFrame(columns=select_diagnostic_columns(pd.DataFrame()).columns)


def build_sector_report(
    *,
    diagnostic: pd.DataFrame,
    sector_summary: pd.DataFrame,
    industry_summary: pd.DataFrame,
    concentration: pd.DataFrame,
    stress_tests: pd.DataFrame,
    db_path: Path,
    oos_trades_path: Path,
) -> str:
    missing_symbols = diagnostic.loc[diagnostic["sector_missing"].astype(bool), "symbol"].nunique() if not diagnostic.empty else 0
    missing_trades = int(diagnostic["sector_missing"].sum()) if not diagnostic.empty else 0
    verdict = sector_verdict(diagnostic, concentration, stress_tests)
    lines = [
        "# Closed-Limit-Up Sector Concentration Diagnostic",
        "",
        f"Source OOS trades: `{oos_trades_path}`",
        f"Database: `{db_path}`",
        "",
        "## Executive Answer",
        "",
        verdict,
        "",
        "## Mapping Coverage",
        "",
        f"Best-strategy OOS trades: {len(diagnostic):,}",
        f"Trades missing sector map: {missing_trades:,}",
        f"Unique symbols missing sector map: {missing_symbols:,}",
        "",
        "## Concentration",
        "",
        markdown_table(concentration),
        "",
        "## Stress Tests",
        "",
        markdown_table(stress_tests),
        "",
        "## Top Sectors By PnL",
        "",
        markdown_table(sector_summary.head(15)),
        "",
        "## Top Industries By PnL",
        "",
        markdown_table(industry_summary.head(20)),
        "",
        "## Data Quality Notes",
        "",
        data_quality_notes(diagnostic),
        "",
    ]
    return "\n".join(lines)


def sector_verdict(diagnostic: pd.DataFrame, concentration: pd.DataFrame, stress_tests: pd.DataFrame) -> str:
    if diagnostic.empty:
        return "No best-strategy OOS trades were available."
    missing_share = float(diagnostic["sector_missing"].mean())
    if missing_share > 0.5:
        return (
            "Strict verdict: sector concentration cannot be trusted yet because most trades lack sector mapping. "
            "Load a complete TPEx sector map before using sector filters."
        )
    trade_all = stress_tests[stress_tests["scenario"].eq("trade_all")]
    remove_top_1 = stress_tests[stress_tests["scenario"].eq("remove_top_1_sector_by_pnl")]
    remove_top_3 = stress_tests[stress_tests["scenario"].eq("remove_top_3_sectors_by_pnl")]
    all_pnl = float(trade_all["net_pnl"].iloc[0]) if not trade_all.empty else np.nan
    top1_pnl = float(remove_top_1["net_pnl"].iloc[0]) if not remove_top_1.empty else np.nan
    top3_pnl = float(remove_top_3["net_pnl"].iloc[0]) if not remove_top_3.empty else np.nan
    sector_hhi = concentration.loc[concentration["level"].eq("sector"), "hhi_by_trades"]
    hhi = float(sector_hhi.iloc[0]) if not sector_hhi.empty else np.nan
    if all_pnl > 0 and top1_pnl > 0 and top3_pnl > 0 and hhi < 0.25:
        return (
            "Strict verdict: the edge is not obviously dependent on one sector. Removing the top sector and "
            "top three sectors by PnL leaves positive OOS PnL, and trade-count concentration is not extreme."
        )
    if all_pnl > 0 and top1_pnl > 0:
        return (
            "Strict verdict: the edge survives removing the top sector, but top-three sector dependence or "
            "concentration is material. Use sector exposure caps rather than hard filters."
        )
    return (
        "Strict verdict: the sector concentration risk is too high. Do not treat this as broad until it "
        "survives removing the leading sector themes."
    )


def data_quality_notes(diagnostic: pd.DataFrame) -> str:
    if diagnostic.empty:
        return "No diagnostic rows were generated."
    missing_symbols = sorted(diagnostic.loc[diagnostic["sector_missing"].astype(bool), "symbol"].astype(str).unique())
    if not missing_symbols:
        return "All diagnostic trades had sector mappings."
    sample = ", ".join(missing_symbols[:25])
    suffix = "..." if len(missing_symbols) > 25 else ""
    return (
        f"{len(missing_symbols):,} unique symbol(s) lack sector mapping. Missing symbol sample: "
        f"{sample}{suffix}"
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnose sector concentration for closed-limit-up OOS trades")
    parser.add_argument("--db", type=Path, default=get_settings().db_path)
    parser.add_argument(
        "--oos-trades",
        type=Path,
        default=get_settings().project_root / "reports" / "walk_forward_closed_limit_up_overnight_oos_trades.csv",
    )
    parser.add_argument("--output-dir", type=Path, default=get_settings().project_root / "reports")
    args = parser.parse_args()

    diagnostic, sector_summary, industry_summary, concentration, stress_tests, report_path = (
        generate_sector_concentration_diagnostic(
            db_path=args.db,
            oos_trades_path=args.oos_trades,
            output_dir=args.output_dir,
        )
    )
    print(f"Wrote {len(diagnostic)} rows to {args.output_dir / DIAGNOSTIC_OUTPUT}")
    print(f"Wrote {len(sector_summary)} rows to {args.output_dir / SECTOR_SUMMARY_OUTPUT}")
    print(f"Wrote {len(industry_summary)} rows to {args.output_dir / INDUSTRY_SUMMARY_OUTPUT}")
    print(f"Wrote {len(concentration)} rows to {args.output_dir / CONCENTRATION_OUTPUT}")
    print(f"Wrote {len(stress_tests)} rows to {args.output_dir / STRESS_OUTPUT}")
    print(f"Wrote report to {report_path}")
    print("\nSector concentration stress tests")
    print(stress_tests.to_string(index=False))


if __name__ == "__main__":
    main()
