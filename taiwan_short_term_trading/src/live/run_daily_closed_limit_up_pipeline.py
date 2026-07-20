"""Daily orchestration for the closed-limit-up paper-trading workflow."""

from __future__ import annotations

import argparse
import time
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from config.settings import get_settings
from src.backtests.event_study import build_event_candidates
from src.data_collectors.collect_daily_prices import collect_daily_range, default_tpex_cache_dir, default_twse_cache_dir
from src.data_collectors.collect_market_context import MarketContextError, collect_sector_map_public, collect_taiex_public
from src.db import get_connection, init_db
from src.live.evaluate_closed_limit_up_signals import (
    LEDGER_OUTPUT,
    evaluate_closed_limit_up_signals,
)
from src.live.generate_closed_limit_up_signals import generate_closed_limit_up_signals
from src.live.generate_closed_limit_up_signals import generate_closed_limit_up_signals_for_profiles
from src.live.manual_fill_log import missing_manual_observation_status
from src.live.strategy_profiles import ALL_PROFILE_NAMES, StrategyProfile, resolve_profile_selection


EXECUTION_WARNING = "Execution remains paper-only until actual Day0 close auction/order-book fills are verified."
TAIPEI_TZ = ZoneInfo("Asia/Taipei")
STRATEGY_ANALYSIS_DIR_NAME = "strategy_analysis"
PAPER_EQUITY_CHART = "paper_equity_curves_by_profile.png"
PAPER_DRAWDOWN_CHART = "paper_drawdown_curves_by_profile.png"
PAPER_PROFILE_SUMMARY = "paper_profile_summary.csv"
HISTORICAL_NORMALIZED_EQUITY_CHART = "historical_normalized_equity_curves.png"
HISTORICAL_DRAWDOWN_CHART = "historical_drawdown_curves.png"
STRATEGY_PERFORMANCE_SUMMARY = "strategy_performance_summary.csv"


@dataclass
class PipelineResult:
    """Summary of one daily pipeline run."""

    run_timestamp: str
    db_path: Path
    output_dir: Path
    dry_run: bool
    market: str
    profile: str
    update_start: pd.Timestamp | None
    update_end: pd.Timestamp | None
    signal_date: pd.Timestamp | None
    latest_twse_date: pd.Timestamp | None
    latest_tpex_date: pd.Timestamp | None
    latest_taiex_date: pd.Timestamp | None
    taiex_freshness_status: str
    taiex_retry_attempted: bool
    taiex_retry_succeeded: bool
    skipped_profiles_due_to_stale_regime: list[str]
    daily_prices_rows: int
    event_candidates_rows: int
    generated_signal_file: Path | None
    generated_markdown_file: Path | None
    generated_signal_files: list[str]
    raw_candidates: int
    selected_orders: int
    selected_symbols: list[str]
    selected_orders_by_profile: dict[str, int]
    selected_symbols_by_profile: dict[str, list[str]]
    overlapping_symbols: dict[str, Any]
    evaluated_signal_files: list[str]
    pending_evaluations: list[str]
    warnings: list[str]
    ledger_summary: dict[str, Any]
    manual_fill_summary: dict[str, Any]
    strategy_analysis_dir: Path
    paper_strategy_analysis_refreshed: bool
    paper_equity_chart: Path
    paper_drawdown_chart: Path
    paper_profile_summary: Path
    historical_normalized_equity_chart: Path
    historical_drawdown_chart: Path
    strategy_performance_summary: Path
    report_path: Path | None


