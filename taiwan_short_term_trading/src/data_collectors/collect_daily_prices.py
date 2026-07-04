"""Collect TWSE and TPEx daily prices into DuckDB."""

from __future__ import annotations

import argparse
from collections.abc import Iterable
from pathlib import Path

import numpy as np
import pandas as pd
from tqdm import tqdm

from config.settings import get_settings
from src.db import get_connection, init_db, upsert_dataframe
from src.tpex_client import TPEXClient
from src.twse_client import DAILY_PRICE_COLUMNS, TWSEClient


DAILY_PRICE_TABLE_COLUMNS = [
    "symbol",
    "trade_date",
    "market",
    "name",
    "open",
    "high",
    "low",
    "close",
    "volume_shares",
    "turnover_twd",
    "trades",
    "prev_close",
    "daily_return",
    "limit_up_price",
    "limit_down_price",
    "touched_limit_up",
    "touched_limit_down",
    "closed_limit_up",
    "closed_limit_down",
    "source",
    "created_at",
]


def collect_twse_daily_range(
    *,
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
    db_path: Path | str,
    cache_dir: Path | str,
    refresh_cache: bool = False,
    polite_sleep_seconds: float = 0.7,
    max_retries: int = 3,
    backoff_seconds: float = 1.5,
    batch_size: int = 50,
    timeout_seconds: float | None = None,
    include_weekends: bool = False,
) -> dict[str, int]:
    """Collect TWSE daily market prices over a date range.

    Raw JSON responses are cached by date in `cache_dir`, making interrupted
    runs resumable. The database load is idempotent because rows are upserted by
    `(symbol, trade_date)`.
    """

    return collect_market_daily_range(
        market="TWSE",
        start=start,
        end=end,
        db_path=db_path,
        cache_dir=cache_dir,
        refresh_cache=refresh_cache,
        polite_sleep_seconds=polite_sleep_seconds,
        max_retries=max_retries,
        backoff_seconds=backoff_seconds,
        batch_size=batch_size,
        timeout_seconds=timeout_seconds,
        include_weekends=include_weekends,
    )


def collect_tpex_daily_range(
    *,
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
    db_path: Path | str,
    cache_dir: Path | str,
    refresh_cache: bool = False,
    polite_sleep_seconds: float = 0.7,
    max_retries: int = 3,
    backoff_seconds: float = 1.5,
    batch_size: int = 50,
    timeout_seconds: float | None = None,
    include_weekends: bool = False,
) -> dict[str, int]:
    """Collect TPEx daily market prices over a date range."""

    return collect_market_daily_range(
        market="TPEX",
        start=start,
        end=end,
        db_path=db_path,
        cache_dir=cache_dir,
        refresh_cache=refresh_cache,
        polite_sleep_seconds=polite_sleep_seconds,
        max_retries=max_retries,
        backoff_seconds=backoff_seconds,
        batch_size=batch_size,
        timeout_seconds=timeout_seconds,
        include_weekends=include_weekends,
    )


def collect_daily_range(
    *,
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
    market: str,
    db_path: Path | str,
    twse_cache_dir: Path | str,
    tpex_cache_dir: Path | str,
    refresh_cache: bool = False,
    polite_sleep_seconds: float = 0.7,
    max_retries: int = 3,
    backoff_seconds: float = 1.5,
    batch_size: int = 50,
    timeout_seconds: float | None = None,
    include_weekends: bool = False,
) -> dict[str, dict[str, int]]:
    """Collect daily prices for TWSE, TPEx, or both markets."""

    market_value = market.upper()
    if market_value not in {"TWSE", "TPEX", "BOTH"}:
        raise ValueError("market must be one of: TWSE, TPEX, BOTH")

    results: dict[str, dict[str, int]] = {}
    if market_value in {"TWSE", "BOTH"}:
        results["TWSE"] = collect_twse_daily_range(
            start=start,
            end=end,
            db_path=db_path,
            cache_dir=twse_cache_dir,
            refresh_cache=refresh_cache,
            polite_sleep_seconds=polite_sleep_seconds,
            max_retries=max_retries,
            backoff_seconds=backoff_seconds,
            batch_size=batch_size,
            timeout_seconds=timeout_seconds,
            include_weekends=include_weekends,
        )
    if market_value in {"TPEX", "BOTH"}:
        results["TPEX"] = collect_tpex_daily_range(
            start=start,
            end=end,
            db_path=db_path,
            cache_dir=tpex_cache_dir,
            refresh_cache=refresh_cache,
            polite_sleep_seconds=polite_sleep_seconds,
            max_retries=max_retries,
            backoff_seconds=backoff_seconds,
            batch_size=batch_size,
            timeout_seconds=timeout_seconds,
            include_weekends=include_weekends,
        )
    return results


