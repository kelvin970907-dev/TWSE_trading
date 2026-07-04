"""Full audit for focused closed-limit-up challenger candidates."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from config.settings import get_settings
from src.backtests.focused_challenger_expansion import (
    FocusedGridConfig,
    champion_to_focused_config,
    evaluate_focused_strategy,
    focused_config_hash,
    period_returns,
    to_strategy_config,
)
from src.backtests.strategy_tournament import (
    StrategyResult,
    combine_with_champion,
    config_to_row,
    ensure_event_candidates,
    infer_tournament_dates,
    load_tournament_universe,
)
from src.db import get_connection
from src.reports.diagnose_closed_limit_up_execution import (
    compute_execution_features,
    enrich_with_database_context,
    run_execution_stress_tests,
    run_fill_haircut_monte_carlo,
    summarize_execution_buckets,
)
from src.reports.diagnose_market_regime import (
    join_taiex_regime_features,
    run_market_regime_stress_tests,
    summarize_market_regimes,
)
from src.reports.diagnose_sector_concentration import (
    calculate_concentration_diagnostics,
    calculate_metrics,
    markdown_table,
    run_sector_concentration_stress_tests,
    summarize_by_group,
)
from src.reports.diagnose_symbol_dependency import (
    add_repeat_event_features,
    calculate_symbol_concentration,
    run_cooldown_stress_tests,
    run_remove_top_symbol_tests,
    summarize_hotness_buckets,
    summarize_symbols,
)


EXACT_RESULTS_DEFAULT = "focused_challenger_exact_results.csv"
EXACT_TOP_DEFAULT = "focused_challenger_exact_top.csv"
EXACT_COMBINED_DEFAULT = "focused_challenger_exact_combined.csv"


@dataclass
class CandidateAudit:
    """Audit artifacts for one challenger candidate."""

    candidate_hash: str
    config: FocusedGridConfig
    result: StrategyResult
    trades: pd.DataFrame
    summary: pd.DataFrame
    fill: pd.DataFrame
    sector: pd.DataFrame
    symbol: pd.DataFrame
    regime: pd.DataFrame
    report_path: Path
    decision: str
    biggest_unresolved_risk: str


def audit_challenger_candidates(
    *,
    db_path: Path | str,
    candidate_hashes: list[str],
    output_dir: Path | str | None = None,
    exact_results_path: Path | str | None = None,
    exact_top_path: Path | str | None = None,
    exact_combined_path: Path | str | None = None,
    monte_carlo_iterations: int = 1000,
    random_seed: int = 42,
) -> tuple[list[CandidateAudit], pd.DataFrame, Path]:
    """Audit focused challenger candidates and write all report files."""

    if not candidate_hashes:
        raise ValueError("candidate_hashes must contain at least one hash")
    if monte_carlo_iterations <= 0:
        raise ValueError("monte_carlo_iterations must be positive")

    report_dir = Path(output_dir) if output_dir is not None else get_settings().project_root / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    exact_results_file = Path(exact_results_path) if exact_results_path is not None else report_dir / EXACT_RESULTS_DEFAULT
    exact_top_file = Path(exact_top_path) if exact_top_path is not None else report_dir / EXACT_TOP_DEFAULT
    exact_combined_file = Path(exact_combined_path) if exact_combined_path is not None else report_dir / EXACT_COMBINED_DEFAULT

    exact_results = read_csv_required(exact_results_file, label="focused exact results")
    exact_top = read_csv_optional(exact_top_file)
    exact_combined = read_csv_optional(exact_combined_file)
    config_by_hash = load_candidate_configs(exact_results, candidate_hashes)

    start_ts, end_ts = infer_tournament_dates(db_path=db_path, start=None, end=None, output_dir=report_dir)
    ensure_event_candidates(db_path=db_path, start=start_ts, end=end_ts, rebuild_events=False)
    universe = load_tournament_universe(db_path=db_path, start=start_ts, end=end_ts)
    champion = evaluate_focused_strategy(champion_to_focused_config(), universe)

    audits: list[CandidateAudit] = []
    for candidate_hash in candidate_hashes:
        config = config_by_hash[candidate_hash]
        result = evaluate_focused_strategy(config, universe)
        trades = prepare_audit_trades(result.trades, candidate_hash=candidate_hash)
        summary = build_candidate_summary(
            candidate_hash=candidate_hash,
            config=config,
            result=result,
            trades=trades,
            champion=champion,
            exact_results=exact_results,
            exact_top=exact_top,
            exact_combined=exact_combined,
        )
        fill = build_fill_audit(
            trades=trades,
            db_path=db_path,
            monte_carlo_iterations=monte_carlo_iterations,
            random_seed=random_seed,
        )
        sector = build_sector_audit(trades)
        symbol = build_symbol_audit(trades=trades, db_path=db_path)
        regime = build_regime_audit(trades=trades, db_path=db_path)
        decision, risk = candidate_decision(summary=summary, fill=fill, sector=sector, symbol=symbol, regime=regime)
        report_text = build_candidate_report(
            candidate_hash=candidate_hash,
            config=config,
            summary=summary,
            fill=fill,
            sector=sector,
            symbol=symbol,
            regime=regime,
            decision=decision,
            biggest_unresolved_risk=risk,
        )

        prefix = f"challenger_audit_{candidate_hash}"
        trades.to_csv(report_dir / f"{prefix}_trades.csv", index=False)
        summary.to_csv(report_dir / f"{prefix}_summary.csv", index=False)
        fill.to_csv(report_dir / f"{prefix}_fill.csv", index=False)
        sector.to_csv(report_dir / f"{prefix}_sector.csv", index=False)
        symbol.to_csv(report_dir / f"{prefix}_symbol.csv", index=False)
        regime.to_csv(report_dir / f"{prefix}_regime.csv", index=False)
        report_path = report_dir / f"{prefix}_report.md"
        report_path.write_text(report_text, encoding="utf-8")
        audits.append(
            CandidateAudit(
                candidate_hash=candidate_hash,
                config=config,
                result=result,
                trades=trades,
                summary=summary,
                fill=fill,
                sector=sector,
                symbol=symbol,
                regime=regime,
                report_path=report_path,
                decision=decision,
                biggest_unresolved_risk=risk,
            )
        )

    comparison = build_comparison(audits, champion=champion)
    comparison.to_csv(report_dir / "challenger_audit_comparison.csv", index=False)
    comparison_report = report_dir / "challenger_audit_comparison.md"
    comparison_report.write_text(build_comparison_report(comparison), encoding="utf-8")
    return audits, comparison, comparison_report


def read_csv_required(path: Path, *, label: str) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Missing {label}: {path}")
    return pd.read_csv(path)


def read_csv_optional(path: Path) -> pd.DataFrame:
    return pd.read_csv(path) if path.exists() else pd.DataFrame()


def load_candidate_configs(exact_results: pd.DataFrame, candidate_hashes: list[str]) -> dict[str, FocusedGridConfig]:
    """Load focused-grid configs by hash from exact results."""

    if "focused_config_hash" not in exact_results.columns:
        raise ValueError("focused exact results must include focused_config_hash")
    configs: dict[str, FocusedGridConfig] = {}
    for candidate_hash in candidate_hashes:
        matches = exact_results[exact_results["focused_config_hash"].astype(str).eq(str(candidate_hash))]
        if matches.empty:
            raise ValueError(f"Candidate hash not found in exact results: {candidate_hash}")
        configs[candidate_hash] = focused_config_from_row(matches.iloc[0])
    return configs


def focused_config_from_row(row: pd.Series) -> FocusedGridConfig:
    """Reconstruct FocusedGridConfig from one exact-results row."""

    required = [
        "market",
        "min_turnover_twd",
        "min_volume_ratio_20d",
        "market_regime_filter",
        "ranking_method",
        "momentum_cap",
        "weak_sector_handling",
    ]
    missing = [column for column in required if column not in row.index]
    if missing:
        raise ValueError(f"Exact config row is missing columns: {missing}")
    return FocusedGridConfig(
        market=str(row["market"]),  # type: ignore[arg-type]
        min_turnover_twd=float(row["min_turnover_twd"]),
        min_volume_ratio_20d=float(row["min_volume_ratio_20d"]),
        market_regime_filter=str(row["market_regime_filter"]),  # type: ignore[arg-type]
        ranking_method=str(row["ranking_method"]),  # type: ignore[arg-type]
        momentum_cap=str(row["momentum_cap"]),  # type: ignore[arg-type]
        weak_sector_handling=str(row["weak_sector_handling"]),  # type: ignore[arg-type]
    )


def prepare_audit_trades(trades: pd.DataFrame, *, candidate_hash: str) -> pd.DataFrame:
    """Normalize simulator trades for all diagnostic helpers."""

    frame = trades.copy()
    frame["candidate_hash"] = candidate_hash
    if "_diagnostic_row_id" not in frame.columns:
        frame["_diagnostic_row_id"] = np.arange(len(frame))
    frame["trade_id"] = frame.get("portfolio_trade_id", frame.index.astype(str)).astype(str)
    frame["config_hash"] = candidate_hash
    for column in ["signal_date", "entry_date", "exit_date"]:
        if column in frame.columns:
            frame[column] = pd.to_datetime(frame[column], errors="coerce").dt.normalize()
    return frame.reset_index(drop=True)


def build_candidate_summary(
    *,
    candidate_hash: str,
    config: FocusedGridConfig,
    result: StrategyResult,
    trades: pd.DataFrame,
    champion: StrategyResult,
    exact_results: pd.DataFrame,
    exact_top: pd.DataFrame,
    exact_combined: pd.DataFrame,
) -> pd.DataFrame:
    """Build basic, period, market, and champion-comparison summary rows."""

    rows: list[dict[str, Any]] = []
    config_row = {f"config_{key}": value for key, value in config.payload().items()}
    rows.append(
        {
            "section": "config",
            "metric": "focused_config",
            "value": json.dumps(config.payload(), sort_keys=True),
            **config_row,
        }
    )
    metrics = {
        **config_to_row(to_strategy_config(config)),
        **result.metrics,
        "focused_config_hash": candidate_hash,
        "recomputed_hash_matches": focused_config_hash(config) == candidate_hash,
    }
    for key, value in metrics.items():
        rows.append({"section": "basic_performance", "metric": key, "value": value, **config_row})

    for label, period_frame in [
        ("monthly_returns", period_returns(result.daily_equity, period="M")),
        ("quarterly_returns", period_returns(result.daily_equity, period="Q")),
    ]:
        for _, row in period_frame.iterrows():
            rows.append({"section": label, "metric": str(row["period"]), **row.to_dict(), **config_row})

    for _, row in summarize_market_split(trades).iterrows():
        rows.append({"section": "market_split", "metric": row["market"], **row.to_dict(), **config_row})

    combined = combine_with_champion(champion, result)
    for key, value in combined.items():
        rows.append({"section": "champion_comparison", "metric": key, "value": value, **config_row})

    exact_match = exact_results[exact_results["focused_config_hash"].astype(str).eq(candidate_hash)]
    if not exact_match.empty:
        for key, value in exact_match.iloc[0].to_dict().items():
            rows.append({"section": "source_exact_results", "metric": key, "value": value, **config_row})
    top_rank = rank_in_frame(exact_top, candidate_hash)
    combined_rank = rank_in_frame(exact_combined, candidate_hash)
    rows.append({"section": "source_rank", "metric": "exact_top_rank", "value": top_rank, **config_row})
    rows.append({"section": "source_rank", "metric": "exact_combined_rank", "value": combined_rank, **config_row})
    return pd.DataFrame(rows)


def rank_in_frame(frame: pd.DataFrame, candidate_hash: str) -> float:
    if frame.empty or "focused_config_hash" not in frame.columns:
        return np.nan
    matches = frame.index[frame["focused_config_hash"].astype(str).eq(candidate_hash)].tolist()
    return float(matches[0] + 1) if matches else np.nan


def summarize_market_split(trades: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for market, group in trades.groupby("market", dropna=False, observed=False):
        metrics = calculate_metrics(group)
        rows.append({"market": str(market), **metrics, "net_pnl": metrics["total_net_pnl"]})
    return pd.DataFrame(rows).sort_values("net_pnl", ascending=False).reset_index(drop=True) if rows else pd.DataFrame()


def build_fill_audit(
    *,
    trades: pd.DataFrame,
    db_path: Path | str,
    monte_carlo_iterations: int,
    random_seed: int,
) -> pd.DataFrame:
    enriched = enrich_with_database_context(trades, db_path=db_path)
    diagnostic = compute_execution_features(enriched)
    bucket_summary = summarize_execution_buckets(diagnostic)
    stress = run_execution_stress_tests(diagnostic)
    monte_carlo = run_fill_haircut_monte_carlo(
        diagnostic,
        iterations=monte_carlo_iterations,
        random_seed=random_seed,
    )
    return combine_sections(
        [
            ("trades", diagnostic),
            ("bucket_summary", bucket_summary),
            ("stress_tests", stress),
            ("partial_fill_monte_carlo", monte_carlo),
        ]
    )


def build_sector_audit(trades: pd.DataFrame) -> pd.DataFrame:
    frame = trades.copy()
    frame["sector"] = clean_text(frame.get("sector"), "MISSING_SECTOR")
    frame["industry"] = clean_text(frame.get("industry"), "MISSING_INDUSTRY")
    sector_summary = summarize_by_group(frame, group_column="sector", output_group_name="sector")
    industry_summary = summarize_by_group(frame, group_column="industry", output_group_name="industry")
    concentration = calculate_concentration_diagnostics(sector_summary, industry_summary)
    stress = run_sector_concentration_stress_tests(frame, sector_summary, industry_summary)
    return combine_sections(
        [
            ("sector_summary", sector_summary),
            ("industry_summary", industry_summary),
            ("concentration", concentration),
            ("stress_tests", stress),
        ]
    )


def build_symbol_audit(*, trades: pd.DataFrame, db_path: Path | str) -> pd.DataFrame:
    frame = trades.copy()
    frame["name"] = clean_text(frame.get("name"), "")
    frame["sector"] = clean_text(frame.get("sector"), "MISSING_SECTOR")
    frame["industry"] = clean_text(frame.get("industry"), "MISSING_INDUSTRY")
    trading_dates = load_trading_dates_for_trades(db_path=db_path, trades=frame)
    enriched = add_repeat_event_features(frame, trading_dates=trading_dates)
    symbol_summary = summarize_symbols(enriched)
    concentration = calculate_symbol_concentration(symbol_summary)
    hotness = summarize_hotness_buckets(enriched)
    cooldown = run_cooldown_stress_tests(enriched, trading_dates=trading_dates)
    remove_top = run_remove_top_symbol_tests(enriched, symbol_summary)
    return combine_sections(
        [
            ("symbol_summary", symbol_summary),
            ("concentration", concentration),
            ("hotness_summary", hotness),
            ("cooldown_tests", cooldown),
            ("remove_top_tests", remove_top),
        ]
    )


def build_regime_audit(*, trades: pd.DataFrame, db_path: Path | str) -> pd.DataFrame:
    try:
        regime_trades = join_taiex_regime_features(trades, db_path=db_path)
    except ValueError as exc:
        return pd.DataFrame([{"section": "error", "error": str(exc)}])
    summary = summarize_market_regimes(regime_trades)
    stress = run_market_regime_stress_tests(regime_trades)
    return combine_sections(
        [
            ("trades", regime_trades),
            ("summary", summary),
            ("stress_tests", stress),
        ]
    )


def load_trading_dates_for_trades(*, db_path: Path | str, trades: pd.DataFrame) -> list[pd.Timestamp]:
    if trades.empty:
        return []
    start = pd.to_datetime(trades["signal_date"], errors="coerce").min()
    end = pd.to_datetime(trades["signal_date"], errors="coerce").max()
    markets = sorted(trades["market"].astype(str).str.upper().unique().tolist())
    placeholders = ",".join(["?"] * len(markets))
    with get_connection(db_path, read_only=True) as conn:
        dates = conn.execute(
            f"""
            SELECT DISTINCT trade_date
            FROM daily_prices
            WHERE UPPER(market) IN ({placeholders})
              AND trade_date >= ?
              AND trade_date <= ?
            ORDER BY trade_date
            """,
            [*markets, pd.Timestamp(start).date(), pd.Timestamp(end).date()],
        ).fetch_df()
    if dates.empty:
        return sorted(pd.to_datetime(trades["signal_date"], errors="coerce").dropna().dt.normalize().unique())
    return sorted(pd.to_datetime(dates["trade_date"], errors="coerce").dropna().dt.normalize().unique())


def combine_sections(sections: list[tuple[str, pd.DataFrame]]) -> pd.DataFrame:
    frames = []
    for name, frame in sections:
        output = frame.copy()
        output.insert(0, "section", name)
        frames.append(output)
    return pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()


def clean_text(values: pd.Series | None, missing_label: str) -> pd.Series:
    if values is None:
        return pd.Series(missing_label, dtype="string")
    output = values.astype("string").fillna("").str.strip()
    return output.mask(output.str.len() == 0, missing_label)


def candidate_decision(
    *,
    summary: pd.DataFrame,
    fill: pd.DataFrame,
    sector: pd.DataFrame,
    symbol: pd.DataFrame,
    regime: pd.DataFrame,
) -> tuple[str, str]:
    basic = summary[summary["section"].eq("basic_performance")]
    metrics = {str(row["metric"]): row.get("value") for _, row in basic.iterrows()}
    final_equity = finite_float(metrics.get("final_equity"))
    profit_factor = finite_float(metrics.get("profit_factor"))
    max_dd_pct = finite_float(metrics.get("max_drawdown_pct"))
    trades = finite_float(metrics.get("trades"))
    fill_stress = fill[fill["section"].eq("stress_tests")]
    hard_possible = fill_stress[fill_stress.get("scenario", pd.Series(dtype=str)).eq("remove_hard_and_possible_fill")]
    survives_fill = not hard_possible.empty and finite_float(hard_possible.iloc[0].get("total_net_pnl")) > 0
    regime_error = not regime.empty and "error" in regime.columns and regime["section"].eq("error").any()
    sector_stress = sector[sector["section"].eq("stress_tests")]
    remove_top_sector = sector_stress[sector_stress.get("scenario", pd.Series(dtype=str)).eq("remove_top_1_sector_by_pnl")]
    survives_top_sector = not remove_top_sector.empty and finite_float(remove_top_sector.iloc[0].get("net_pnl")) > 0

    if trades >= 500 and final_equity > 4_000_000 and profit_factor > 4 and max_dd_pct > -0.05 and survives_fill:
        decision = "keep as research challenger"
        if survives_top_sector and not regime_error:
            decision = "promote to paper champion candidate"
    elif final_equity > 2_000_000 and profit_factor > 2 and survives_fill:
        decision = "keep as research challenger"
    else:
        decision = "reject"

    risk = "Day0 close limit-up execution/fill probability remains unproven without order-book or broker observations."
    if regime_error:
        risk = "Missing TAIEX regime coverage prevents full market-regime validation."
    elif not survives_fill:
        risk = "The candidate weakens under conservative daily-data fillability stress tests."
    elif not survives_top_sector:
        risk = "Performance is too dependent on the top sector or industry theme."
    return decision, risk


def build_candidate_report(
    *,
    candidate_hash: str,
    config: FocusedGridConfig,
    summary: pd.DataFrame,
    fill: pd.DataFrame,
    sector: pd.DataFrame,
    symbol: pd.DataFrame,
    regime: pd.DataFrame,
    decision: str,
    biggest_unresolved_risk: str,
) -> str:
    basic = summary[summary["section"].eq("basic_performance")][["metric", "value"]]
    market = summary[summary["section"].eq("market_split")]
    fill_summary = fill[fill["section"].isin(["bucket_summary", "stress_tests"])]
    sector_summary = sector[sector["section"].isin(["sector_summary", "industry_summary", "stress_tests"])]
    symbol_summary = symbol[symbol["section"].isin(["concentration", "cooldown_tests", "remove_top_tests"])]
    regime_summary = regime[regime["section"].isin(["summary", "stress_tests", "error"])]
    return "\n".join(
        [
            f"# Challenger Audit {candidate_hash}",
            "",
            "## Decision",
            "",
            f"- Decision: **{decision}**",
            f"- Biggest unresolved risk: {biggest_unresolved_risk}",
            "",
            "## Config",
            "",
            markdown_table(pd.DataFrame([config.payload()])),
            "",
            "## Basic Performance",
            "",
            markdown_table(basic),
            "",
            "## Market Split",
            "",
            markdown_table(market),
            "",
            "## Fill Realism",
            "",
            markdown_table(fill_summary),
            "",
            "## Sector And Industry",
            "",
            markdown_table(sector_summary),
            "",
            "## Symbol Dependency",
            "",
            markdown_table(symbol_summary),
            "",
            "## Market Regime",
            "",
            markdown_table(regime_summary),
            "",
        ]
    )


def build_comparison(audits: list[CandidateAudit], *, champion: StrategyResult) -> pd.DataFrame:
    rows = []
    champion_final = finite_float(champion.metrics.get("final_equity"))
    champion_dd = finite_float(champion.metrics.get("max_drawdown_pct"))
    champion_pf = finite_float(champion.metrics.get("profit_factor"))
    for audit in audits:
        metrics = audit.result.metrics
        combined = combine_with_champion(champion, audit.result)
        rows.append(
            {
                "candidate_hash": audit.candidate_hash,
                **audit.config.payload(),
                "decision": audit.decision,
                "biggest_unresolved_risk": audit.biggest_unresolved_risk,
                "trades": metrics.get("trades"),
                "final_equity": metrics.get("final_equity"),
                "total_net_pnl": metrics.get("total_net_pnl"),
                "annualized_return": metrics.get("annualized_return"),
                "profit_factor": metrics.get("profit_factor"),
                "max_drawdown_pct": metrics.get("max_drawdown_pct"),
                "median_net_return_per_trade": metrics.get("median_net_return_per_trade"),
                "final_equity_vs_champion": finite_float(metrics.get("final_equity")) - champion_final,
                "maxdd_vs_champion": finite_float(metrics.get("max_drawdown_pct")) - champion_dd,
                "pf_vs_champion": finite_float(metrics.get("profit_factor")) - champion_pf,
                "correlation_with_champion_daily_returns": combined.get("correlation_with_champion_daily_returns"),
                "combined_final_equity": combined.get("combined_final_equity"),
                "combined_cagr_like": combined.get("combined_cagr_like"),
                "combined_max_drawdown_pct": combined.get("combined_max_drawdown_pct"),
                "combined_improves_cagr": combined.get("combined_improves_cagr"),
                "combined_improves_drawdown": combined.get("combined_improves_drawdown"),
                "diversification_score": combined.get("diversification_score"),
            }
        )
    return pd.DataFrame(rows).sort_values(["decision", "final_equity"], ascending=[True, False]).reset_index(drop=True)


def build_comparison_report(comparison: pd.DataFrame) -> str:
    return "\n".join(
        [
            "# Challenger Audit Comparison",
            "",
            "Exact simulator results are used here. Fast pre-screen outputs are not eligible for promotion decisions.",
            "",
            "## Candidate Decisions",
            "",
            markdown_table(comparison),
            "",
            "## Strict Interpretation",
            "",
            comparison_verdict(comparison),
            "",
        ]
    )


def comparison_verdict(comparison: pd.DataFrame) -> str:
    if comparison.empty:
        return "No candidates were audited."
    promoted = comparison[comparison["decision"].eq("promote to paper champion candidate")]
    research = comparison[comparison["decision"].eq("keep as research challenger")]
    lines = []
    if not promoted.empty:
        lines.append(
            "Promotion candidate(s): "
            + ", ".join(promoted["candidate_hash"].astype(str).tolist())
            + ". Treat this as paper-trading promotion only until actual Day0 close fills are observed."
        )
    if not research.empty:
        lines.append(
            "Research challenger(s): "
            + ", ".join(research["candidate_hash"].astype(str).tolist())
            + ". These need more execution and concentration validation before replacing the current champion."
        )
    rejected = comparison[comparison["decision"].eq("reject")]
    if not rejected.empty:
        lines.append("Rejected: " + ", ".join(rejected["candidate_hash"].astype(str).tolist()) + ".")
    return "\n".join(lines) if lines else "No candidate is ready for promotion."


def finite_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    if not np.isfinite(result):
        return default
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit focused challenger candidates")
    parser.add_argument("--db", type=Path, default=get_settings().db_path)
    parser.add_argument("--candidate-hashes", nargs="+", required=True)
    parser.add_argument("--output-dir", type=Path, default=get_settings().project_root / "reports")
    parser.add_argument("--exact-results", type=Path)
    parser.add_argument("--exact-top", type=Path)
    parser.add_argument("--exact-combined", type=Path)
    parser.add_argument("--monte-carlo-iterations", type=int, default=1000)
    parser.add_argument("--random-seed", type=int, default=42)
    args = parser.parse_args()

    audits, comparison, comparison_report = audit_challenger_candidates(
        db_path=args.db,
        candidate_hashes=args.candidate_hashes,
        output_dir=args.output_dir,
        exact_results_path=args.exact_results,
        exact_top_path=args.exact_top,
        exact_combined_path=args.exact_combined,
        monte_carlo_iterations=args.monte_carlo_iterations,
        random_seed=args.random_seed,
    )
    for audit in audits:
        print(f"Wrote audit for {audit.candidate_hash}: {audit.report_path}")
    print(f"Wrote comparison: {comparison_report}")
    print(comparison[["candidate_hash", "decision", "final_equity", "profit_factor", "max_drawdown_pct"]].to_string(index=False))


if __name__ == "__main__":
    main()