def run_daily_closed_limit_up_pipeline(
    *,
    db_path: Path | str,
    capital_twd: float = 1_000_000.0,
    market: str = "BOTH",
    start: str | pd.Timestamp | None = None,
    end: str | pd.Timestamp = "latest",
    output_dir: Path | str | None = None,
    skip_data_update: bool = False,
    skip_index_update: bool = False,
    skip_sector_update: bool = False,
    refresh_sector_map: bool = False,
    signal_date: str | pd.Timestamp = "latest",
    dry_run: bool = False,
    profile: str = "all",
    taiex_retry_delay_seconds: float = 30.0,
) -> PipelineResult:
    """Run the daily paper-trading pipeline."""

    market_value = normalize_market(market)
    db = Path(db_path)
    report_dir = Path(output_dir) if output_dir is not None else get_settings().project_root / "reports" / "live_signals"
    report_dir.mkdir(parents=True, exist_ok=True)
    init_db(db)
    warnings: list[str] = []
    run_timestamp = pd.Timestamp.now(tz=TAIPEI_TZ).isoformat()

    before_summary = load_database_summary(db)
    update_start, update_end = resolve_update_window(
        db,
        start=start,
        end=end,
        market=market_value,
        latest_summary=before_summary,
    )

    if dry_run:
        warnings.append(
            "dry_run: skipped daily price, TAIEX, sector-map, event, persistent signal, and evaluation writes."
        )
    else:
        if skip_data_update:
            warnings.append("skip_data_update enabled: daily_prices were not refreshed.")
        else:
            try:
                collect_daily_range(
                    start=update_start,
                    end=update_end,
                    market=market_value,
                    db_path=db,
                    twse_cache_dir=default_twse_cache_dir(),
                    tpex_cache_dir=default_tpex_cache_dir(),
                )
            except Exception as exc:  # noqa: BLE001 - keep pipeline report explicit.
                warnings.append(f"daily price update failed: {exc}")

        if skip_index_update:
            warnings.append("skip_index_update enabled: TAIEX index data was not refreshed.")
        else:
            try:
                collect_taiex_public(db_path=db, start=update_start, end=update_end)
            except (MarketContextError, Exception) as exc:  # noqa: BLE001
                warnings.append(f"TAIEX update failed: {exc}")

        if skip_sector_update:
            warnings.append("skip_sector_update enabled: stock_sector_map was not refreshed.")
        else:
            sector_counts = load_sector_map_counts(db)
            if refresh_sector_map or int(sector_counts.get("TPEX", 0)) == 0:
                try:
                    collect_sector_map_public(db_path=db, market="TPEX")
                except (MarketContextError, Exception) as exc:  # noqa: BLE001
                    warnings.append(f"TPEx sector map update failed: {exc}")

    resolved_signal_date = resolve_signal_date_for_pipeline(db, signal_date=signal_date)
    if resolved_signal_date is None:
        warnings.append("No TPEX daily_prices date is available; signal generation skipped.")
    after_summary = load_database_summary(db)
    taiex_retry_attempted = False
    taiex_retry_succeeded = False
    taiex_freshness_status = describe_taiex_freshness(after_summary, resolved_signal_date)
    if taiex_freshness_status != "fresh" and resolved_signal_date is not None:
        warnings.append(
            "TAIEX freshness check failed before signal generation: "
            f"status={taiex_freshness_status}, latest TAIEX={format_optional_date(after_summary.latest_taiex_date)}, "
            f"required={format_optional_date(resolved_signal_date)}."
        )
        if not dry_run and not skip_index_update:
            taiex_retry_attempted = True
            if taiex_retry_delay_seconds > 0:
                time.sleep(taiex_retry_delay_seconds)
            retry_start = taiex_retry_start_date(after_summary, required_date=resolved_signal_date, fallback_start=update_start)
            try:
                collect_taiex_public(
                    db_path=db,
                    start=retry_start,
                    end=resolved_signal_date,
                    force_refresh=True,
                )
                retried_summary = load_database_summary(db)
                retried_status = describe_taiex_freshness(retried_summary, resolved_signal_date)
                taiex_retry_succeeded = retried_status == "fresh"
                after_summary = retried_summary
                taiex_freshness_status = retried_status
                if taiex_retry_succeeded:
                    warnings.append(
                        "TAIEX retry succeeded: "
                        f"latest TAIEX={format_optional_date(after_summary.latest_taiex_date)}."
                    )
                else:
                    warnings.append(
                        "TAIEX retry completed but data is still stale: "
                        f"latest TAIEX={format_optional_date(after_summary.latest_taiex_date)}, "
                        f"required={format_optional_date(resolved_signal_date)}."
                    )
            except (MarketContextError, Exception) as exc:  # noqa: BLE001
                after_summary = load_database_summary(db)
                taiex_freshness_status = describe_taiex_freshness(after_summary, resolved_signal_date)
                warnings.append(f"TAIEX retry failed: {exc}")

    selected_profiles, skipped_profiles_due_to_stale_regime = resolve_profiles_for_freshness(
        profile,
        report_dir=report_dir,
        taiex_is_fresh=taiex_freshness_status == "fresh",
    )
    if skipped_profiles_due_to_stale_regime:
        warnings.append(
            "Skipped market-regime profile(s) because TAIEX was not fresh for the signal date: "
            + ", ".join(skipped_profiles_due_to_stale_regime)
        )

    generated_signal_file: Path | None = None
    generated_markdown_file: Path | None = None
    raw_candidates = 0
    selected_orders = 0
    selected_symbols: list[str] = []
    generated_signal_files: list[str] = []
    selected_orders_by_profile: dict[str, int] = {}
    selected_symbols_by_profile: dict[str, list[str]] = {}
    overlapping_symbols: dict[str, Any] = {}

    if resolved_signal_date is not None:
        if not dry_run:
            try:
                build_event_candidates(
                    db_path=db,
                    start=resolved_signal_date,
                    end=resolved_signal_date,
                    markets=["TWSE", "TPEX"] if market_value == "BOTH" else [market_value],
                )
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"event candidate rebuild failed: {exc}")
            try:
                if profile == "all":
                    profile_results, combined_csv, combined_md = generate_closed_limit_up_signals_for_profiles(
                        db_path=db,
                        capital_twd=capital_twd,
                        signal_date=resolved_signal_date,
                        output_dir=report_dir,
                        profile=profile,
                        profiles_override=selected_profiles,
                        force_combined=True,
                        refresh_events=False,
                    )
                    generated_signal_file = combined_csv
                    generated_markdown_file = combined_md
                    selected_orders_by_profile = {name: 0 for name in ALL_PROFILE_NAMES}
                    selected_symbols_by_profile = {name: [] for name in ALL_PROFILE_NAMES}
                    for profile_name, (orders, _skipped, csv_path, _md_path) in profile_results.items():
                        generated_signal_files.append(str(csv_path))
                        selected_orders_by_profile[profile_name] = int(len(orders))
                        selected_symbols_by_profile[profile_name] = orders["symbol"].astype(str).tolist() if not orders.empty else []
                    selected_orders = int(sum(selected_orders_by_profile.values()))
                    selected_symbols = sorted({symbol for symbols in selected_symbols_by_profile.values() for symbol in symbols})
                    overlapping_symbols = calculate_profile_overlaps(selected_symbols_by_profile)
                else:
                    selected_orders_by_profile = {profile: 0}
                    selected_symbols_by_profile = {profile: []}
                    if selected_profiles:
                        orders, _skipped, generated_signal_file, generated_markdown_file = generate_closed_limit_up_signals(
                            db_path=db,
                            capital_twd=capital_twd,
                            signal_date=resolved_signal_date,
                            output_dir=report_dir,
                            refresh_events=False,
                            profile=selected_profiles[0],
                        )
                        generated_signal_files.append(str(generated_signal_file))
                        selected_orders = int(len(orders))
                        selected_symbols = orders["symbol"].astype(str).tolist() if not orders.empty else []
                        selected_orders_by_profile[profile] = selected_orders
                        selected_symbols_by_profile[profile] = selected_symbols
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"signal generation failed: {exc}")
        else:
            if profile == "all":
                generated_signal_file = report_dir / f"closed_limit_up_signals_all_profiles_{resolved_signal_date:%Y-%m-%d}.csv"
                generated_markdown_file = report_dir / f"closed_limit_up_signals_all_profiles_{resolved_signal_date:%Y-%m-%d}.md"
                try:
                    with TemporaryDirectory(prefix="closed_limit_up_dry_run_") as tmpdir:
                        profile_results, _combined_csv, _combined_md = generate_closed_limit_up_signals_for_profiles(
                            db_path=db,
                            capital_twd=capital_twd,
                            signal_date=resolved_signal_date,
                            output_dir=tmpdir,
                            profile=profile,
                            profiles_override=selected_profiles,
                            force_combined=True,
                            refresh_events=False,
                        )
                    selected_orders_by_profile = {name: 0 for name in ALL_PROFILE_NAMES}
                    selected_symbols_by_profile = {name: [] for name in ALL_PROFILE_NAMES}
                    for profile_name, (orders, _skipped, _csv_path, _md_path) in profile_results.items():
                        selected_orders_by_profile[profile_name] = int(len(orders))
                        selected_symbols_by_profile[profile_name] = (
                            orders["symbol"].astype(str).tolist() if not orders.empty else []
                        )
                    selected_orders = int(sum(selected_orders_by_profile.values()))
                    selected_symbols = sorted(
                        {symbol for symbols in selected_symbols_by_profile.values() for symbol in symbols}
                    )
                    overlapping_symbols = calculate_profile_overlaps(selected_symbols_by_profile)
                except Exception as exc:  # noqa: BLE001
                    warnings.append(f"dry-run signal selection failed: {exc}")
                    selected_orders_by_profile = {name: 0 for name in ALL_PROFILE_NAMES}
                    selected_symbols_by_profile = {name: [] for name in ALL_PROFILE_NAMES}
            else:
                generated_signal_file = report_dir / f"closed_limit_up_signals_{profile}_{resolved_signal_date:%Y-%m-%d}.csv"
                generated_markdown_file = report_dir / f"closed_limit_up_signals_{profile}_{resolved_signal_date:%Y-%m-%d}.md"
                try:
                    selected_orders_by_profile = {profile: selected_orders}
                    selected_symbols_by_profile = {profile: selected_symbols}
                    if selected_profiles:
                        with TemporaryDirectory(prefix="closed_limit_up_dry_run_") as tmpdir:
                            orders, _skipped, _csv_path, _md_path = generate_closed_limit_up_signals(
                                db_path=db,
                                capital_twd=capital_twd,
                                signal_date=resolved_signal_date,
                                output_dir=tmpdir,
                                refresh_events=False,
                                profile=selected_profiles[0],
                            )
                        selected_orders = int(len(orders))
                        selected_symbols = orders["symbol"].astype(str).tolist() if not orders.empty else []
                        selected_orders_by_profile = {profile: selected_orders}
                        selected_symbols_by_profile = {profile: selected_symbols}
                except Exception as exc:  # noqa: BLE001
                    warnings.append(f"dry-run signal selection failed: {exc}")
                    selected_orders_by_profile = {profile: 0}
                    selected_symbols_by_profile = {profile: []}
        raw_candidates = count_raw_closed_limit_up_candidates(db, resolved_signal_date, market=market_value)

    evaluated_signal_files: list[str] = []
    pending_evaluations: list[str] = []
    if not dry_run:
        evaluated_signal_files, pending_evaluations = evaluate_previous_signal_files(
            db_path=db,
            output_dir=report_dir,
            current_signal_date=resolved_signal_date,
        )

    latest_summary = load_database_summary(db)
    event_candidates_rows = count_event_candidates(db)
    ledger_summary = summarize_paper_ledger(report_dir / LEDGER_OUTPUT)
    manual_fill_summary = missing_manual_observation_status(db_path=db, output_dir=report_dir)
    strategy_analysis_dir = report_dir.parent / STRATEGY_ANALYSIS_DIR_NAME
    paper_equity_chart = strategy_analysis_dir / PAPER_EQUITY_CHART
    paper_drawdown_chart = strategy_analysis_dir / PAPER_DRAWDOWN_CHART
    paper_profile_summary = strategy_analysis_dir / PAPER_PROFILE_SUMMARY
    historical_normalized_equity_chart = strategy_analysis_dir / HISTORICAL_NORMALIZED_EQUITY_CHART
    historical_drawdown_chart = strategy_analysis_dir / HISTORICAL_DRAWDOWN_CHART
    strategy_performance_summary = strategy_analysis_dir / STRATEGY_PERFORMANCE_SUMMARY
    paper_strategy_analysis_refreshed = False
    if not dry_run:
        try:
            from src.reports.generate_strategy_equity_analysis import refresh_paper_strategy_analysis

            refresh_paper_strategy_analysis(output_dir=strategy_analysis_dir, ledger_path=report_dir / LEDGER_OUTPUT)
            paper_strategy_analysis_refreshed = True
        except Exception as exc:  # noqa: BLE001 - report the failure without blocking paper signals.
            warnings.append(f"paper strategy analysis refresh failed: {exc}")
    if (
        int(manual_fill_summary.get("total_signal_orders", 0)) > 0
        and int(manual_fill_summary.get("missing_manual_observations", 0)) > 0
    ):
        warnings.append(
            "Manual fill observations are missing for "
            f"{manual_fill_summary['missing_manual_observations']} selected paper signal(s)."
        )
    warnings.extend(build_data_warnings(db, latest_summary))
    if not warnings:
        warnings.append("No data warnings.")
    warnings.append(EXECUTION_WARNING)

    result = PipelineResult(
        run_timestamp=run_timestamp,
        db_path=db,
        output_dir=report_dir,
        dry_run=dry_run,
        market=market_value,
        profile=profile,
        update_start=update_start,
        update_end=update_end,
        signal_date=resolved_signal_date,
        latest_twse_date=latest_summary.latest_twse_date,
        latest_tpex_date=latest_summary.latest_tpex_date,
        latest_taiex_date=latest_summary.latest_taiex_date,
        taiex_freshness_status=taiex_freshness_status,
        taiex_retry_attempted=taiex_retry_attempted,
        taiex_retry_succeeded=taiex_retry_succeeded,
        skipped_profiles_due_to_stale_regime=skipped_profiles_due_to_stale_regime,
        daily_prices_rows=latest_summary.daily_prices_rows,
        event_candidates_rows=event_candidates_rows,
        generated_signal_file=generated_signal_file,
        generated_markdown_file=generated_markdown_file,
        generated_signal_files=generated_signal_files,
        raw_candidates=raw_candidates,
        selected_orders=selected_orders,
        selected_symbols=selected_symbols,
        selected_orders_by_profile=selected_orders_by_profile,
        selected_symbols_by_profile=selected_symbols_by_profile,
        overlapping_symbols=overlapping_symbols,
        evaluated_signal_files=evaluated_signal_files,
        pending_evaluations=pending_evaluations,
        warnings=warnings,
        ledger_summary=ledger_summary,
        manual_fill_summary=manual_fill_summary,
        strategy_analysis_dir=strategy_analysis_dir,
        paper_strategy_analysis_refreshed=paper_strategy_analysis_refreshed,
        paper_equity_chart=paper_equity_chart,
        paper_drawdown_chart=paper_drawdown_chart,
        paper_profile_summary=paper_profile_summary,
        historical_normalized_equity_chart=historical_normalized_equity_chart,
        historical_drawdown_chart=historical_drawdown_chart,
        strategy_performance_summary=strategy_performance_summary,
        report_path=None,
    )

    report_path = report_dir / f"daily_pipeline_report_{date_label(resolved_signal_date)}.md"
    report_path.write_text(build_pipeline_report(result), encoding="utf-8")
    result.report_path = report_path
    return result