def collect_market_daily_range(
    *,
    market: str,
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
    db_path: Path | str,
    cache_dir: Path | str,
    refresh_cache: bool = False,
    polite_sleep_seconds: float = 0.7,
    max_retries: int = 3,
    backoff_seconds: float = 1.5,
    batch_size: int = 50,
    timeout_seconds: float | None = None,
    include_weekends: bool = False,
) -> dict[str, int]:
    """Collect one market's daily prices over a date range."""

    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")

    market_value = market.upper()
    client = make_market_client(
        market_value,
        max_retries=max_retries,
        backoff_seconds=backoff_seconds,
        polite_sleep_seconds=polite_sleep_seconds,
        timeout_seconds=timeout_seconds,
    )

    init_db(db_path)

    frames: list[pd.DataFrame] = []
    empty_dates = 0
    raw_rows_upserted = 0
    feature_rows_upserted = 0
    dates = list(iter_collection_dates(start, end, include_weekends=include_weekends))

    for trade_date in tqdm(dates, desc=f"Collecting {market_value} daily prices"):
        frame = client.fetch_daily_prices(
            trade_date,
            cache_dir=cache_dir,
            refresh_cache=refresh_cache,
        )
        if frame.empty:
            empty_dates += 1
            continue
        frames.append(frame)
        if len(frames) >= batch_size:
            raw_rows, feature_rows = flush_daily_price_frames(
                frames,
                db_path=db_path,
                market=market_value,
            )
            raw_rows_upserted += raw_rows
            feature_rows_upserted += feature_rows
            frames.clear()

    if not frames:
        return {
            "dates_requested": len(dates),
            "empty_dates": empty_dates,
            "raw_rows_upserted": raw_rows_upserted,
            "feature_rows_upserted": feature_rows_upserted,
        }

    raw_rows, feature_rows = flush_daily_price_frames(
        frames,
        db_path=db_path,
        market=market_value,
    )
    raw_rows_upserted += raw_rows
    feature_rows_upserted += feature_rows

    return {
        "dates_requested": len(dates),
        "empty_dates": empty_dates,
        "raw_rows_upserted": raw_rows_upserted,
        "feature_rows_upserted": feature_rows_upserted,
    }


def flush_daily_price_frames(
    frames: list[pd.DataFrame],
    *,
    db_path: Path | str,
    market: str,
) -> tuple[int, int]:
    """Upsert a batch of daily-price frames and recompute dependent features."""

    if not frames:
        return 0, 0

    raw_prices = normalize_daily_price_frame(pd.concat(frames, ignore_index=True))
    if raw_prices.empty:
        return 0, 0

    with get_connection(db_path) as conn:
        raw_rows = upsert_dataframe(
            conn,
            "daily_prices",
            raw_prices[DAILY_PRICE_COLUMNS],
            ["symbol", "trade_date"],
        )
        feature_rows = recompute_daily_price_features(
            conn,
            symbols=raw_prices["symbol"].dropna().unique().tolist(),
            market=market,
        )
    return raw_rows, feature_rows


def make_market_client(
    market: str,
    *,
    max_retries: int,
    backoff_seconds: float,
    polite_sleep_seconds: float,
    timeout_seconds: float | None = None,
):
    """Create a public-data client for the requested market."""

    if market == "TWSE":
        return TWSEClient(
            max_retries=max_retries,
            backoff_seconds=backoff_seconds,
            polite_sleep_seconds=polite_sleep_seconds,
            timeout_seconds=timeout_seconds,
        )
    if market == "TPEX":
        return TPEXClient(
            max_retries=max_retries,
            backoff_seconds=backoff_seconds,
            polite_sleep_seconds=polite_sleep_seconds,
            timeout_seconds=timeout_seconds,
        )
    raise ValueError("market must be one of: TWSE, TPEX")


def iter_collection_dates(
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
    *,
    include_weekends: bool = False,
) -> Iterable[pd.Timestamp]:
    """Yield dates to request from TWSE."""

    start_ts = pd.Timestamp(start).normalize()
    end_ts = pd.Timestamp(end).normalize()
    if end_ts < start_ts:
        raise ValueError(f"end date {end_ts.date()} is before start date {start_ts.date()}")

    frequency = "D" if include_weekends else "B"
    yield from pd.date_range(start_ts, end_ts, freq=frequency)


