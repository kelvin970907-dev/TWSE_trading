#!/usr/bin/env python3
"""Databricks entry point for the Taiwan paper-trading daily pipeline.

This script is intentionally thin: it configures the runtime root, validates
that the DuckDB file exists, redirects logs under the configured root, then
calls the same Python pipeline used by local launchd.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


CODE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABRICKS_ROOT = Path("/dbfs/FileStore/taiwan_trading")
CHICAGO_TZ = ZoneInfo("America/Chicago")


class Tee:
    """Write text to multiple file-like streams."""

    def __init__(self, *streams):
        self.streams = streams

    def write(self, text: str) -> int:
        for stream in self.streams:
            stream.write(text)
            stream.flush()
        return len(text)

    def flush(self) -> None:
        for stream in self.streams:
            stream.flush()


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

    with log_file.open("a", encoding="utf-8") as log_handle:
        tee_stdout = Tee(sys.stdout, log_handle)
        tee_stderr = Tee(sys.stderr, log_handle)
        with contextlib.redirect_stdout(tee_stdout), contextlib.redirect_stderr(tee_stderr):
            print("=" * 72)
            print("Taiwan closed-limit-up Databricks paper pipeline")
            print(f"Started: {datetime.now(CHICAGO_TZ).isoformat()}")
            print(f"Code root: {CODE_ROOT}")
            print(f"Runtime root: {root}")
            print(f"Database: {db_path}")
            print(f"Output dir: {output_dir}")
            print("Paper trading only: no real orders are submitted by this pipeline.")
            print("=" * 72)
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
                print(f"Completed: {datetime.now(CHICAGO_TZ).isoformat()}")
                print(f"Latest pipeline report: {result.report_path}")
                print(f"TAIEX freshness: {result.taiex_freshness_status}")
                print(f"Selected paper orders: {result.selected_orders}")
                print("Selected orders by profile:")
                for profile_name, count in result.selected_orders_by_profile.items():
                    print(f"- {profile_name}: {count}")
                print(f"Log file: {log_file}")
                return 0
            except Exception as exc:  # noqa: BLE001 - top-level runner should log full failure details.
                with error_log.open("a", encoding="utf-8") as error_handle:
                    error_handle.write(
                        f"[{datetime.now(CHICAGO_TZ).isoformat()}] FAILURE db={db_path} log={log_file}: {exc}\n"
                    )
                print("Databricks paper pipeline failed.")
                print(traceback.format_exc())
                print(f"Error log: {error_log}")
                return 1


if __name__ == "__main__":
    raise SystemExit(main())