@dataclass(frozen=True)
class DatabaseSummary:
    latest_twse_date: pd.Timestamp | None
    latest_tpex_date: pd.Timestamp | None
    latest_taiex_date: pd.Timestamp | None
    daily_prices_rows: int


def load_database_summary(db_path: Path | str) -> DatabaseSummary:
    with get_connection(db_path, read_only=True) as conn:
        dates = conn.execute(
            """
            SELECT UPPER(market) AS market, MAX(trade_date) AS latest_date
            FROM daily_prices
            GROUP BY UPPER(market)
            """
        ).fetch_df()
        row_count = conn.execute("SELECT COUNT(*) FROM daily_prices").fetchone()[0]
        taiex = conn.execute(
            "SELECT MAX(trade_date) FROM index_daily_prices WHERE UPPER(index_symbol) = 'TAIEX'"
        ).fetchone()[0]
    date_by_market = {
        str(row["market"]).upper(): optional_timestamp(row["latest_date"]) for _, row in dates.iterrows()
    }
    return DatabaseSummary(
        latest_twse_date=date_by_market.get("TWSE"),
        latest_tpex_date=date_by_market.get("TPEX"),
        latest_taiex_date=optional_timestamp(taiex),
        daily_prices_rows=int(row_count or 0),
    )


def resolve_update_window(
    db_path: Path | str,
    *,
    start: str | pd.Timestamp | None,
    end: str | pd.Timestamp,
    market: str,
    latest_summary: DatabaseSummary,
) -> tuple[pd.Timestamp, pd.Timestamp]:
    end_ts = resolve_end_date(end)
    if start is not None:
        return pd.Timestamp(start).normalize(), end_ts
    latest_dates = []
    if market in {"TWSE", "BOTH"} and latest_summary.latest_twse_date is not None:
        latest_dates.append(latest_summary.latest_twse_date)
    if market in {"TPEX", "BOTH"} and latest_summary.latest_tpex_date is not None:
        latest_dates.append(latest_summary.latest_tpex_date)
    if latest_dates:
        start_ts = min(latest_dates).normalize()
    else:
        start_ts = end_ts
    return start_ts, end_ts