def normalize_daily_price_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Normalize dtypes and remove duplicate source rows before DB loading."""

    missing = sorted(set(DAILY_PRICE_COLUMNS) - set(frame.columns))
    if missing:
        raise ValueError(f"daily price frame is missing required columns: {missing}")

    output = frame[DAILY_PRICE_COLUMNS].copy()
    output["symbol"] = output["symbol"].astype("string").str.strip()
    output["name"] = output["name"].astype("string").str.strip()
    output["market"] = output["market"].astype("string").str.strip().str.upper()
    output["trade_date"] = pd.to_datetime(output["trade_date"]).dt.normalize()

    numeric_columns = ["open", "high", "low", "close", "turnover_twd"]
    for column in numeric_columns:
        output[column] = pd.to_numeric(output[column], errors="coerce")

    integer_columns = ["volume_shares", "trades"]
    for column in integer_columns:
        output[column] = pd.to_numeric(output[column], errors="coerce").astype("Int64")

    output = output.dropna(subset=["symbol", "trade_date"])
    output = output.drop_duplicates(subset=["symbol", "trade_date"], keep="last")
    return output.reset_index(drop=True)


def recompute_daily_price_features(
    conn,
    *,
    symbols: list[str] | None = None,
    market: str = "TWSE",
) -> int:
    """Recompute previous close, returns, limits, and limit flags in daily_prices."""

    params: list[object] = [market]
    symbol_filter = ""
    if symbols:
        placeholders = ", ".join(["?"] * len(symbols))
        symbol_filter = f" AND symbol IN ({placeholders})"
        params.extend(symbols)

    prices = conn.execute(
        f"""
        SELECT {", ".join(DAILY_PRICE_TABLE_COLUMNS)}
        FROM daily_prices
        WHERE market = ?
        {symbol_filter}
        ORDER BY symbol, trade_date
        """,
        params,
    ).fetch_df()

    if prices.empty:
        return 0

    prices = add_daily_price_features(prices)
    return upsert_dataframe(
        conn,
        "daily_prices",
        prices[DAILY_PRICE_TABLE_COLUMNS],
        ["symbol", "trade_date"],
    )


def add_daily_price_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Compute daily return and Taiwan ±10% limit features.

    Tick-size rounding is an approximation for first-pass research. It uses the
    common Taiwan equity tick bands and rounds limit-up down to the nearest tick
    and limit-down up to the nearest tick. Exchange-provided limit prices should
    replace this for production trading.
    """

    output = frame.copy()
    output["trade_date"] = pd.to_datetime(output["trade_date"]).dt.normalize()
    output = output.sort_values(["market", "symbol", "trade_date"]).reset_index(drop=True)

    output["prev_close"] = output.groupby(["market", "symbol"])["close"].shift(1)
    output["daily_return"] = output["close"] / output["prev_close"] - 1.0

    raw_limit_up = output["prev_close"] * 1.10
    raw_limit_down = output["prev_close"] * 0.90
    output["limit_up_price"] = raw_limit_up.map(lambda price: round_to_twse_tick(price, side="floor"))
    output["limit_down_price"] = raw_limit_down.map(lambda price: round_to_twse_tick(price, side="ceil"))

    output["touched_limit_up"] = (
        output["limit_up_price"].notna()
        & output["high"].notna()
        & (output["high"] >= output["limit_up_price"])
    )
    output["touched_limit_down"] = (
        output["limit_down_price"].notna()
        & output["low"].notna()
        & (output["low"] <= output["limit_down_price"])
    )
    output["closed_limit_up"] = (
        output["limit_up_price"].notna()
        & output["close"].notna()
        & (output["close"] >= output["limit_up_price"])
    )
    output["closed_limit_down"] = (
        output["limit_down_price"].notna()
        & output["close"].notna()
        & (output["close"] <= output["limit_down_price"])
    )

    return output


def twse_tick_size(price: float | int | None) -> float:
    """Approximate TWSE stock tick size for first-pass research."""

    if price is None or pd.isna(price):
        return np.nan

    value = float(price)
    if value < 5:
        return 0.01
    if value < 10:
        return 0.05
    if value < 50:
        return 0.10
    if value < 100:
        return 0.50
    if value < 500:
        return 1.00
    return 5.00


