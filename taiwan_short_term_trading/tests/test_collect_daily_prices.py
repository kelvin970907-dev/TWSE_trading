from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from src.data_collectors import collect_daily_prices
from src.data_collectors.collect_daily_prices import (
    add_daily_price_features,
    collect_daily_range,
    collect_twse_daily_range,
    daily_price_database_summary,
    round_to_twse_tick,
    twse_tick_size,
)
from src.db import get_connection
from src.tpex_client import parse_daily_quotes_payload
from src.twse_client import parse_mi_index_payload


def sample_payload(trade_date: str, close: str, high: str | None = None, low: str | None = None) -> dict[str, Any]:
    high = high or close
    low = low or close
    return {
        "stat": "OK",
        "date": trade_date.replace("-", ""),
        "tables": [
            {
                "title": "每日收盤行情",
                "fields": [
                    "證券代號",
                    "證券名稱",
                    "成交股數",
                    "成交筆數",
                    "成交金額",
                    "開盤價",
                    "最高價",
                    "最低價",
                    "收盤價",
                ],
                "data": [
                    [
                        "2330",
                        "台積電",
                        "1,000",
                        "100",
                        "100,000",
                        "100.00",
                        high,
                        low,
                        close,
                    ]
                ],
            }
        ],
    }


def sample_tpex_payload(
    trade_date: str,
    close: str,
    high: str | None = None,
    low: str | None = None,
) -> dict[str, Any]:
    high = high or close
    low = low or close
    return {
        "stat": "OK",
        "date": trade_date.replace("-", ""),
        "tables": [
            {
                "title": "上櫃股票行情",
                "date": "113/01/02",
                "fields": [
                    "代號",
                    "名稱",
                    "收盤",
                    "漲跌",
                    "開盤",
                    "最高",
                    "最低",
                    "均價",
                    "成交股數",
                    "成交金額(元)",
                    "成交筆數",
                ],
                "data": [
                    [
                        "8069",
                        "元太",
                        close,
                        "+1.00",
                        "100.00",
                        high,
                        low,
                        close,
                        "2,000",
                        "200,000",
                        "200",
                    ]
                ],
            }
        ],
    }


def test_tick_size_and_limit_rounding_are_approximate() -> None:
    assert twse_tick_size(4.99) == 0.01
    assert twse_tick_size(8.0) == 0.05
    assert twse_tick_size(35.0) == 0.10
    assert twse_tick_size(75.0) == 0.50
    assert twse_tick_size(600.0) == 5.00
    assert round_to_twse_tick(110.2, side="floor") == 110.0
    assert round_to_twse_tick(89.8, side="ceil") == 90.0


def test_add_daily_price_features_computes_limits_and_flags() -> None:
    frame = pd.DataFrame(
        [
            {
                "symbol": "2330",
                "trade_date": "2024-01-02",
                "market": "TWSE",
                "name": "台積電",
                "open": 100.0,
                "high": 100.0,
                "low": 100.0,
                "close": 100.0,
                "volume_shares": 1_000,
                "turnover_twd": 100_000.0,
                "trades": 100,
                "source": "test",
            },
            {
                "symbol": "2330",
                "trade_date": "2024-01-03",
                "market": "TWSE",
                "name": "台積電",
                "open": 108.0,
                "high": 110.0,
                "low": 107.0,
                "close": 109.5,
                "volume_shares": 1_200,
                "turnover_twd": 130_000.0,
                "trades": 120,
                "source": "test",
            },
        ]
    )

    features = add_daily_price_features(frame)

    assert pd.isna(features.loc[0, "prev_close"])
    assert features.loc[1, "prev_close"] == 100.0
    assert features.loc[1, "daily_return"] == pytest.approx(0.095)
    assert features.loc[1, "limit_up_price"] == 110.0
    assert features.loc[1, "limit_down_price"] == 90.0
    assert bool(features.loc[1, "touched_limit_up"]) is True
    assert bool(features.loc[1, "closed_limit_up"]) is False