def resolve_end_date(end: str | pd.Timestamp) -> pd.Timestamp:
    if isinstance(end, str) and end.strip().lower() == "latest":
        return pd.Timestamp.now(tz=TAIPEI_TZ).tz_localize(None).normalize()
    return pd.Timestamp(end).normalize()


def resolve_signal_date_for_pipeline(db_path: Path | str, *, signal_date: str | pd.Timestamp) -> pd.Timestamp | None:
    if isinstance(signal_date, str) and signal_date.strip().lower() == "latest":
        with get_connection(db_path, read_only=True) as conn:
            value = conn.execute(
                "SELECT MAX(trade_date) FROM daily_prices WHERE UPPER(market) = 'TPEX'"
            ).fetchone()[0]
        return optional_timestamp(value)
    return pd.Timestamp(signal_date).normalize()


def evaluate_previous_signal_files(
    *,
    db_path: Path | str,
    output_dir: Path,
    current_signal_date: pd.Timestamp | None,
) -> tuple[list[str], list[str]]:
    evaluated: list[str] = []
    pending: list[str] = []
    for signals_path in sorted(output_dir.glob("closed_limit_up_signals_*.csv")):
        if signals_path.stem.startswith("closed_limit_up_signals_all_profiles_"):
            continue
        signal_date = signal_date_from_path(signals_path)
        if current_signal_date is not None and signal_date is not None and signal_date >= current_signal_date:
            continue
        eval_path = output_dir / f"closed_limit_up_eval_{signals_path.stem.removeprefix('closed_limit_up_signals_')}.csv"
        should_evaluate = not eval_path.exists() or evaluation_is_pending(eval_path)
        if not should_evaluate:
            continue
        try:
            evaluations, _summary, _csv, _md, _ledger = evaluate_closed_limit_up_signals(
                db_path=db_path,
                signals_csv=signals_path,
                output_dir=output_dir,
            )
            if not evaluations.empty and evaluations["status"].eq("missing_day1_data").any():
                pending.append(signals_path.name)
            else:
                evaluated.append(signals_path.name)
        except Exception as exc:  # noqa: BLE001
            pending.append(f"{signals_path.name}: {exc}")
    return evaluated, pending