def round_to_twse_tick(price: float | int | None, *, side: str) -> float:
    """Round a price to the approximate TWSE tick grid."""

    if price is None or pd.isna(price):
        return np.nan

    tick = twse_tick_size(price)
    value = float(price)
    if side == "floor":
        return round(np.floor((value + 1e-12) / tick) * tick, 4)
    if side == "ceil":
        return round(np.ceil((value - 1e-12) / tick) * tick, 4)
    raise ValueError("side must be 'floor' or 'ceil'")


def daily_price_database_summary(conn) -> dict[str, pd.DataFrame]:
    """Return row counts, date ranges, and top symbols for loaded daily prices."""

    row_counts = conn.execute(
        """
        SELECT market, COUNT(*) AS row_count
        FROM daily_prices
        GROUP BY market
        ORDER BY market
        """
    ).fetch_df()
    date_ranges = conn.execute(
        """
        SELECT
            market,
            MIN(trade_date) AS start_date,
            MAX(trade_date) AS end_date,
            COUNT(DISTINCT trade_date) AS trading_days
        FROM daily_prices
        GROUP BY market
        ORDER BY market
        """
    ).fetch_df()
    top_symbols = conn.execute(
        """
        SELECT
            market,
            symbol,
            ANY_VALUE(name) AS name,
            COUNT(*) AS row_count,
            MIN(trade_date) AS start_date,
            MAX(trade_date) AS end_date
        FROM daily_prices
        GROUP BY market, symbol
        ORDER BY row_count DESC, market, symbol
        LIMIT 10
        """
    ).fetch_df()
    return {
        "row_counts_by_market": row_counts,
        "date_range_by_market": date_ranges,
        "top_10_symbols_by_rows": top_symbols,
    }


def print_daily_price_database_summary(db_path: Path | str) -> None:
    with get_connection(db_path, read_only=True) as conn:
        summary = daily_price_database_summary(conn)

    print("\nrow counts by market")
    print(_format_summary_frame(summary["row_counts_by_market"]))
    print("\ndate range by market")
    print(_format_summary_frame(summary["date_range_by_market"]))
    print("\ntop 10 symbols by number of rows")
    print(_format_summary_frame(summary["top_10_symbols_by_rows"]))


def _format_summary_frame(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "(no rows)"
    return frame.to_string(index=False)


def default_twse_cache_dir() -> Path:
    return get_settings().raw_data_dir / "twse_daily"


def default_tpex_cache_dir() -> Path:
    return get_settings().raw_data_dir / "tpex_daily"


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect TWSE/TPEx daily prices into DuckDB")
    parser.add_argument("--start", required=True, help="Start date, YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="End date, YYYY-MM-DD")
    parser.add_argument("--market", default="TWSE", choices=["TWSE", "TPEX", "BOTH"])
    parser.add_argument("--db", type=Path, default=get_settings().db_path, help="DuckDB path")
    parser.add_argument(
        "--twse-cache-dir",
        type=Path,
        default=default_twse_cache_dir(),
        help="Raw TWSE JSON cache directory",
    )
    parser.add_argument(
        "--tpex-cache-dir",
        type=Path,
        default=default_tpex_cache_dir(),
        help="Raw TPEx JSON cache directory",
    )
    parser.add_argument("--refresh-cache", action="store_true", help="Refetch even when raw cache exists")
    parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=0.7,
        help="Minimum seconds between network requests",
    )
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--backoff-seconds", type=float, default=1.5)
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=None,
        help="HTTP read/connect timeout per request; defaults to config setting",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=50,
        help="Number of non-empty trade-date frames to upsert per database batch",
    )
    parser.add_argument(
        "--include-weekends",
        action="store_true",
        help="Request every calendar day instead of weekdays only",
    )
    args = parser.parse_args()

    result = collect_daily_range(
        start=args.start,
        end=args.end,
        market=args.market,
        db_path=args.db,
        twse_cache_dir=args.twse_cache_dir,
        tpex_cache_dir=args.tpex_cache_dir,
        refresh_cache=args.refresh_cache,
        polite_sleep_seconds=args.sleep_seconds,
        max_retries=args.max_retries,
        backoff_seconds=args.backoff_seconds,
        batch_size=args.batch_size,
        timeout_seconds=args.timeout_seconds,
        include_weekends=args.include_weekends,
    )

    print("Daily collection complete")
    for market, market_result in result.items():
        print(f"\n{market}")
        for key, value in market_result.items():
            print(f"{key}: {value}")

    print_daily_price_database_summary(args.db)


if __name__ == "__main__":
    main()