def test_collect_twse_daily_range_upserts_and_recomputes_features(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class FakeTWSEClient:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

        def fetch_daily_prices(
            self,
            trade_date: pd.Timestamp,
            *,
            cache_dir: Path,
            refresh_cache: bool,
        ) -> pd.DataFrame:
            query_date = pd.Timestamp(trade_date).strftime("%Y-%m-%d")
            if query_date == "2024-01-02":
                return parse_mi_index_payload(sample_payload(query_date, "100.00"))
            if query_date == "2024-01-03":
                return parse_mi_index_payload(sample_payload(query_date, "109.50", high="110.00", low="107.00"))
            return pd.DataFrame()

    monkeypatch.setattr(collect_daily_prices, "TWSEClient", FakeTWSEClient)

    db_path = tmp_path / "taiwan_trading.duckdb"
    result = collect_twse_daily_range(
        start="2024-01-02",
        end="2024-01-03",
        db_path=db_path,
        cache_dir=tmp_path / "cache",
        polite_sleep_seconds=0,
    )

    assert result["raw_rows_upserted"] == 2
    assert result["feature_rows_upserted"] == 2

    with get_connection(db_path, read_only=True) as conn:
        rows = conn.execute(
            """
            SELECT symbol, trade_date, close, prev_close, daily_return, limit_up_price, touched_limit_up
            FROM daily_prices
            ORDER BY trade_date
            """
        ).fetchall()

    assert rows[0][0:3] == ("2330", pd.Timestamp("2024-01-02").date(), 100.0)
    assert rows[1][3] == 100.0
    assert rows[1][4] == pytest.approx(0.095)
    assert rows[1][5] == 110.0
    assert rows[1][6] is True


def test_collect_daily_range_supports_both_markets(tmp_path: Path, monkeypatch) -> None:
    class FakeTWSEClient:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

        def fetch_daily_prices(
            self,
            trade_date: pd.Timestamp,
            *,
            cache_dir: Path,
            refresh_cache: bool,
        ) -> pd.DataFrame:
            query_date = pd.Timestamp(trade_date).strftime("%Y-%m-%d")
            return parse_mi_index_payload(sample_payload(query_date, "100.00"))

    class FakeTPEXClient:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

        def fetch_daily_prices(
            self,
            trade_date: pd.Timestamp,
            *,
            cache_dir: Path,
            refresh_cache: bool,
        ) -> pd.DataFrame:
            query_date = pd.Timestamp(trade_date).strftime("%Y-%m-%d")
            return parse_daily_quotes_payload(sample_tpex_payload(query_date, "200.00"))

    monkeypatch.setattr(collect_daily_prices, "TWSEClient", FakeTWSEClient)
    monkeypatch.setattr(collect_daily_prices, "TPEXClient", FakeTPEXClient)

    db_path = tmp_path / "taiwan_trading.duckdb"
    result = collect_daily_range(
        start="2024-01-02",
        end="2024-01-02",
        market="BOTH",
        db_path=db_path,
        twse_cache_dir=tmp_path / "twse_cache",
        tpex_cache_dir=tmp_path / "tpex_cache",
        polite_sleep_seconds=0,
    )

    assert result["TWSE"]["raw_rows_upserted"] == 1
    assert result["TPEX"]["raw_rows_upserted"] == 1

    with get_connection(db_path, read_only=True) as conn:
        markets = conn.execute(
            """
            SELECT market, COUNT(*) AS row_count
            FROM daily_prices
            GROUP BY market
            ORDER BY market
            """
        ).fetchall()
        summary = daily_price_database_summary(conn)

    assert markets == [("TPEX", 1), ("TWSE", 1)]
    assert list(summary["row_counts_by_market"]["market"]) == ["TPEX", "TWSE"]
    assert summary["top_10_symbols_by_rows"]["row_count"].sum() == 2
