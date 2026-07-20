#!/usr/bin/env python3
"""Databricks entry point for the Taiwan paper-trading daily pipeline.

This script is intentionally thin: it configures the runtime root, validates
that the DuckDB file exists, redirects logs under the configured root, then
calls the same Python pipeline used by local launchd.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo


DEFAULT_DATABRICKS_ROOT = Path("/dbfs/FileStore/taiwan_trading")
DEFAULT_DATABRICKS_SCRATCH_ROOT = Path("/local_disk0/taiwan_trading_work")
CHICAGO_TZ = ZoneInfo("America/Chicago")


def resolve_code_root(explicit_code_root: Path | str | None = None) -> Path:
    """Resolve the checked-out project root in CLI and Databricks Job contexts."""

    if explicit_code_root is not None:
        root = Path(explicit_code_root).expanduser().resolve()
        if not root.exists():
            raise FileNotFoundError(f"Explicit code root does not exist: {root}")
        return root

    file_value = globals().get("__file__")
    if file_value:
        return Path(file_value).resolve().parents[1]

    env_code_root = os.getenv("TAIWAN_TRADING_CODE_ROOT")
    if env_code_root:
        root = Path(env_code_root).expanduser().resolve()
        if not root.exists():
            raise FileNotFoundError(f"TAIWAN_TRADING_CODE_ROOT does not exist: {root}")
        return root

    cwd = Path.cwd().resolve()
    if (cwd / "scripts" / "run_databricks_daily_pipeline.py").exists() and (cwd / "src").is_dir():
        return cwd

    if cwd.name == "scripts" and (cwd / "run_databricks_daily_pipeline.py").exists() and (cwd.parent / "src").is_dir():
        return cwd.parent

    raise RuntimeError("Cannot resolve code root. Run from project root or set TAIWAN_TRADING_CODE_ROOT.")


def safe_write_text(path: Path, text: str) -> None:
    """Write text in one operation for Databricks Volume compatibility."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def safe_append_text(path: Path, text: str) -> None:
    """Append text without holding or flushing an open Volume file handle."""

    existing = ""
    if path.exists():
        existing = path.read_text(encoding="utf-8")
    safe_write_text(path, existing + text)


def default_scratch_root(root: Path) -> Path:
    """Choose a scratch root that is local on Databricks and safe locally."""

    configured = os.getenv("TAIWAN_TRADING_SCRATCH_ROOT")
    if configured:
        return Path(configured).expanduser()
    root_text = str(root)
    if os.getenv("DATABRICKS_RUNTIME_VERSION") or root_text.startswith(("/dbfs/", "/Volumes/")):
        return DEFAULT_DATABRICKS_SCRATCH_ROOT
    return root / ".databricks_scratch"


