"""DuckDB helpers for the Taiwan trading research database."""

from __future__ import annotations

import argparse
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Iterable

import duckdb
import pandas as pd

from config.settings import get_settings


SCHEMA_PATH = Path(__file__).with_name("schema.sql")

EXPECTED_TABLE_COLUMNS: dict[str, list[str]] = {
    "trading_calendar": ["trade_date", "is_open", "market", "notes"],
    "daily_prices": [
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
    ],
    "intraday_bars": [
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
    ],
    "index_daily_prices": [
        "index_symbol",
        "trade_date",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "turnover_twd",
        "daily_return",
        "ma5",
        "ma20",
        "ma60",
        "close_above_ma20",
        "close_above_ma60",
        "drawdown_from_60d_high",
        "source",
    ],
    "stock_sector_map": ["symbol", "market", "name", "sector", "industry", "source"],
    "sector_daily_features": [
        "sector",
        "trade_date",
        "equal_weight_return",
        "value_weight_return",
        "num_advancers",
        "num_decliners",
        "num_limit_up",
        "sector_momentum_5d",
        "sector_momentum_20d",
    ],
    "institutional_flows": [
        "symbol",
        "trade_date",
        "market",
        "foreign_net_buy_twd",
        "investment_trust_net_buy_twd",
        "dealer_net_buy_twd",
        "total_institutional_net_buy_twd",
        "source",
    ],
    "margin_short": [
        "symbol",
        "trade_date",
        "market",
        "margin_buy_balance",
        "margin_sell_balance",
        "short_sale_balance",
        "day_trade_volume",
        "source",
    ],
    "event_candidates": [
        "event_id",
        "symbol",
        "trade_date",
        "market",
        "event_type",
        "day0_return",
        "day0_open",
        "day0_high",
        "day0_low",
        "day0_close",
        "day0_volume_shares",
        "day0_turnover_twd",
        "close_location",
        "volume_ratio_20d",
        "touched_limit_up",
        "closed_limit_up",
        "failed_limit_up",
        "next_trade_date",
        "foreign_net_buy_twd",
        "investment_trust_net_buy_twd",
        "dealer_net_buy_twd",
        "total_institutional_net_buy_twd",
        "foreign_net_buy_to_turnover",
        "investment_trust_net_buy_to_turnover",
        "dealer_net_buy_to_turnover",
        "total_institutional_net_buy_to_turnover",
        "margin_buy_balance",
        "margin_sell_balance",
        "short_sale_balance",
        "day_trade_volume",
        "margin_balance_change_1d",
        "short_balance_change_1d",
        "short_squeeze_proxy",
        "margin_crowding_proxy",
    ],
    "backtest_trades": [
        "trade_id",
        "strategy_name",
        "symbol",
        "market",
        "signal_date",
        "entry_date",
        "entry_time",
        "exit_date",
        "exit_time",
        "side",
        "entry_price",
        "exit_price",
        "shares",
        "gross_pnl",
        "fees",
        "tax",
        "slippage",
        "net_pnl",
        "gross_return",
        "net_return",
        "holding_minutes",
        "exit_reason",
        "metadata_json",
    ],
    "backtest_runs": [
        "run_id",
        "strategy_name",
        "started_at",
        "ended_at",
        "parameters_json",
        "metrics_json",
    ],
    "manual_fill_observations": [
        "observation_id",
        "signal_date",
        "profile_name",
        "candidate_hash",
        "symbol",
        "market",
        "name",
        "observed_time",
        "broker",
        "intended_entry_price",
        "displayed_best_bid",
        "displayed_best_ask",
        "displayed_bid_size_shares",
        "displayed_ask_size_shares",
        "limit_up_price",
        "was_limit_up_locked",
        "was_order_submitted",
        "order_type",
        "order_quantity_shares",
        "order_price",
        "simulated_queue_position",
        "actual_filled_shares",
        "actual_avg_fill_price",
        "fill_status",
        "reason_not_filled",
        "screenshot_path",
        "notes",
        "created_at",
    ],
}

EXPECTED_PRIMARY_KEYS: dict[str, set[str]] = {
    "trading_calendar": {"trade_date"},
    "daily_prices": {"symbol", "trade_date"},
    "intraday_bars": {"symbol", "bar_time"},
    "index_daily_prices": {"index_symbol", "trade_date"},
    "stock_sector_map": {"symbol", "market"},
    "sector_daily_features": {"sector", "trade_date"},
    "institutional_flows": {"symbol", "trade_date"},
    "margin_short": {"symbol", "trade_date"},
    "event_candidates": {"event_id"},
    "backtest_trades": {"trade_id"},
    "backtest_runs": {"run_id"},
    "manual_fill_observations": {"observation_id"},
}