def calculate_profile_overlaps(selected_symbols_by_profile: dict[str, list[str]]) -> dict[str, list[str]]:
    """Return pairwise overlapping symbols by profile."""

    names = list(selected_symbols_by_profile)
    overlaps: dict[str, list[str]] = {}
    for i, left in enumerate(names):
        for right in names[i + 1 :]:
            overlap = sorted(set(selected_symbols_by_profile.get(left, [])) & set(selected_symbols_by_profile.get(right, [])))
            overlaps[f"{left}__{right}"] = overlap
    return overlaps


def evaluation_is_pending(eval_path: Path) -> bool:
    try:
        frame = pd.read_csv(eval_path)
    except (pd.errors.EmptyDataError, FileNotFoundError):
        return True
    if frame.empty:
        return False
    return bool(frame.get("status", pd.Series(dtype=str)).eq("missing_day1_data").any())


def count_raw_closed_limit_up_candidates(db_path: Path | str, signal_date: pd.Timestamp, *, market: str = "TPEX") -> int:
    markets = ["TWSE", "TPEX"] if market.upper() == "BOTH" else [market.upper()]
    placeholders = ",".join(["?"] * len(markets))
    with get_connection(db_path, read_only=True) as conn:
        return int(
            conn.execute(
                f"""
                SELECT COUNT(*)
                FROM event_candidates
                WHERE trade_date = ?
                  AND UPPER(market) IN ({placeholders})
                  AND event_type = 'closed_limit_up'
                """,
                [signal_date.date(), *markets],
            ).fetchone()[0]
            or 0
        )