def copy_file_with_size_check(source: Path, destination: Path, *, label: str) -> None:
    """Copy a file and verify the destination byte size matches."""

    if not source.exists():
        raise FileNotFoundError(f"{label} source is missing: {source}")
    source_size = source.stat().st_size
    if source_size <= 0:
        raise RuntimeError(f"{label} source is empty: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    destination_size = destination.stat().st_size
    if destination_size != source_size:
        raise RuntimeError(
            f"{label} copy size mismatch: source={source_size} bytes, "
            f"destination={destination_size} bytes"
        )


def copy_tree_files(source_dir: Path, destination_dir: Path) -> int:
    """Copy all files from one directory tree to another and return file count."""

    if not source_dir.exists():
        return 0
    count = 0
    for source in source_dir.rglob("*"):
        if not source.is_file():
            continue
        relative = source.relative_to(source_dir)
        destination = destination_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        count += 1
    return count


def prepare_scratch_workspace(
    *,
    persistent_db: Path,
    persistent_reports_dir: Path,
    scratch_root: Path,
) -> tuple[Path, Path, Path]:
    """Reset scratch, copy persistent DB and reports into it, and return scratch paths."""

    if scratch_root.resolve() == Path("/"):
        raise RuntimeError("Refusing to use filesystem root as scratch directory")
    if scratch_root.resolve() == persistent_reports_dir.parent.resolve():
        raise RuntimeError("Scratch root must not equal the persistent runtime root")

    shutil.rmtree(scratch_root, ignore_errors=True)
    scratch_db = scratch_root / "data" / "taiwan_trading.duckdb"
    scratch_output_dir = scratch_root / "reports" / "live_signals"
    scratch_logs_dir = scratch_root / "logs"
    scratch_logs_dir.mkdir(parents=True, exist_ok=True)
    copy_file_with_size_check(persistent_db, scratch_db, label="DuckDB scratch input")
    copy_tree_files(persistent_reports_dir, scratch_root / "reports")
    scratch_output_dir.mkdir(parents=True, exist_ok=True)
    return scratch_db, scratch_output_dir, scratch_logs_dir


def sync_scratch_db_back(*, scratch_db: Path, persistent_db: Path) -> None:
    """Copy scratch DuckDB back to persistent storage via temp file then replace."""

    temp_path = persistent_db.with_name(f".{persistent_db.name}.tmp.{os.getpid()}")
    try:
        copy_file_with_size_check(scratch_db, temp_path, label="DuckDB sync-back")
        os.replace(temp_path, persistent_db)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def should_run_full_strategy_analysis(args: argparse.Namespace, now: datetime | None = None) -> bool:
    """Return whether this run should do the expensive historical chart refresh."""

    if bool(getattr(args, "refresh_strategy_analysis", False)):
        return True
    if not bool(getattr(args, "weekly_strategy_analysis", False)):
        return False
    current = now or datetime.now(CHICAGO_TZ)
    return current.astimezone(CHICAGO_TZ).weekday() == 6


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Taiwan paper pipeline on Databricks")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(os.getenv("TAIWAN_TRADING_ROOT", DEFAULT_DATABRICKS_ROOT)),
        help="Runtime root for data, reports, and logs. Examples: /dbfs/FileStore/taiwan_trading or a UC Volume path.",
    )
    parser.add_argument(
        "--scratch-root",
        type=Path,
        help="Local scratch root for active DuckDB writes. Defaults to /local_disk0/taiwan_trading_work on Databricks.",
    )
    parser.add_argument(
        "--code-root",
        type=Path,
        help=(
            "Explicit project code root. Use this for Databricks Jobs when __file__ is unavailable, "
            "for example /Workspace/Users/.../TWSE_trading/taiwan_short_term_trading."
        ),
    )
    parser.add_argument("--db", type=Path, help="DuckDB path. Defaults to <root>/data/taiwan_trading.duckdb.")
    parser.add_argument("--capital-twd", type=float, default=1_000_000.0)
    parser.add_argument("--market", choices=["TWSE", "TPEX", "BOTH"], default="BOTH")
    parser.add_argument("--profile", default="all")
    parser.add_argument("--start")
    parser.add_argument("--end", default="latest")
    parser.add_argument("--signal-date", default="latest")
    parser.add_argument("--skip-data-update", action="store_true")
    parser.add_argument("--skip-index-update", action="store_true")
    parser.add_argument("--skip-sector-update", action="store_true")
    parser.add_argument("--refresh-sector-map", action="store_true")
    parser.add_argument(
        "--refresh-strategy-analysis",
        action="store_true",
        help="Run the full historical strategy equity reconstruction after the daily pipeline.",
    )
    parser.add_argument(
        "--weekly-strategy-analysis",
        action="store_true",
        help="Run full historical strategy analysis only when the Databricks runner date is Sunday.",
    )
    parser.add_argument("--taiex-retry-delay-seconds", type=float, default=30.0)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def run_databricks_pipeline(
    args: argparse.Namespace,
    *,
    pipeline_func: Callable[..., Any] | None = None,
    strategy_analysis_func: Callable[..., Any] | None = None,
) -> int:
    code_root = resolve_code_root(args.code_root)
    root = args.root.expanduser()
    persistent_db = (args.db if args.db is not None else root / "data" / "taiwan_trading.duckdb").expanduser()
    persistent_reports_dir = root / "reports"
    persistent_output_dir = persistent_reports_dir / "live_signals"
    persistent_strategy_analysis_dir = persistent_reports_dir / "strategy_analysis"
    persistent_log_dir = root / "logs"
    scratch_root = (args.scratch_root if args.scratch_root is not None else default_scratch_root(root)).expanduser()
    run_date = datetime.now(CHICAGO_TZ).strftime("%Y-%m-%d")
    persistent_log_file = persistent_log_dir / f"databricks_daily_pipeline_{run_date}.log"
    persistent_error_log = persistent_log_dir / "databricks_daily_pipeline_errors.log"

    root.mkdir(parents=True, exist_ok=True)
    persistent_output_dir.mkdir(parents=True, exist_ok=True)
    persistent_log_dir.mkdir(parents=True, exist_ok=True)

    if str(code_root) not in sys.path:
        sys.path.insert(0, str(code_root))

    log_lines: list[str] = []

    def log(message: str = "") -> None:
        print(message)
        log_lines.append(message)

    log("=" * 72)
    log("Taiwan closed-limit-up Databricks paper pipeline")
    log(f"Started: {datetime.now(CHICAGO_TZ).isoformat()}")
    log(f"Code root: {code_root}")
    log(f"Persistent root: {root}")
    log(f"Persistent DB: {persistent_db}")
    log(f"Persistent output dir: {persistent_output_dir}")
    log(f"Scratch root: {scratch_root}")
    log("Paper trading only: no real orders are submitted by this pipeline.")
    log("=" * 72)
    exit_code = 0
    scratch_db = scratch_root / "data" / "taiwan_trading.duckdb"
    scratch_output_dir = scratch_root / "reports" / "live_signals"
    scratch_logs_dir = scratch_root / "logs"
    scratch_log_file = scratch_logs_dir / f"databricks_daily_pipeline_{run_date}.log"
    scratch_error_log = scratch_logs_dir / "databricks_daily_pipeline_errors.log"
    db_sync_back_succeeded = False
    copied_report_count = 0
    copied_strategy_analysis_count = 0
    try:
        if not persistent_db.exists():
            raise FileNotFoundError(
                f"DuckDB database is missing: {persistent_db}. "
                "Upload or create the database before running the Databricks job."
            )
        scratch_db, scratch_output_dir, scratch_logs_dir = prepare_scratch_workspace(
            persistent_db=persistent_db,
            persistent_reports_dir=persistent_reports_dir,
            scratch_root=scratch_root,
        )
        scratch_log_file = scratch_logs_dir / f"databricks_daily_pipeline_{run_date}.log"
        scratch_error_log = scratch_logs_dir / "databricks_daily_pipeline_errors.log"
        log(f"Scratch DB: {scratch_db}")
        log(f"Scratch output dir: {scratch_output_dir}")
        log(f"Scratch logs dir: {scratch_logs_dir}")

        os.environ["TAIWAN_TRADING_ROOT"] = str(scratch_root)
        os.environ["TAIWAN_TRADING_DB_PATH"] = str(scratch_db)
        os.environ["TAIWAN_PAPER_TRADING_ONLY"] = "1"
        os.environ.setdefault("PYTHONUNBUFFERED", "1")
        os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
        os.environ.setdefault("TZ", "America/Chicago")

        if pipeline_func is None:
            from src.live.run_daily_closed_limit_up_pipeline import run_daily_closed_limit_up_pipeline

            pipeline_func = run_daily_closed_limit_up_pipeline

        result = pipeline_func(
            db_path=scratch_db,
            capital_twd=args.capital_twd,
            market=args.market,
            start=args.start,
            end=args.end,
            output_dir=scratch_output_dir,
            skip_data_update=args.skip_data_update,
            skip_index_update=args.skip_index_update,
            skip_sector_update=args.skip_sector_update,
            refresh_sector_map=args.refresh_sector_map,
            signal_date=args.signal_date,
            dry_run=args.dry_run,
            profile=args.profile,
            taiex_retry_delay_seconds=args.taiex_retry_delay_seconds,
        )
        full_strategy_refresh = should_run_full_strategy_analysis(args)
        if full_strategy_refresh:
            if strategy_analysis_func is None:
                from src.reports.generate_strategy_equity_analysis import run_strategy_equity_analysis

                strategy_analysis_func = run_strategy_equity_analysis
            scratch_strategy_analysis_dir = scratch_root / "reports" / "strategy_analysis"
            log("Running full historical strategy equity analysis refresh.")
            _equity, _drawdown, _monthly, _summary, strategy_report = strategy_analysis_func(
                db_path=scratch_db,
                output_dir=scratch_strategy_analysis_dir,
            )
            log(f"Full strategy equity analysis report: {strategy_report}")
        else:
            log("Full historical strategy equity analysis refresh skipped.")
        sync_scratch_db_back(scratch_db=scratch_db, persistent_db=persistent_db)
        db_sync_back_succeeded = True
        copied_report_count = copy_tree_files(scratch_output_dir, persistent_output_dir)
        copied_strategy_analysis_count = copy_tree_files(
            scratch_root / "reports" / "strategy_analysis",
            persistent_strategy_analysis_dir,
        )
        log(f"Completed: {datetime.now(CHICAGO_TZ).isoformat()}")
        log(f"Latest pipeline report: {result.report_path}")
        log(f"TAIEX freshness: {result.taiex_freshness_status}")
        log(f"Selected paper orders: {result.selected_orders}")
        log(f"DB sync-back succeeded: {db_sync_back_succeeded}")
        log(f"Copied report files back: {copied_report_count}")
        log(f"Copied strategy analysis files back: {copied_strategy_analysis_count}")
        log("Selected orders by profile:")
        for profile_name, count in result.selected_orders_by_profile.items():
            log(f"- {profile_name}: {count}")
        log(f"Persistent strategy analysis dir: {persistent_strategy_analysis_dir}")
        log(f"Scratch log file: {scratch_log_file}")
        log(f"Persistent log file: {persistent_log_file}")
    except Exception as exc:  # noqa: BLE001 - top-level runner should log full failure details.
        exit_code = 1
        failure_line = (
            f"[{datetime.now(CHICAGO_TZ).isoformat()}] FAILURE persistent_db={persistent_db} "
            f"scratch_db={scratch_db} log={scratch_log_file}: {exc}\n"
        )
        safe_append_text(scratch_error_log, failure_line)
        log("Databricks paper pipeline failed.")
        log(traceback.format_exc())
        log("Persistent DB was not overwritten because the run failed.")
        log(f"Scratch error log: {scratch_error_log}")
        log(f"Persistent error log: {persistent_error_log}")
    finally:
        try:
            log(f"Final DB sync-back succeeded: {db_sync_back_succeeded}")
            log(f"Final copied report count: {copied_report_count}")
            log(f"Final copied strategy analysis count: {copied_strategy_analysis_count}")
            safe_write_text(scratch_log_file, "\n".join(log_lines) + "\n")
            copy_tree_files(scratch_logs_dir, persistent_log_dir)
        except Exception as log_exc:  # noqa: BLE001 - stdout still carries the run status.
            print(f"Failed to write or copy Databricks log files: {log_exc}", file=sys.stderr)
            try:
                safe_append_text(
                    persistent_error_log,
                    f"[{datetime.now(CHICAGO_TZ).isoformat()}] log copy failure: {log_exc}\n",
                )
            except Exception:  # noqa: BLE001
                pass
            if exit_code == 0:
                exit_code = 1
    return exit_code


def main() -> int:
    return run_databricks_pipeline(parse_args())


def run_as_script() -> None:
    """Exit nonzero on failure, but return normally on success for Databricks."""

    exit_code = main()
    if exit_code != 0:
        raise SystemExit(exit_code)


if __name__ == "__main__":
    run_as_script()