class DatabaseError(RuntimeError):
    """Raised when a database operation cannot be completed safely."""


def get_connection(
    db_path: Path | str | None = None,
    *,
    read_only: bool = False,
) -> duckdb.DuckDBPyConnection:
    """Return a DuckDB connection for the configured local database.

    Args:
        db_path: Path to the DuckDB file. When omitted, the project setting
            `TAIWAN_TRADING_DB_PATH` or `data/taiwan_trading.duckdb` is used.
        read_only: Open the database in read-only mode.

    Raises:
        DatabaseError: If the parent directory does not exist for a read-only
            connection, or if DuckDB cannot open the file.
    """

    settings = get_settings()
    resolved_path = Path(db_path) if db_path is not None else settings.db_path

    if read_only and not resolved_path.exists():
        raise DatabaseError(f"Cannot open missing DuckDB database read-only: {resolved_path}")

    if not read_only:
        resolved_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        return duckdb.connect(str(resolved_path), read_only=read_only)
    except duckdb.Error as exc:
        raise DatabaseError(f"Failed to open DuckDB database at {resolved_path}: {exc}") from exc


def init_db(db_path: Path | str | None = None) -> Path:
    """Create the DuckDB database and all research tables if needed."""

    settings = get_settings()
    resolved_path = Path(db_path) if db_path is not None else settings.db_path

    if not SCHEMA_PATH.exists():
        raise DatabaseError(f"Schema file not found: {SCHEMA_PATH}")

    with get_connection(resolved_path) as conn:
        try:
            conn.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
            _migrate_legacy_schema(conn)
        except duckdb.Error as exc:
            if not _database_is_empty(conn):
                raise DatabaseError(
                    f"Failed to initialize schema from {SCHEMA_PATH}: {exc}"
                ) from exc
            _drop_main_tables(conn)
            try:
                conn.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
                _migrate_legacy_schema(conn)
            except duckdb.Error as rebuild_exc:
                raise DatabaseError(
                    f"Failed to rebuild empty database schema from {SCHEMA_PATH}: {rebuild_exc}"
                ) from rebuild_exc

        try:
            validate_schema(conn)
        except DatabaseError:
            if not _database_is_empty(conn):
                raise
            _drop_main_tables(conn)
            try:
                conn.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
                _migrate_legacy_schema(conn)
                validate_schema(conn)
            except duckdb.Error as exc:
                raise DatabaseError(
                    f"Failed to rebuild empty database schema from {SCHEMA_PATH}: {exc}"
                ) from exc

    return resolved_path


def _migrate_legacy_schema(conn: duckdb.DuckDBPyConnection) -> None:
    """Apply narrow schema migrations for older local DuckDB files."""

    rows = conn.execute("PRAGMA table_info('stock_sector_map')").fetchall()
    if not rows:
        return
    actual_columns = [str(row[1]) for row in rows]
    expected_columns = EXPECTED_TABLE_COLUMNS["stock_sector_map"]
    if actual_columns == expected_columns:
        return
    if set(actual_columns) == set(expected_columns):
        temp_table = "_stock_sector_map_migrated"
        conn.execute(f"DROP TABLE IF EXISTS {temp_table}")
        conn.execute(
            f"""
            CREATE TABLE {temp_table} (
                symbol TEXT NOT NULL,
                market TEXT NOT NULL,
                name TEXT,
                sector TEXT,
                industry TEXT,
                source TEXT,
                PRIMARY KEY (symbol, market)
            )
            """
        )
        conn.execute(
            f"""
            INSERT INTO {temp_table} (symbol, market, name, sector, industry, source)
            SELECT symbol, market, name, sector, industry, source
            FROM stock_sector_map
            """
        )
        conn.execute("DROP TABLE stock_sector_map")
        conn.execute(f"ALTER TABLE {temp_table} RENAME TO stock_sector_map")


def validate_schema(conn: duckdb.DuckDBPyConnection) -> None:
    """Validate that the open database matches this project's schema contract."""

    for table_name, expected_columns in EXPECTED_TABLE_COLUMNS.items():
        _quote_identifier(table_name)
        rows = conn.execute(f"PRAGMA table_info('{table_name}')").fetchall()
        if not rows:
            raise DatabaseError(f"Expected table {table_name!r} was not created")

        actual_columns = [str(row[1]) for row in rows]
        if set(actual_columns) != set(expected_columns):
            missing = [column for column in expected_columns if column not in actual_columns]
            extra = [column for column in actual_columns if column not in expected_columns]
            raise DatabaseError(
                f"Schema mismatch for table {table_name!r}. Missing columns {missing}, "
                f"extra columns {extra}. Expected columns {expected_columns}, found "
                f"{actual_columns}. If this is an older local database, migrate it or "
                "initialize a fresh DuckDB file."
            )

        actual_primary_keys = {str(row[1]) for row in rows if bool(row[5])}
        expected_primary_keys = EXPECTED_PRIMARY_KEYS[table_name]
        if actual_primary_keys != expected_primary_keys:
            raise DatabaseError(
                f"Primary key mismatch for table {table_name!r}. Expected "
                f"{sorted(expected_primary_keys)}, found {sorted(actual_primary_keys)}."
            )