def count_event_candidates(db_path: Path | str) -> int:
    with get_connection(db_path, read_only=True) as conn:
        return int(conn.execute("SELECT COUNT(*) FROM event_candidates").fetchone()[0] or 0)


def load_sector_map_counts(db_path: Path | str) -> dict[str, int]:
    with get_connection(db_path, read_only=True) as conn:
        frame = conn.execute(
            "SELECT UPPER(market) AS market, COUNT(*) AS rows FROM stock_sector_map GROUP BY UPPER(market)"
        ).fetch_df()
    return {str(row["market"]).upper(): int(row["rows"]) for _, row in frame.iterrows()}


def describe_taiex_freshness(summary: DatabaseSummary, required_date: pd.Timestamp | None) -> str:
    """Return the TAIEX freshness state for market-regime signal generation."""

    if required_date is None:
        return "unknown_no_signal_date"
    if summary.latest_taiex_date is None:
        return "missing"
    if summary.latest_taiex_date < required_date:
        return "stale"
    return "fresh"


def taiex_retry_start_date(
    summary: DatabaseSummary,
    *,
    required_date: pd.Timestamp,
    fallback_start: pd.Timestamp,
) -> pd.Timestamp:
    """Choose a tight TAIEX retry window ending at the required signal date."""

    if summary.latest_taiex_date is None:
        return min(fallback_start.normalize(), required_date.normalize())
    next_date = summary.latest_taiex_date + pd.Timedelta(days=1)
    return min(next_date.normalize(), required_date.normalize())


def resolve_profiles_for_freshness(
    profile_selection: str,
    *,
    report_dir: Path,
    taiex_is_fresh: bool,
) -> tuple[list[StrategyProfile], list[str]]:
    """Skip profiles that need market-regime data when TAIEX is stale."""

    profiles = resolve_profile_selection(
        profile_selection,
        report_dir=report_dir.parent if report_dir.name == "live_signals" else report_dir,
    )
    allowed: list[StrategyProfile] = []
    skipped: list[str] = []
    for item in profiles:
        needs_regime = str(item.market_regime_filter).strip().lower() != "none"
        if needs_regime and not taiex_is_fresh:
            skipped.append(item.profile_name)
        else:
            allowed.append(item)
    return allowed, skipped


def summarize_paper_ledger(ledger_path: Path) -> dict[str, Any]:
    if not ledger_path.exists():
        return empty_ledger_summary()
    try:
        ledger = pd.read_csv(ledger_path)
    except pd.errors.EmptyDataError:
        return empty_ledger_summary()
    if ledger.empty or "status" not in ledger.columns:
        return empty_ledger_summary()
    evaluated = ledger[ledger["status"].eq("evaluated")].copy()
    if evaluated.empty:
        return empty_ledger_summary()
    net_pnl = pd.to_numeric(evaluated["net_pnl"], errors="coerce").fillna(0.0)
    net_return = pd.to_numeric(evaluated["net_return"], errors="coerce")
    gains = net_pnl[net_pnl > 0].sum()
    losses = abs(net_pnl[net_pnl < 0].sum())
    by_profile = {}
    if "profile_name" in evaluated.columns:
        for profile_name, group in evaluated.groupby("profile_name", dropna=False):
            profile_pnl = pd.to_numeric(group["net_pnl"], errors="coerce").fillna(0.0)
            profile_return = pd.to_numeric(group["net_return"], errors="coerce")
            profile_gains = profile_pnl[profile_pnl > 0].sum()
            profile_losses = abs(profile_pnl[profile_pnl < 0].sum())
            by_profile[str(profile_name)] = {
                "trades": int(len(group)),
                "net_pnl": float(profile_pnl.sum()),
                "avg_net_return": float(profile_return.mean()),
                "win_rate": float((profile_pnl > 0).mean()),
                "profit_factor": profit_factor(profile_gains, profile_losses),
            }
    return {
        "total_evaluated_paper_trades": int(len(evaluated)),
        "cumulative_net_pnl": float(net_pnl.sum()),
        "avg_net_return": float(net_return.mean()),
        "median_net_return": float(net_return.median()),
        "win_rate": float((net_pnl > 0).mean()),
        "profit_factor": profit_factor(gains, losses),
        "by_profile": by_profile,
    }


