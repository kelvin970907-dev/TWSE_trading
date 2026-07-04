"""Collect or import intraday bars into DuckDB.

Broad historical 1-minute TWSE/TPEx stock bars are not exposed through a
stable project-supported public endpoint yet, so the production path is vendor
or user-provided CSV import. The public-download command is intentionally
adapter-based and raises a helpful error until a concrete source is added.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from tqdm.auto import tqdm

from config.settings import get_settings
from src.db import get_connection, init_db, upsert_dataframe


EXPECTED_CSV_COLUMNS = [
    "symbol",
    "market",
    "bar_time",
    "open",
    "high",
    "low",
    "close",
    "volume_shares",
    "turnover_twd",
]

INTRADAY_TABLE_COLUMNS = [
    "symbol",
    "trade_date",
    "market",
    "bar_time",
    "open",
    "high",
    "low",
    "close",
    "volume_shares",
    "turnover_twd",
    "source",
]

PRICE_COLUMNS = ["open", "high", "low", "close"]
TAIWAN_SESSION_START = "09:00"
TAIWAN_SESSION_END = "13:30"


class IntradayImportError(ValueError):
    """Raised when intraday input data fails validation."""


class PublicIntradayUnavailableError(RuntimeError):
    """Raised when a public historical intraday adapter is requested."""


def import_intraday_csv(
    *,
    db_path: Path | str,
    csv_path: Path | str,
    source: str | None = None,
    session_start: str = TAIWAN_SESSION_START,
    session_end: str = TAIWAN_SESSION_END,
) -> tuple[int, pd.DataFrame]:
    """Import one CSV file of 1-minute intraday bars."""

    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(f"Intraday CSV not found: {path}")
    frame = read_intraday_csv(path)
    normalized = normalize_intraday_frame(
        frame,
        source=source or f"csv:{path.name}",
        session_start=session_start,
        session_end=session_end,
    )
    rows = store_intraday_bars(db_path=db_path, bars=normalized)
    with get_connection(db_path, read_only=True) as conn:
        coverage = build_coverage_report(conn, normalized)
    return rows, coverage


def import_intraday_folder(
    *,
    db_path: Path | str,
    folder: Path | str,
    pattern: str = "*.csv",
    recursive: bool = False,
    session_start: str = TAIWAN_SESSION_START,
    session_end: str = TAIWAN_SESSION_END,
) -> tuple[int, pd.DataFrame]:
    """Import all CSV files from a folder as one validated batch."""

    folder_path = Path(folder)
    if not folder_path.exists() or not folder_path.is_dir():
        raise FileNotFoundError(f"Intraday folder not found: {folder_path}")

    csv_paths = sorted(folder_path.rglob(pattern) if recursive else folder_path.glob(pattern))
    csv_paths = [path for path in csv_paths if path.is_file()]
    if not csv_paths:
        raise IntradayImportError(f"No CSV files matched {pattern!r} in {folder_path}")

    frames: list[pd.DataFrame] = []
    for csv_path in tqdm(csv_paths, desc="Reading intraday CSV files"):
        raw = read_intraday_csv(csv_path)
        frames.append(
            normalize_intraday_frame(
                raw,
                source=f"csv:{csv_path.name}",
                session_start=session_start,
                session_end=session_end,
            )
        )

    combined = pd.concat(frames, ignore_index=True)
    validate_no_duplicate_keys(combined)
    rows = store_intraday_bars(db_path=db_path, bars=combined)
    with get_connection(db_path, read_only=True) as conn:
        coverage = build_coverage_report(conn, combined)
    return rows, coverage


def public_download_intraday(
    *,
    db_path: Path | str,
    market: str,
    symbols: Sequence[str],
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
    provider: str = "official",
) -> tuple[int, pd.DataFrame]:
    """Placeholder for future public historical intraday adapters.

    Taiwan official public endpoints are useful for daily and current/session
    data, but this project does not currently have a reliable broad historical
    1-minute TWSE/TPEx source adapter. Keeping this as an explicit mode makes
    the CLI stable while avoiding accidental use of incomplete unofficial data.
    """

    del db_path, market, symbols, start, end
    raise PublicIntradayUnavailableError(
        f"No public historical 1-minute intraday adapter is configured for provider={provider!r}. "
        "Use `import-csv` or `import-folder` with vendor/exported data, or add a provider-specific "
        "adapter that returns the expected intraday schema."
    )


def read_intraday_csv(path: Path) -> pd.DataFrame:
    """Read a CSV and normalize its column names."""

    frame = pd.read_csv(path)
    frame.columns = [str(column).strip().lower() for column in frame.columns]
    missing = sorted(set(EXPECTED_CSV_COLUMNS) - set(frame.columns))
    if missing:
        raise IntradayImportError(
            f"{path} is missing required intraday column(s): {missing}. "
            f"Expected columns are {EXPECTED_CSV_COLUMNS}"
        )
    return frame[EXPECTED_CSV_COLUMNS].copy()


def normalize_intraday_frame(
    frame: pd.DataFrame,
    *,
    source: str,
    session_start: str = TAIWAN_SESSION_START,
    session_end: str = TAIWAN_SESSION_END,
) -> pd.DataFrame:
    """Normalize and validate intraday bars before database insertion."""

    missing = sorted(set(EXPECTED_CSV_COLUMNS) - set(frame.columns))
    if missing:
        raise IntradayImportError(f"intraday data is missing required column(s): {missing}")

    output = frame[EXPECTED_CSV_COLUMNS].copy()
    output["symbol"] = output["symbol"].astype("string").str.strip()
    output["market"] = output["market"].map(normalize_market_value)
    output["bar_time"] = parse_bar_times(output["bar_time"])
    output["trade_date"] = output["bar_time"].dt.date

    for column in PRICE_COLUMNS:
        output[column] = parse_numeric_column(output[column], column=column)
    output["volume_shares"] = parse_numeric_column(output["volume_shares"], column="volume_shares").round()
    output["turnover_twd"] = parse_numeric_column(output["turnover_twd"], column="turnover_twd")

    output["source"] = source
    output = output[INTRADAY_TABLE_COLUMNS]
    validate_intraday_bars(output, session_start=session_start, session_end=session_end)
    output["volume_shares"] = output["volume_shares"].astype("Int64")
    return output.reset_index(drop=True)


def parse_bar_times(values: pd.Series) -> pd.Series:
    parsed = pd.to_datetime(values, errors="coerce")
    if parsed.isna().any():
        bad_examples = values[parsed.isna()].head(5).astype(str).tolist()
        raise IntradayImportError(f"bar_time contains unparsable value(s), examples: {bad_examples}")

    if isinstance(parsed.dtype, pd.DatetimeTZDtype):
        parsed = parsed.dt.tz_convert("Asia/Taipei").dt.tz_localize(None)
    return parsed.dt.floor("s")


def parse_numeric_column(values: pd.Series, *, column: str) -> pd.Series:
    cleaned = (
        values.astype("string")
        .str.strip()
        .str.replace(",", "", regex=False)
        .replace({"": pd.NA, "--": pd.NA, "nan": pd.NA, "None": pd.NA})
    )
    numeric = pd.to_numeric(cleaned, errors="coerce")
    if numeric.isna().any():
        bad_examples = values[numeric.isna()].head(5).astype(str).tolist()
        raise IntradayImportError(f"{column} contains missing or non-numeric value(s), examples: {bad_examples}")
    return numeric.astype("float64")


def normalize_market_value(value: Any) -> str:
    market = str(value).upper().strip()
    mapping = {
        "TWSE": "TWSE",
        "TSE": "TWSE",
        "TPEX": "TPEX",
        "TPE": "TPEX",
        "OTC": "TPEX",
        "TPEx".upper(): "TPEX",
    }
    if market not in mapping:
        raise IntradayImportError(f"Unsupported market {value!r}; expected TWSE or TPEX")
    return mapping[market]


def validate_intraday_bars(
    bars: pd.DataFrame,
    *,
    session_start: str = TAIWAN_SESSION_START,
    session_end: str = TAIWAN_SESSION_END,
) -> None:
    """Validate bars before writing to DuckDB."""

    if bars.empty:
        raise IntradayImportError("intraday import produced no rows")

    required = sorted(set(INTRADAY_TABLE_COLUMNS) - set(bars.columns))
    if required:
        raise IntradayImportError(f"intraday bars are missing required column(s): {required}")

    null_required = bars[["symbol", "market", "bar_time", "trade_date"]].isna().sum()
    null_columns = [column for column, count in null_required.items() if count > 0]
    if null_columns:
        raise IntradayImportError(f"intraday bars contain null required fields: {null_columns}")

    blank_symbols = bars["symbol"].astype("string").str.len().fillna(0) == 0
    if blank_symbols.any():
        raise IntradayImportError("symbol contains blank value(s)")

    validate_no_duplicate_keys(bars)
    validate_price_and_volume_values(bars)
    validate_session_times(bars, session_start=session_start, session_end=session_end)


def validate_no_duplicate_keys(bars: pd.DataFrame) -> None:
    duplicate_mask = bars.duplicated(subset=["symbol", "bar_time"], keep=False)
    if duplicate_mask.any():
        examples = (
            bars.loc[duplicate_mask, ["symbol", "bar_time"]]
            .head(5)
            .assign(bar_time=lambda frame: frame["bar_time"].astype(str))
            .to_dict("records")
        )
        raise IntradayImportError(
            f"intraday data contains duplicate symbol/bar_time rows; examples: {examples}"
        )


def validate_price_and_volume_values(bars: pd.DataFrame) -> None:
    for column in PRICE_COLUMNS:
        invalid = ~np.isfinite(bars[column]) | (bars[column] <= 0)
        if invalid.any():
            examples = bars.loc[invalid, ["symbol", "bar_time", column]].head(5).to_dict("records")
            raise IntradayImportError(f"{column} must be positive for every bar; examples: {examples}")

    negative_volume = bars["volume_shares"] < 0
    if negative_volume.any():
        examples = bars.loc[negative_volume, ["symbol", "bar_time", "volume_shares"]].head(5).to_dict("records")
        raise IntradayImportError(f"volume_shares cannot be negative; examples: {examples}")

    negative_turnover = bars["turnover_twd"] < 0
    if negative_turnover.any():
        examples = bars.loc[negative_turnover, ["symbol", "bar_time", "turnover_twd"]].head(5).to_dict("records")
        raise IntradayImportError(f"turnover_twd cannot be negative; examples: {examples}")

    high_too_low = bars["high"] < bars[["open", "low", "close"]].max(axis=1)
    low_too_high = bars["low"] > bars[["open", "high", "close"]].min(axis=1)
    if high_too_low.any() or low_too_high.any():
        bad = bars.loc[high_too_low | low_too_high, ["symbol", "bar_time", "open", "high", "low", "close"]]
        raise IntradayImportError(f"OHLC values are inconsistent; examples: {bad.head(5).to_dict('records')}")


def validate_session_times(
    bars: pd.DataFrame,
    *,
    session_start: str = TAIWAN_SESSION_START,
    session_end: str = TAIWAN_SESSION_END,
) -> None:
    start_time = parse_session_time(session_start, name="session_start")
    end_time = parse_session_time(session_end, name="session_end")
    if end_time < start_time:
        raise IntradayImportError("session_end must be after session_start")

    local_times = bars["bar_time"].dt.time
    in_session = local_times.map(lambda value: start_time <= value <= end_time)
    if not in_session.all():
        examples = (
            bars.loc[~in_session, ["symbol", "bar_time"]]
            .head(5)
            .assign(bar_time=lambda frame: frame["bar_time"].astype(str))
            .to_dict("records")
        )
        raise IntradayImportError(
            f"bar_time must be within Taiwan regular session {session_start}-{session_end}; examples: {examples}"
        )


def parse_session_time(value: str, *, name: str):
    parsed = pd.to_datetime(value, format="%H:%M", errors="coerce")
    if pd.isna(parsed):
        raise IntradayImportError(f"{name} must use HH:MM format, got {value!r}")
    return parsed.time()


def store_intraday_bars(*, db_path: Path | str, bars: pd.DataFrame) -> int:
    init_db(db_path)
    with get_connection(db_path) as conn:
        return upsert_dataframe(
            conn,
            "intraday_bars",
            bars[INTRADAY_TABLE_COLUMNS],
            ["symbol", "bar_time"],
        )


def build_coverage_report(conn, bars: pd.DataFrame) -> pd.DataFrame:
    """Build coverage by market/symbol for the imported batch."""

    if bars.empty:
        return empty_coverage_report()

    rows: list[dict[str, Any]] = []
    for (market, symbol), group in bars.groupby(["market", "symbol"], dropna=False):
        observed_dates = {pd.Timestamp(value).date() for value in group["trade_date"].dropna().unique()}
        start_date = min(observed_dates)
        end_date = max(observed_dates)
        expected_dates = expected_open_dates(conn, market=str(market), start=start_date, end=end_date)
        missing_dates = sorted(expected_dates - observed_dates)
        rows.append(
            {
                "market": market,
                "symbol": symbol,
                "rows": int(len(group)),
                "start_date": start_date,
                "end_date": end_date,
                "first_bar_time": group["bar_time"].min(),
                "last_bar_time": group["bar_time"].max(),
                "observed_days": len(observed_dates),
                "expected_days": len(expected_dates),
                "missing_days": len(missing_dates),
                "missing_day_list": ",".join(date.isoformat() for date in missing_dates[:20]),
            }
        )
    return pd.DataFrame(rows, columns=coverage_report_columns()).sort_values(["market", "symbol"]).reset_index(drop=True)


def expected_open_dates(conn, *, market: str, start, end) -> set:
    calendar_dates = load_calendar_open_dates(conn, market=market, start=start, end=end)
    if calendar_dates:
        return calendar_dates
    return {ts.date() for ts in pd.bdate_range(start=start, end=end)}


def load_calendar_open_dates(conn, *, market: str, start, end) -> set:
    try:
        rows = conn.execute(
            """
            SELECT trade_date
            FROM trading_calendar
            WHERE is_open = TRUE
              AND trade_date >= ?
              AND trade_date <= ?
              AND (market IS NULL OR market = '' OR UPPER(market) = ?)
            ORDER BY trade_date
            """,
            [start, end, market.upper()],
        ).fetchall()
    except Exception:
        return set()
    return {row[0] for row in rows}


def coverage_report_columns() -> list[str]:
    return [
        "market",
        "symbol",
        "rows",
        "start_date",
        "end_date",
        "first_bar_time",
        "last_bar_time",
        "observed_days",
        "expected_days",
        "missing_days",
        "missing_day_list",
    ]


def empty_coverage_report() -> pd.DataFrame:
    return pd.DataFrame(columns=coverage_report_columns())


def print_coverage_report(coverage: pd.DataFrame) -> None:
    if coverage.empty:
        print("No intraday rows imported.")
        return

    total_rows = int(coverage["rows"].sum())
    symbols = int(coverage[["market", "symbol"]].drop_duplicates().shape[0])
    start_date = coverage["start_date"].min()
    end_date = coverage["end_date"].max()
    missing_days = int(coverage["missing_days"].sum())
    print("\nintraday coverage")
    print(f"symbols: {symbols}")
    print(f"date_range: {start_date} to {end_date}")
    print(f"rows: {total_rows}")
    print(f"missing_days: {missing_days}")
    print("\ncoverage by symbol")
    print(coverage.to_string(index=False))


def iter_csv_files(folder: Path, *, pattern: str = "*.csv", recursive: bool = False) -> Iterable[Path]:
    return sorted(folder.rglob(pattern) if recursive else folder.glob(pattern))


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect or import TWSE/TPEx intraday bars")
    subparsers = parser.add_subparsers(dest="command", required=True)

    csv_parser = subparsers.add_parser("import-csv", help="Import one vendor/exported intraday CSV")
    csv_parser.add_argument("--db", type=Path, default=get_settings().db_path, help="DuckDB path")
    csv_parser.add_argument("--csv", type=Path, required=True, help="CSV path")
    csv_parser.add_argument("--source", help="Optional source label stored with each row")
    csv_parser.add_argument("--session-start", default=TAIWAN_SESSION_START)
    csv_parser.add_argument("--session-end", default=TAIWAN_SESSION_END)

    folder_parser = subparsers.add_parser("import-folder", help="Import all intraday CSVs from a folder")
    folder_parser.add_argument("--db", type=Path, default=get_settings().db_path, help="DuckDB path")
    folder_parser.add_argument("--folder", type=Path, required=True, help="Folder containing CSV files")
    folder_parser.add_argument("--pattern", default="*.csv", help="Glob pattern, default: *.csv")
    folder_parser.add_argument("--recursive", action="store_true", help="Search folders recursively")
    folder_parser.add_argument("--session-start", default=TAIWAN_SESSION_START)
    folder_parser.add_argument("--session-end", default=TAIWAN_SESSION_END)

    public_parser = subparsers.add_parser(
        "public-download",
        help="Adapter placeholder for public historical intraday sources",
    )
    public_parser.add_argument("--db", type=Path, default=get_settings().db_path, help="DuckDB path")
    public_parser.add_argument("--market", choices=["TWSE", "TPEX"], required=True)
    public_parser.add_argument("--symbols", nargs="+", required=True)
    public_parser.add_argument("--start", required=True)
    public_parser.add_argument("--end", required=True)
    public_parser.add_argument("--provider", default="official")

    args = parser.parse_args()

    if args.command == "import-csv":
        rows, coverage = import_intraday_csv(
            db_path=args.db,
            csv_path=args.csv,
            source=args.source,
            session_start=args.session_start,
            session_end=args.session_end,
        )
        print(f"Imported {rows} intraday row(s) from {args.csv}")
        print_coverage_report(coverage)
        return

    if args.command == "import-folder":
        rows, coverage = import_intraday_folder(
            db_path=args.db,
            folder=args.folder,
            pattern=args.pattern,
            recursive=args.recursive,
            session_start=args.session_start,
            session_end=args.session_end,
        )
        print(f"Imported {rows} intraday row(s) from {args.folder}")
        print_coverage_report(coverage)
        return

    if args.command == "public-download":
        try:
            rows, coverage = public_download_intraday(
                db_path=args.db,
                market=args.market,
                symbols=args.symbols,
                start=args.start,
                end=args.end,
                provider=args.provider,
            )
        except PublicIntradayUnavailableError as exc:
            parser.exit(status=2, message=f"{exc}\n")
        print(f"Downloaded {rows} intraday row(s) from provider={args.provider}")
        print_coverage_report(coverage)
        return


if __name__ == "__main__":
    main()
