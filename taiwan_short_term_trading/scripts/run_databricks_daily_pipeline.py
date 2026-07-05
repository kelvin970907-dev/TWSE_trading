#!/usr/bin/env python3
"""Databricks entry point for the Taiwan paper-trading daily pipeline.

This script is intentionally thin: it configures the runtime root, validates
that the DuckDB file exists, redirects logs under the configured root, then
calls the same Python pipeline used by local launchd.
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


CODE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABRICKS_ROOT = Path("/dbfs/FileStore/taiwan_trading")
CHICAGO_TZ = ZoneInfo("America/Chicago")


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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Taiwan paper pipeline on Databricks")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(os.getenv("TAIWAN_TRADING_ROOT", DEFAULT_DATABRICKS_ROOT)),
        help="Runtime root for data, reports, and logs. Examples: /dbfs/FileStore/taiwan_trading or a UC Volume path.",
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
    parser.add_argument("--taiex-retry-delay-seconds", type=float, default=30.0)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.root.expanduser()
    db_path = (args.db if args.db is not None else root / "data" / "taiwan_trading.duckdb").expanduser()
    output_dir = root / "reports" / "live_signals"
    log_dir = root / "logs"
    run_date = datetime.now(CHICAGO_TZ).strftime("%Y-%m-%d")
    log_file = log_dir / f"databricks_daily_pipeline_{run_date}.log"
    error_log = log_dir / "databricks_daily_pipeline_errors.log"

    root.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)

    os.environ["TAIWAN_TRADING_ROOT"] = str(root)
    os.environ["TAIWAN_TRADING_DB_PATH"] = str(db_path)
    os.environ["TAIWAN_PAPER_TRADING_ONLY"] = "1"
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    os.environ.setdefault("TZ", "America/Chicago")

    if str(CODE_ROOT) not in sys.path:
        sys.path.insert(0, str(CODE_ROOT))

    log_lines: list[str] = []

    def log(message: str = "") -> None:
        print(message)
        log_lines.append(message)

    log("=" * 72)
    log("Taiwan closed-limit-up Databricks paper pipeline")
    log(f"Started: {datetime.now(CHICAGO_TZ).isoformat()}")
    log(f"Code root: {CODE_ROOT}")
    log(f"Runtime root: {root}")
    log(f"Database: {db_path}")
    log(f"Output dir: {output_dir}")
    log("Paper trading only: no real orders are submitted by this pipeline.")
    log("=" * 72)
    exit_code = 0
    try:
        if not db_path.exists():
            raise FileNotFoundError(
                f"DuckDB database is missing: {db_path}. "
                "Upload or create the database before running the Databricks job."
            )
        from src.live.run_daily_closed_limit_up_pipeline import run_daily_closed_limit_up_pipeline

        result = run_daily_closed_limit_up_pipeline(
            db_path=db_path,
            capital_twd=args.capital_twd,
            market=args.market,
            start=args.start,
            end=args.end,
            output_dir=output_dir,
            skip_data_update=args.skip_data_update,
            skip_index_update=args.skip_index_update,
            skip_sector_update=args.skip_sector_update,
            refresh_sector_map=args.refresh_sector_map,
            signal_date=args.signal_date,
            dry_run=args.dry_run,
            profile=args.profile,
            taiex_retry_delay_seconds=args.taiex_retry_delay_seconds,
        )
        log(f"Completed: {datetime.now(CHICAGO_TZ).isoformat()}")
        log(f"Latest pipeline report: {result.report_path}")
        log(f"TAIEX freshness: {result.taiex_freshness_status}")
        log(f"Selected paper orders: {result.selected_orders}")
        log("Selected orders by profile:")
        for profile_name, count in result.selected_orders_by_profile.items():
            log(f"- {profile_name}: {count}")
        log(f"Log file: {log_file}")
    except Exception as exc:  # noqa: BLE001 - top-level runner should log full failure details.
        exit_code = 1
        failure_line = f"[{datetime.now(CHICAGO_TZ).isoformat()}] FAILURE db={db_path} log={log_file}: {exc}\n"
        safe_append_text(error_log, failure_line)
        log("Databricks paper pipeline failed.")
        log(traceback.format_exc())
        log(f"Error log: {error_log}")
    finally:
        try:
            safe_write_text(log_file, "\n".join(log_lines) + "\n")
        except Exception as log_exc:  # noqa: BLE001 - stdout still carries the run status.
            print(f"Failed to write Databricks log file {log_file}: {log_exc}", file=sys.stderr)
            if exit_code == 0:
                exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