def empty_ledger_summary() -> dict[str, Any]:
    return {
        "total_evaluated_paper_trades": 0,
        "cumulative_net_pnl": 0.0,
        "avg_net_return": np.nan,
        "median_net_return": np.nan,
        "win_rate": np.nan,
        "profit_factor": np.nan,
        "by_profile": {},
    }


def build_data_warnings(db_path: Path | str, summary: DatabaseSummary) -> list[str]:
    warnings: list[str] = []
    if summary.latest_twse_date is None:
        warnings.append("TWSE daily_prices are missing.")
    if summary.latest_tpex_date is None:
        warnings.append("TPEx daily_prices are missing.")
    if summary.latest_taiex_date is None:
        warnings.append("TAIEX index_daily_prices are missing.")
    elif summary.latest_tpex_date is not None and summary.latest_taiex_date < summary.latest_tpex_date:
        warnings.append(
            f"TAIEX data may be stale: latest TAIEX {summary.latest_taiex_date.date()} "
            f"is before latest TPEx {summary.latest_tpex_date.date()}."
        )
    sector_counts = load_sector_map_counts(db_path)
    if int(sector_counts.get("TPEX", 0)) == 0:
        warnings.append("TPEx stock_sector_map rows are missing.")
    return warnings


def build_pipeline_report(result: PipelineResult) -> str:
    selected_symbols = ", ".join(result.selected_symbols) if result.selected_symbols else "_None_"
    evaluated = "\n".join(f"- {name}" for name in result.evaluated_signal_files) or "_None_"
    pending = "\n".join(f"- {name}" for name in result.pending_evaluations) or "_None_"
    warnings = "\n".join(f"- {warning}" for warning in result.warnings)
    signal_file_label = "Planned signal file" if result.dry_run else "Generated signal file"
    profile_rows = [
        {
            "profile_name": profile_name,
            "selected_orders": count,
            "symbols": ", ".join(result.selected_symbols_by_profile.get(profile_name, [])),
        }
        for profile_name, count in result.selected_orders_by_profile.items()
    ]
    overlap_rows = [
        {"profile_pair": key, "overlap_count": len(value), "symbols": ", ".join(value)}
        for key, value in result.overlapping_symbols.items()
    ]
    ledger_by_profile = result.ledger_summary.get("by_profile", {})
    ledger_profile_rows = [
        {"profile_name": profile_name, **metrics} for profile_name, metrics in ledger_by_profile.items()
    ]
    freshness_rows = [
        {
            "taiex_freshness_status": result.taiex_freshness_status,
            "taiex_retry_attempted": result.taiex_retry_attempted,
            "taiex_retry_succeeded": result.taiex_retry_succeeded,
            "skipped_profiles_due_to_stale_regime": ", ".join(result.skipped_profiles_due_to_stale_regime),
        }
    ]
    lines = [
        f"# Daily Closed-Limit-Up Paper Pipeline - {date_label(result.signal_date)}",
        "",
        f"Run timestamp: `{result.run_timestamp}`",
        f"Database: `{result.db_path}`",
        f"Dry run: `{result.dry_run}`",
        f"Profile selection: `{result.profile}`",
        "",
        "## Data Coverage",
        "",
        f"Latest TWSE date: `{format_optional_date(result.latest_twse_date)}`",
        f"Latest TPEx date: `{format_optional_date(result.latest_tpex_date)}`",
        f"Latest TAIEX date: `{format_optional_date(result.latest_taiex_date)}`",
        f"TAIEX freshness status: `{result.taiex_freshness_status}`",
        f"daily_prices rows: `{result.daily_prices_rows:,}`",
        f"event_candidates rows: `{result.event_candidates_rows:,}`",
        "",
        "## TAIEX Freshness",
        "",
        markdown_table(pd.DataFrame(freshness_rows)),
        "",
        "## Signal Generation",
        "",
        f"Signal date: `{format_optional_date(result.signal_date)}`",
        f"{signal_file_label}: `{result.generated_signal_file or ''}`",
        f"Raw closed-limit-up candidates: `{result.raw_candidates:,}`",
        f"Selected paper orders: `{result.selected_orders:,}`",
        f"Selected symbols: {selected_symbols}",
        "",
        "## Profile Comparison",
        "",
        markdown_table(pd.DataFrame(profile_rows)),
        "",
        "Overlapping symbols:",
        "",
        markdown_table(pd.DataFrame(overlap_rows)),
        "",
        "## Previous Evaluations",
        "",
        "Evaluated signal files:",
        "",
        evaluated,
        "",
        "Pending evaluations:",
        "",
        pending,
        "",
        "## Paper Ledger Summary",
        "",
        markdown_table(pd.DataFrame([result.ledger_summary])),
        "",
        "Profile-level ledger:",
        "",
        markdown_table(pd.DataFrame(ledger_profile_rows)),
        "",
        "## Strategy Analysis Outputs",
        "",
        f"Paper-only analysis refreshed this run: `{result.paper_strategy_analysis_refreshed}`",
        f"Strategy analysis directory: `{result.strategy_analysis_dir}`",
        f"Latest paper equity chart: `{result.paper_equity_chart}`",
        f"Latest paper drawdown chart: `{result.paper_drawdown_chart}`",
        f"Paper profile summary: `{result.paper_profile_summary}`",
        f"Historical normalized equity chart: `{result.historical_normalized_equity_chart}`",
        f"Historical drawdown chart: `{result.historical_drawdown_chart}`",
        f"Strategy performance summary: `{result.strategy_performance_summary}`",
        "",
        "## Manual Fill Observations",
        "",
        markdown_table(pd.DataFrame([result.manual_fill_summary])),
        "",
        "## Warnings",
        "",
        warnings,
        "",
    ]
    return "\n".join(lines)


