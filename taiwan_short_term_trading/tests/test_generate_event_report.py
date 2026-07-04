from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.db import get_connection, init_db, upsert_dataframe
from src.reports.generate_event_report import generate_report


def test_generate_event_report_uses_current_daily_prices_schema(tmp_path: Path) -> None:
    db_path = tmp_path / "taiwan_trading.duckdb"
    init_db(db_path)
    with get_connection(db_path) as conn:
        upsert_dataframe(
            conn,
            "daily_prices",
            pd.DataFrame(
                [
                    {
                        "symbol": "1001",
                        "trade_date": pd.Timestamp("2024-01-02"),
                        "market": "TWSE",
                        "name": "Compat Test",
                        "open": 100.0,
                        "high": 100.0,
                        "low": 100.0,
                        "close": 100.0,
                        "volume_shares": 1000,
                        "turnover_twd": 100_000.0,
                        "source": "test",
                    },
                    {
                        "symbol": "1001",
                        "trade_date": pd.Timestamp("2024-01-03"),
                        "market": "TWSE",
                        "name": "Compat Test",
                        "open": 103.0,
                        "high": 109.0,
                        "low": 102.0,
                        "close": 108.5,
                        "volume_shares": 1000,
                        "turnover_twd": 108_500.0,
                        "source": "test",
                    },
                    {
                        "symbol": "1001",
                        "trade_date": pd.Timestamp("2024-01-04"),
                        "market": "TWSE",
                        "name": "Compat Test",
                        "open": 109.0,
                        "high": 110.0,
                        "low": 108.0,
                        "close": 109.5,
                        "volume_shares": 1000,
                        "turnover_twd": 109_500.0,
                        "source": "test",
                    },
                ]
            ),
            ["symbol", "trade_date"],
        )

    trades, summary = generate_report("is_plus_8_to_9_not_limit", db_path=db_path)

    assert len(trades) == 1
    assert trades.iloc[0]["symbol"] == "1001"
    assert trades.iloc[0]["entry_price"] == pytest.approx(109.0)
    assert trades.iloc[0]["exit_price"] == pytest.approx(109.5)
    assert "mean_return" in summary
