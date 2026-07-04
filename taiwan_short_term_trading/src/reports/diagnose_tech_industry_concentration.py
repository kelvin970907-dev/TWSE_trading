"""Technology/Electronics industry drill-down for closed-limit-up OOS trades."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from config.settings import get_settings
from src.reports.diagnose_sector_concentration import (
    calculate_metrics,
    markdown_table,
    top_symbols,
)


TECH_SECTOR = "Technology/Electronics"

SUMMARY_OUTPUT = "closed_limit_up_tech_industry_summary.csv"
CONCENTRATION_OUTPUT = "closed_limit_up_tech_industry_concentration.csv"
STRESS_OUTPUT = "closed_limit_up_tech_industry_stress_tests.csv"
REPORT_OUTPUT = "closed_limit_up_tech_industry_report.md"

SUMMARY_COLUMNS = [
    "industry",
    "trades",
    "trade_share_within_tech",
    "net_pnl",
    "pnl_share_within_tech",
    "avg_net_return",
    "median_net_return",
    "win_rate",
    "profit_factor",
    "max_drawdown",
    "top_10_symbols_by_trades",
    "top_10_symbols_by_pnl",
]

STRESS_COLUMNS = [
    "scenario",
    "trades",
    "trade_share_within_tech",
    "net_pnl",
    "pnl_share_within_tech",
    "avg_net_return",
    "median_net_return",
    "win_rate",
    "profit_factor",
    "max_drawdown",
]


def generate_tech_industry_concentration_report(
    *,
    sector_diagnostic_path: Path | str,
    output_dir: Path | str | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, Path]:
    """Generate Technology/Electronics industry concentration reports."""

    report_dir = Path(output_dir) if output_dir is not None else get_settings().project_root / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    tech_trades = load_tech_trades(sector_diagnostic_path)
    summary = summarize_tech_industries(tech_trades)
    concentration = calculate_tech_industry_concentration(summary)
    stress = run_tech_industry_stress_tests(tech_trades, summary)
    report_text = build_tech_industry_report(
        tech_trades=tech_trades,
        summary=summary,
        concentration=concentration,
        stress=stress,
        source_path=Path(sector_diagnostic_path),
    )

    summary.to_csv(report_dir / SUMMARY_OUTPUT, index=False)
    concentration.to_csv(report_dir / CONCENTRATION_OUTPUT, index=False)
    stress.to_csv(report_dir / STRESS_OUTPUT, index=False)
    report_path = report_dir / REPORT_OUTPUT
    report_path.write_text(report_text, encoding="utf-8")
    return summary, concentration, stress, report_path


def load_tech_trades(sector_diagnostic_path: Path | str) -> pd.DataFrame:
    """Load sector diagnostic rows and isolate Technology/Electronics trades."""

    path = Path(sector_diagnostic_path)
    if not path.exists():
        raise FileNotFoundError(f"Sector diagnostic CSV not found: {path}")
    frame = pd.read_csv(path)
    required = {"sector", "industry", "symbol", "net_return", "net_pnl"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"Sector diagnostic CSV is missing required columns: {missing}")
    frame["sector"] = frame["sector"].astype("string").str.strip()
    frame["industry"] = frame["industry"].astype("string").fillna("").str.strip()
    frame["industry"] = frame["industry"].mask(frame["industry"].str.len() == 0, "MISSING_INDUSTRY")
    tech = frame[frame["sector"].eq(TECH_SECTOR)].copy().reset_index(drop=True)
    for column in ["signal_date", "entry_date", "exit_date"]:
        if column in tech.columns:
            tech[column] = pd.to_datetime(tech[column], errors="coerce").dt.normalize()
    return tech


def summarize_tech_industries(tech_trades: pd.DataFrame) -> pd.DataFrame:
    """Summarize Technology/Electronics trades by industry."""

    if tech_trades.empty:
        return pd.DataFrame(columns=SUMMARY_COLUMNS)
    total_trades = len(tech_trades)
    total_pnl = pd.to_numeric(tech_trades["net_pnl"], errors="coerce").fillna(0.0).sum()
    rows: list[dict[str, Any]] = []
    for industry, group in tech_trades.groupby("industry", dropna=False, observed=False):
        metrics = calculate_metrics(group)
        rows.append(
            {
                "industry": str(industry),
                "trades": metrics["trades"],
                "trade_share_within_tech": metrics["trades"] / total_trades if total_trades else np.nan,
                "net_pnl": metrics["total_net_pnl"],
                "pnl_share_within_tech": metrics["total_net_pnl"] / total_pnl if total_pnl else np.nan,
                "avg_net_return": metrics["avg_net_return"],
                "median_net_return": metrics["median_net_return"],
                "win_rate": metrics["win_rate"],
                "profit_factor": metrics["profit_factor"],
                "max_drawdown": metrics["max_drawdown"],
                "top_10_symbols_by_trades": top_symbols(group, sort_by="trades", limit=10),
                "top_10_symbols_by_pnl": top_symbols(group, sort_by="pnl", limit=10),
            }
        )
    return pd.DataFrame(rows, columns=SUMMARY_COLUMNS).sort_values(
        ["net_pnl", "trades"],
        ascending=[False, False],
    ).reset_index(drop=True)


def calculate_tech_industry_concentration(summary: pd.DataFrame) -> pd.DataFrame:
    """Compute industry concentration within Technology/Electronics."""

    columns = [
        "sector",
        "industries",
        "trades",
        "net_pnl",
        "top_1_industry_trade_share",
        "top_3_industry_trade_share",
        "top_5_industry_trade_share",
        "top_1_industry_pnl_share",
        "top_3_industry_pnl_share",
        "top_5_industry_pnl_share",
        "hhi_by_trades",
        "hhi_by_pnl",
    ]
    if summary.empty:
        return pd.DataFrame(columns=columns)
    trades = pd.to_numeric(summary["trades"], errors="coerce").fillna(0.0)
    pnl = pd.to_numeric(summary["net_pnl"], errors="coerce").fillna(0.0)
    total_trades = trades.sum()
    total_pnl = pnl.sum()
    trade_shares = trades / total_trades if total_trades else trades * np.nan
    abs_pnl = pnl.abs()
    abs_pnl_shares = abs_pnl / abs_pnl.sum() if abs_pnl.sum() else abs_pnl * np.nan
    by_trade = summary.assign(_share=trade_shares).sort_values("_share", ascending=False)
    by_pnl = summary.assign(_pnl=pnl).sort_values("_pnl", ascending=False)
    row = {
        "sector": TECH_SECTOR,
        "industries": int(summary["industry"].nunique(dropna=False)),
        "trades": int(total_trades),
        "net_pnl": float(total_pnl),
        "top_1_industry_trade_share": top_n_sum(by_trade["_share"], 1),
        "top_3_industry_trade_share": top_n_sum(by_trade["_share"], 3),
        "top_5_industry_trade_share": top_n_sum(by_trade["_share"], 5),
        "top_1_industry_pnl_share": top_n_pnl_share(by_pnl["_pnl"], total_pnl, 1),
        "top_3_industry_pnl_share": top_n_pnl_share(by_pnl["_pnl"], total_pnl, 3),
        "top_5_industry_pnl_share": top_n_pnl_share(by_pnl["_pnl"], total_pnl, 5),
        "hhi_by_trades": float((trade_shares**2).sum()),
        "hhi_by_pnl": float((abs_pnl_shares**2).sum()),
    }
    return pd.DataFrame([row], columns=columns)


def run_tech_industry_stress_tests(tech_trades: pd.DataFrame, summary: pd.DataFrame) -> pd.DataFrame:
    """Run requested industry stress tests inside Technology/Electronics."""

    if tech_trades.empty:
        return pd.DataFrame(columns=STRESS_COLUMNS)
    top_by_pnl = summary.sort_values("net_pnl", ascending=False)["industry"].tolist()
    top_by_trades = summary.sort_values(["trades", "net_pnl"], ascending=[False, False])["industry"].tolist()
    min_30 = summary.loc[summary["trades"] >= 30, "industry"].tolist()
    positive_median = summary.loc[summary["median_net_return"] > 0, "industry"].tolist()
    nonnegative_median = summary.loc[summary["median_net_return"] >= 0, "industry"].tolist()
    pf_gt_1_5 = summary.loc[summary["profit_factor"] > 1.5, "industry"].tolist()
    pf_ge_1_2 = summary.loc[summary["profit_factor"] >= 1.2, "industry"].tolist()
    scenarios = {
        "trade_all_tech": pd.Series(True, index=tech_trades.index),
        "remove_top_1_industry_by_pnl": ~tech_trades["industry"].isin(top_by_pnl[:1]),
        "remove_top_3_industries_by_pnl": ~tech_trades["industry"].isin(top_by_pnl[:3]),
        "remove_top_1_industry_by_trade_count": ~tech_trades["industry"].isin(top_by_trades[:1]),
        "remove_top_3_industries_by_trade_count": ~tech_trades["industry"].isin(top_by_trades[:3]),
        "industries_with_at_least_30_oos_trades": tech_trades["industry"].isin(min_30),
        "industries_with_positive_median_net_return": tech_trades["industry"].isin(positive_median),
        "industries_with_profit_factor_gt_1_5": tech_trades["industry"].isin(pf_gt_1_5),
        "avoid_negative_median_net_return": tech_trades["industry"].isin(nonnegative_median),
        "avoid_profit_factor_lt_1_2": tech_trades["industry"].isin(pf_ge_1_2),
    }
    total_trades = len(tech_trades)
    total_pnl = pd.to_numeric(tech_trades["net_pnl"], errors="coerce").fillna(0.0).sum()
    rows: list[dict[str, Any]] = []
    for scenario, mask in scenarios.items():
        group = tech_trades[mask.fillna(False)]
        metrics = calculate_metrics(group)
        rows.append(
            {
                "scenario": scenario,
                "trades": metrics["trades"],
                "trade_share_within_tech": metrics["trades"] / total_trades if total_trades else np.nan,
                "net_pnl": metrics["total_net_pnl"],
                "pnl_share_within_tech": metrics["total_net_pnl"] / total_pnl if total_pnl else np.nan,
                "avg_net_return": metrics["avg_net_return"],
                "median_net_return": metrics["median_net_return"],
                "win_rate": metrics["win_rate"],
                "profit_factor": metrics["profit_factor"],
                "max_drawdown": metrics["max_drawdown"],
            }
        )
    return pd.DataFrame(rows, columns=STRESS_COLUMNS)


def top_n_sum(values: pd.Series, n: int) -> float:
    return float(pd.to_numeric(values, errors="coerce").fillna(0.0).head(n).sum())


def top_n_pnl_share(values: pd.Series, total_pnl: float, n: int) -> float:
    if not total_pnl:
        return np.nan
    return float(pd.to_numeric(values, errors="coerce").fillna(0.0).head(n).sum() / total_pnl)


def build_tech_industry_report(
    *,
    tech_trades: pd.DataFrame,
    summary: pd.DataFrame,
    concentration: pd.DataFrame,
    stress: pd.DataFrame,
    source_path: Path,
) -> str:
    """Build Markdown report text."""

    verdict = tech_industry_verdict(concentration, stress)
    lines = [
        "# Closed-Limit-Up Technology/Electronics Industry Drill-Down",
        "",
        f"Source diagnostic CSV: `{source_path}`",
        "",
        "## Executive Answer",
        "",
        verdict,
        "",
        "## Coverage",
        "",
        f"Technology/Electronics OOS trades: {len(tech_trades):,}",
        f"Industries: {summary['industry'].nunique(dropna=False) if not summary.empty else 0:,}",
        "",
        "## Industry Concentration",
        "",
        markdown_table(concentration),
        "",
        "## Stress Tests",
        "",
        markdown_table(stress),
        "",
        "## Industries By Net PnL",
        "",
        markdown_table(summary),
        "",
        "## Note",
        "",
        "`hhi_by_pnl` uses absolute PnL shares so weak negative industries are still counted as concentration.",
        "",
    ]
    return "\n".join(lines)


def tech_industry_verdict(concentration: pd.DataFrame, stress: pd.DataFrame) -> str:
    if concentration.empty or stress.empty:
        return "No Technology/Electronics trades were available."
    remove_top_1 = stress[stress["scenario"].eq("remove_top_1_industry_by_pnl")]
    remove_top_3 = stress[stress["scenario"].eq("remove_top_3_industries_by_pnl")]
    top1_pnl = float(remove_top_1["net_pnl"].iloc[0]) if not remove_top_1.empty else np.nan
    top3_pnl = float(remove_top_3["net_pnl"].iloc[0]) if not remove_top_3.empty else np.nan
    top1_trade_share = float(concentration["top_1_industry_trade_share"].iloc[0])
    hhi_trades = float(concentration["hhi_by_trades"].iloc[0])
    if top1_pnl > 0 and top3_pnl > 0 and top1_trade_share < 0.35 and hhi_trades < 0.18:
        return (
            "Strict verdict: Technology/Electronics is internally diversified across several industries. "
            "Removing the leading industry or top three industries still leaves positive OOS PnL."
        )
    if top1_pnl > 0 and top3_pnl > 0:
        return (
            "Strict verdict: the edge survives removing the top industry and top three industries, but "
            "trade/PnL concentration is meaningful. Use industry caps, not a single hard industry bet."
        )
    return (
        "Strict verdict: Technology/Electronics is too dependent on the leading industries. Treat this as "
        "theme-concentrated until more granular execution and order-book data confirms breadth."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Drill into Technology/Electronics industry concentration")
    parser.add_argument(
        "--sector-diagnostic",
        type=Path,
        default=get_settings().project_root / "reports" / "closed_limit_up_sector_diagnostic_trades.csv",
    )
    parser.add_argument("--output-dir", type=Path, default=get_settings().project_root / "reports")
    args = parser.parse_args()

    summary, concentration, stress, report_path = generate_tech_industry_concentration_report(
        sector_diagnostic_path=args.sector_diagnostic,
        output_dir=args.output_dir,
    )
    print(f"Wrote {len(summary)} rows to {args.output_dir / SUMMARY_OUTPUT}")
    print(f"Wrote {len(concentration)} rows to {args.output_dir / CONCENTRATION_OUTPUT}")
    print(f"Wrote {len(stress)} rows to {args.output_dir / STRESS_OUTPUT}")
    print(f"Wrote report to {report_path}")
    print("\nTechnology/Electronics industry stress tests")
    print(stress.to_string(index=False))


if __name__ == "__main__":
    main()