def normalize_market(market: str) -> str:
    value = str(market).upper().strip()
    if value not in {"TWSE", "TPEX", "BOTH"}:
        raise ValueError("market must be TWSE, TPEX, or BOTH")
    return value


def signal_date_from_path(path: Path) -> pd.Timestamp | None:
    stem = path.stem.removeprefix("closed_limit_up_signals_")
    if stem.startswith("all_profiles_"):
        return None
    if "_" in stem:
        stem = stem.rsplit("_", 1)[-1]
    try:
        return pd.Timestamp(stem).normalize()
    except ValueError:
        return None


def optional_timestamp(value: Any) -> pd.Timestamp | None:
    if value is None or pd.isna(value):
        return None
    return pd.Timestamp(value).normalize()


def format_optional_date(value: pd.Timestamp | None) -> str:
    if value is None or pd.isna(value):
        return "missing"
    return value.date().isoformat()


def date_label(value: pd.Timestamp | None) -> str:
    if value is None or pd.isna(value):
        return pd.Timestamp.now(tz=TAIPEI_TZ).strftime("%Y-%m-%d")
    return value.strftime("%Y-%m-%d")


def profit_factor(gains: float, losses: float) -> float:
    if losses == 0 and gains > 0:
        return float("inf")
    if losses == 0:
        return np.nan
    return float(gains / losses)


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
    parser = argparse.ArgumentParser(description="Run daily closed-limit-up paper-trading pipeline")
    parser.add_argument("--db", type=Path, default=get_settings().db_path)
    parser.add_argument("--capital-twd", type=float, default=1_000_000.0)
    parser.add_argument("--market", choices=["TWSE", "TPEX", "BOTH"], default="BOTH")
    parser.add_argument("--start")
    parser.add_argument("--end", default="latest")
    parser.add_argument("--output-dir", type=Path, default=get_settings().project_root / "reports" / "live_signals")
    parser.add_argument("--skip-data-update", action="store_true")
    parser.add_argument("--skip-index-update", action="store_true")
    parser.add_argument("--skip-sector-update", action="store_true")
    parser.add_argument("--refresh-sector-map", action="store_true")
    parser.add_argument("--signal-date", default="latest")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--taiex-retry-delay-seconds", type=float, default=30.0)
    parser.add_argument(
        "--profile",
        choices=[*ALL_PROFILE_NAMES, "all"],
        default="all",
    )
    args = parser.parse_args()

    result = run_daily_closed_limit_up_pipeline(
        db_path=args.db,
        capital_twd=args.capital_twd,
        market=args.market,
        start=args.start,
        end=args.end,
        output_dir=args.output_dir,
        skip_data_update=args.skip_data_update,
        skip_index_update=args.skip_index_update,
        skip_sector_update=args.skip_sector_update,
        refresh_sector_map=args.refresh_sector_map,
        signal_date=args.signal_date,
        dry_run=args.dry_run,
        profile=args.profile,
        taiex_retry_delay_seconds=args.taiex_retry_delay_seconds,
    )
    print(f"Wrote daily pipeline report to {result.report_path}")
    print(f"Signal date: {format_optional_date(result.signal_date)}")
    print(f"Raw candidates: {result.raw_candidates}")
    print(f"Selected paper orders: {result.selected_orders}")
    print(f"TAIEX freshness: {result.taiex_freshness_status}")
    if result.taiex_retry_attempted:
        print(f"TAIEX retry succeeded: {result.taiex_retry_succeeded}")
    if result.skipped_profiles_due_to_stale_regime:
        print("Skipped stale-regime profiles:")
        for profile_name in result.skipped_profiles_due_to_stale_regime:
            print(f"- {profile_name}")
    print("Selected orders by profile:")
    for profile_name, count in result.selected_orders_by_profile.items():
        print(f"- {profile_name}: {count}")
    print(f"Evaluated previous files: {len(result.evaluated_signal_files)}")
    print(f"Pending evaluations: {len(result.pending_evaluations)}")
    print("\nWarnings")
    for warning in result.warnings:
        print(f"- {warning}")


if __name__ == "__main__":
    main()