def read_sql(
    sql: str,
    params: Iterable[object] | None = None,
    db_path: Path | str | None = None,
) -> pd.DataFrame:
    """Run a SQL query against the local DuckDB database."""

    with get_connection(db_path=db_path, read_only=False) as conn:
        try:
            return conn.execute(sql, list(params or [])).fetch_df()
        except duckdb.Error as exc:
            raise DatabaseError(f"SQL query failed: {exc}") from exc


def upsert_dataframe(
    conn: duckdb.DuckDBPyConnection,
    table_name: str,
    df: pd.DataFrame,
    key_columns: Sequence[str],
) -> int:
    """Upsert a pandas DataFrame into a DuckDB table.

    The operation validates that the table exists, all DataFrame columns belong
    to the table, and every key column exists in both the table and DataFrame.
    Rows matching the supplied key columns are deleted and then reinserted.
    This deliberately avoids DuckDB conflict/replace syntax and explicit
    transaction wrappers, which have been fragile on Databricks Volume-backed
    DuckDB files.

    Args:
        conn: Open DuckDB connection.
        table_name: Destination table name.
        df: Rows to upsert.
        key_columns: Columns that identify existing rows.

    Returns:
        Number of rows inserted.

    Raises:
        DatabaseError: For missing tables/columns, null keys, duplicate keys, or
            DuckDB execution failures.
        ValueError: For unsafe identifiers or empty key configuration.
    """

    if not isinstance(df, pd.DataFrame):
        raise TypeError(f"df must be a pandas DataFrame, got {type(df).__name__}")

    if df.empty:
        return 0

    safe_table = _quote_identifier(table_name)
    if isinstance(key_columns, str):
        raise TypeError("key_columns must be a sequence of column names, not a single string")

    keys = list(key_columns)
    if not keys:
        raise ValueError("key_columns must contain at least one column")
    if len(keys) != len(set(keys)):
        raise ValueError(f"key_columns contains duplicates: {keys}")

    safe_keys = [_quote_identifier(column) for column in keys]
    safe_df_columns = [_quote_identifier(column) for column in df.columns]

    table_columns = _get_table_columns(conn, table_name)
    table_column_set = set(table_columns)
    df_column_set = set(df.columns)
    key_column_set = set(keys)

    missing_df_keys = sorted(key_column_set - df_column_set)
    if missing_df_keys:
        raise DatabaseError(
            f"Cannot upsert into {table_name}: DataFrame is missing key columns {missing_df_keys}"
        )

    missing_table_keys = sorted(key_column_set - table_column_set)
    if missing_table_keys:
        raise DatabaseError(
            f"Cannot upsert into {table_name}: table is missing key columns {missing_table_keys}"
        )

    extra_columns = sorted(df_column_set - table_column_set)
    if extra_columns:
        raise DatabaseError(
            f"Cannot upsert into {table_name}: DataFrame has columns not present in table "
            f"{extra_columns}. Valid columns are {table_columns}"
        )

    null_key_counts = df[keys].isna().sum()
    null_keys = [column for column, count in null_key_counts.items() if count > 0]
    if null_keys:
        raise DatabaseError(
            f"Cannot upsert into {table_name}: key columns contain null values {null_keys}"
        )

    duplicate_count = int(df.duplicated(subset=keys).sum())
    if duplicate_count:
        raise DatabaseError(
            f"Cannot upsert into {table_name}: DataFrame contains {duplicate_count} duplicate "
            f"row(s) for key columns {keys}"
        )

    temp_view = f"_upsert_{table_name}_{uuid.uuid4().hex}"
    safe_temp_view = _quote_identifier(temp_view)
    column_sql = ", ".join(safe_df_columns)
    delete_predicate = " AND ".join(
        f"target.{safe_key} = source.{safe_key}" for safe_key in safe_keys
    )

    try:
        conn.register(temp_view, df)
        conn.execute(
            f"""
            DELETE FROM {safe_table} AS target
            WHERE EXISTS (
                SELECT 1
                FROM {safe_temp_view} AS source
                WHERE {delete_predicate}
            )
            """
        )
        conn.execute(
            f"""
            INSERT INTO {safe_table} ({column_sql})
            SELECT {column_sql}
            FROM {safe_temp_view}
            """
        )
    except duckdb.Error as exc:
        raise DatabaseError(f"Failed to upsert {len(df)} row(s) into {table_name}: {exc}") from exc
    finally:
        _unregister_quietly(conn, temp_view)

    return len(df)


