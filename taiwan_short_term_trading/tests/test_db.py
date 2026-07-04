from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
import duckdb

from src.db import DatabaseError, get_connection, init_db, upsert_dataframe


EXPECTED_COLUMNS = {
    "trading_calendar": [
        "trade_date",
        "is_open",
        "market",
        "notes",
    ],
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

EXPECTED_PRIMARY_KEYS = {
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


def test_init_db_creates_expected_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "taiwan_trading.duckdb"

    init_db(db_path)

    with get_connection(db_path, read_only=True) as conn:
        table_names = {
            row[0]
            for row in conn.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'main'
                """
            ).fetchall()
        }

        assert set(EXPECTED_COLUMNS).issubset(table_names)
        for table_name, expected_columns in EXPECTED_COLUMNS.items():
            table_info = conn.execute(f"PRAGMA table_info('{table_name}')").fetchall()
            actual_columns = [row[1] for row in table_info]
            actual_primary_keys = {row[1] for row in table_info if bool(row[5])}

            assert actual_columns == expected_columns
            assert actual_primary_keys == EXPECTED_PRIMARY_KEYS[table_name]


def test_upsert_dataframe_inserts_and_replaces_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "taiwan_trading.duckdb"
    init_db(db_path)

    first = pd.DataFrame(
        [
            {
                "symbol": "2330",
                "trade_date": pd.Timestamp("2024-01-02"),
                "market": "TWSE",
                "name": "TSMC",
                "open": 590.0,
                "high": 600.0,
                "low": 588.0,
                "close": 599.0,
                "volume_shares": 10_000_000,
                "source": "test",
            }
        ]
    )
    replacement = first.assign(close=601.0)

    with get_connection(db_path) as conn:
        assert upsert_dataframe(conn, "daily_prices", first, ["symbol", "trade_date"]) == 1
        assert upsert_dataframe(conn, "daily_prices", replacement, ["symbol", "trade_date"]) == 1

        rows = conn.execute(
            """
            SELECT symbol, trade_date, close
            FROM daily_prices
            WHERE symbol = '2330'
            """
        ).fetchall()

    assert rows == [("2330", pd.Timestamp("2024-01-02").date(), 601.0)]


def test_upsert_dataframe_rejects_missing_key_column(tmp_path: Path) -> None:
    db_path = tmp_path / "taiwan_trading.duckdb"
    init_db(db_path)
    frame = pd.DataFrame([{"symbol": "2330"}])

    with get_connection(db_path) as conn:
        with pytest.raises(DatabaseError, match="missing key columns"):
            upsert_dataframe(conn, "daily_prices", frame, ["symbol", "trade_date"])


def test_init_db_rebuilds_empty_stale_schema(tmp_path: Path) -> None:
    db_path = tmp_path / "taiwan_trading.duckdb"
    with duckdb.connect(str(db_path)) as conn:
        conn.execute(
            """
            CREATE TABLE daily_prices (
                trade_date DATE NOT NULL,
                stock_id VARCHAR NOT NULL,
                close DOUBLE,
                PRIMARY KEY (trade_date, stock_id)
            )
            """
        )

    init_db(db_path)

    with get_connection(db_path, read_only=True) as conn:
        daily_price_columns = [
            row[1] for row in conn.execute("PRAGMA table_info('daily_prices')").fetchall()
        ]

    assert daily_price_columns == EXPECTED_COLUMNS["daily_prices"]