def _get_table_columns(conn: duckdb.DuckDBPyConnection, table_name: str) -> list[str]:
    _quote_identifier(table_name)
    rows = conn.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'main'
          AND table_name = ?
        ORDER BY ordinal_position
        """,
        [table_name],
    ).fetchall()

    if not rows:
        raise DatabaseError(
            f"Table {table_name!r} does not exist in the DuckDB database. "
            "Run init_db(db_path) before inserting data."
        )

    return [str(row[0]) for row in rows]


def _database_is_empty(conn: duckdb.DuckDBPyConnection) -> bool:
    tables = _main_table_names(conn)
    for table_name in tables:
        safe_table = _quote_identifier(table_name)
        count = conn.execute(f"SELECT COUNT(*) FROM {safe_table}").fetchone()[0]
        if count:
            return False
    return True


def _drop_main_tables(conn: duckdb.DuckDBPyConnection) -> None:
    for table_name in _main_table_names(conn):
        safe_table = _quote_identifier(table_name)
        conn.execute(f"DROP TABLE IF EXISTS {safe_table}")


def _main_table_names(conn: duckdb.DuckDBPyConnection) -> list[str]:
    rows = conn.execute(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'main'
          AND table_type = 'BASE TABLE'
        ORDER BY table_name
        """
    ).fetchall()
    return [str(row[0]) for row in rows]


def _quote_identifier(identifier: str) -> str:
    if not isinstance(identifier, str):
        raise TypeError(f"SQL identifier must be a string, got {type(identifier).__name__}")
    if not identifier:
        raise ValueError("SQL identifier cannot be empty")
    if not identifier.replace("_", "").isalnum():
        raise ValueError(
            f"Unsafe SQL identifier {identifier!r}. Use only letters, numbers, and underscores."
        )
    return f'"{identifier}"'


def _rollback_quietly(conn: duckdb.DuckDBPyConnection) -> None:
    try:
        conn.execute("ROLLBACK")
    except duckdb.Error:
        pass


def _unregister_quietly(conn: duckdb.DuckDBPyConnection, relation_name: str) -> None:
    try:
        conn.unregister(relation_name)
    except duckdb.Error:
        pass


def connect(
    db_path: Path | str | None = None,
    read_only: bool = False,
) -> duckdb.DuckDBPyConnection:
    """Backward-compatible alias for get_connection."""

    return get_connection(db_path=db_path, read_only=read_only)


def initialize_database(db_path: Path | str | None = None) -> Path:
    """Backward-compatible alias for init_db."""

    return init_db(db_path=db_path)


def insert_dataframe(
    conn: duckdb.DuckDBPyConnection,
    table_name: str,
    frame: pd.DataFrame,
) -> int:
    """Append a DataFrame to a DuckDB table.

    This compatibility helper performs the same validation as `upsert_dataframe`
    where applicable but does not delete existing rows first.
    """

    if not isinstance(frame, pd.DataFrame):
        raise TypeError(f"frame must be a pandas DataFrame, got {type(frame).__name__}")
    if frame.empty:
        return 0

    safe_table = _quote_identifier(table_name)
    table_columns = _get_table_columns(conn, table_name)
    extra_columns = sorted(set(frame.columns) - set(table_columns))
    if extra_columns:
        raise DatabaseError(
            f"Cannot insert into {table_name}: DataFrame has columns not present in table "
            f"{extra_columns}. Valid columns are {table_columns}"
        )

    safe_columns = [_quote_identifier(column) for column in frame.columns]
    column_sql = ", ".join(safe_columns)
    temp_view = f"_insert_{table_name}_{uuid.uuid4().hex}"
    safe_temp_view = _quote_identifier(temp_view)

    try:
        conn.register(temp_view, frame)
        conn.execute(
            f"""
            INSERT INTO {safe_table} ({column_sql})
            SELECT {column_sql}
            FROM {safe_temp_view}
            """
        )
    except duckdb.Error as exc:
        raise DatabaseError(f"Failed to insert {len(frame)} row(s) into {table_name}: {exc}") from exc
    finally:
        _unregister_quietly(conn, temp_view)

    return len(frame)


def main() -> None:
    parser = argparse.ArgumentParser(description="DuckDB database utilities")
    parser.add_argument("--init", action="store_true", help="Initialize the local database")
    parser.add_argument("--db-path", type=Path, help="Optional DuckDB database path")
    args = parser.parse_args()

    if args.init:
        db_path = init_db(args.db_path)
        print(f"Initialized DuckDB database at {db_path}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
