from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.data_collectors.collect_market_context import (
    MarketContextError,
    build_and_store_sector_daily_features,
    import_index_daily_csv,
    import_sector_map_csv,
    normalize_index_daily_frame,
    normalize_public_company_info,
    parse_twse_index_date,
    parse_twse_taiex_payload,
)
from src.db import get_connection, init_db, upsert_dataframe
from src.features.regime_filters import apply_event_context_filters


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    pd.DataFrame(rows).to_csv(path, index=False)


def test_import_index_daily_csv_computes_regime_features(tmp_path: Path) -> None:
    db_path = tmp_path / "taiwan_trading.duckdb"
    csv_path = tmp_path / "taiex.csv"
    write_csv(
        csv_path,
        [
            {"trade_date": "2024-01-01", "open": 100, "high": 101, "low": 99, "close": 100, "volume": "1,000"},
            {"trade_date": "2024-01-02", "open": 100, "high": 103, "low": 100, "close": 102, "volume": "1,100"},
            {"trade_date": "2024-01-03", "open": 102, "high": 103, "low": 100, "close": 101, "volume": "1,200"},
        ],
    )

    rows = import_index_daily_csv(db_path=db_path, csv_path=csv_path, default_index_symbol="TAIEX")

    assert rows == 3
    with get_connection(db_path, read_only=True) as conn:
        stored = conn.execute(
            """
            SELECT index_symbol, trade_date, daily_return, ma5, close_above_ma20, drawdown_from_60d_high, source
            FROM index_daily_prices
            ORDER BY trade_date
            """
        ).fetch_df()

    assert stored["index_symbol"].tolist() == ["TAIEX", "TAIEX", "TAIEX"]
    assert pd.isna(stored["daily_return"].iloc[0])
    assert stored["daily_return"].iloc[1] == pytest.approx(0.02)
    assert stored["ma5"].iloc[1] == pytest.approx(101.0)
    assert bool(stored["close_above_ma20"].iloc[1])
    assert stored["drawdown_from_60d_high"].iloc[2] == pytest.approx(101.0 / 102.0 - 1.0)
    assert stored["source"].tolist() == ["csv", "csv", "csv"]


def test_parse_twse_taiex_payload_handles_roc_dates_and_commas() -> None:
    payload = {
        "fields": ["日期", "開盤指數", "最高指數", "最低指數", "收盤指數"],
        "data": [["113/01/02", "17,900.00", "18,100.00", "17,850.00", "18,050.00"]],
    }

    parsed = parse_twse_taiex_payload(payload)

    assert parse_twse_index_date("113/01/02") == pd.Timestamp("2024-01-02")
    assert parsed.loc[0, "trade_date"] == pd.Timestamp("2024-01-02")
    assert parsed.loc[0, "open"] == "17,900.00"
    normalized = normalize_index_daily_frame(parsed, source="twse_public")
    assert normalized.loc[0, "open"] == pytest.approx(17_900.0)
    assert normalized.loc[0, "source"] == "twse_public"


def test_sector_map_import_and_sector_feature_build(tmp_path: Path) -> None:
    db_path = tmp_path / "taiwan_trading.duckdb"
    sector_csv = tmp_path / "sector.csv"
    init_db(db_path)
    write_csv(
        sector_csv,
        [
            {"symbol": "1001", "market": "TWSE", "name": "Alpha", "sector": "Tech", "industry": "Semis"},
            {"symbol": "1002", "market": "TWSE", "name": "Beta", "sector": "Tech", "industry": "Hardware"},
            {"symbol": "2001", "market": "TWSE", "name": "Gamma", "sector": "Finance", "industry": "Bank"},
        ],
    )
    with get_connection(db_path) as conn:
        upsert_dataframe(
            conn,
            "daily_prices",
            pd.DataFrame(
                [
                    daily_row("1001", "2024-01-02", 0.02, closed_limit_up=True),
                    daily_row("1002", "2024-01-02", -0.01),
                    daily_row("2001", "2024-01-02", -0.03),
                    daily_row("1001", "2024-01-03", 0.04),
                    daily_row("1002", "2024-01-03", 0.02),
                    daily_row("2001", "2024-01-03", 0.01),
                ]
            ),
            ["symbol", "trade_date"],
        )

    assert import_sector_map_csv(db_path=db_path, csv_path=sector_csv, source="test") == 3
    assert build_and_store_sector_daily_features(db_path=db_path) == 4

    with get_connection(db_path, read_only=True) as conn:
        features = conn.execute(
            """
            SELECT sector, trade_date, equal_weight_return, num_advancers, num_decliners, num_limit_up, sector_momentum_5d
            FROM sector_daily_features
            ORDER BY sector, trade_date
            """
        ).fetch_df()

    tech_day1 = features[(features["sector"] == "Tech") & (features["trade_date"] == pd.Timestamp("2024-01-02"))].iloc[0]
    assert tech_day1["equal_weight_return"] == pytest.approx(0.005)
    assert tech_day1["num_advancers"] == 1
    assert tech_day1["num_decliners"] == 1
    assert tech_day1["num_limit_up"] == 1
    assert tech_day1["sector_momentum_5d"] == pytest.approx(0.005)

    with get_connection(db_path, read_only=True) as conn:
        sector_map = conn.execute(
            """
            SELECT symbol, market, name, sector, industry, source
            FROM stock_sector_map
            ORDER BY symbol
            """
        ).fetch_df()
    assert sector_map["name"].tolist() == ["Alpha", "Beta", "Gamma"]


def test_normalize_public_company_info_maps_industry_code_to_sector() -> None:
    payload = [
        {"公司代號": "2330", "公司簡稱": "台積電", "產業別": "24"},
        {"公司代號": "1101", "公司簡稱": "台泥", "產業別": "01"},
    ]

    normalized = normalize_public_company_info(payload, market="TWSE", source="unit")

    assert normalized[["symbol", "market", "name", "sector", "industry"]].to_dict("records") == [
        {
            "symbol": "2330",
            "market": "TWSE",
            "name": "台積電",
            "sector": "Technology/Electronics",
            "industry": "Semiconductor",
        },
        {
            "symbol": "1101",
            "market": "TWSE",
            "name": "台泥",
            "sector": "Materials",
            "industry": "Cement",
        },
    ]


def test_apply_event_context_filters_market_and_sector(tmp_path: Path) -> None:
    db_path = tmp_path / "taiwan_trading.duckdb"
    init_db(db_path)
    with get_connection(db_path) as conn:
        upsert_dataframe(
            conn,
            "index_daily_prices",
            pd.DataFrame(
                [
                    {
                        "index_symbol": "TAIEX",
                        "trade_date": pd.Timestamp("2024-01-02"),
                        "open": 100.0,
                        "high": 101.0,
                        "low": 99.0,
                        "close": 101.0,
                        "daily_return": 0.01,
                        "close_above_ma20": True,
                        "close_above_ma60": False,
                    }
                ]
            ),
            ["index_symbol", "trade_date"],
        )
        upsert_dataframe(
            conn,
            "stock_sector_map",
            pd.DataFrame(
                [
                    {"symbol": "1001", "market": "TWSE", "name": "Alpha", "sector": "Tech", "industry": "Semis", "source": "test"},
                    {"symbol": "2001", "market": "TWSE", "name": "Gamma", "sector": "Finance", "industry": "Bank", "source": "test"},
                ]
            ),
            ["symbol", "market"],
        )
        upsert_dataframe(
            conn,
            "sector_daily_features",
            pd.DataFrame(
                [
                    sector_feature("Tech", "2024-01-02", 0.03, 0.05),
                    sector_feature("Finance", "2024-01-02", -0.01, -0.02),
                    sector_feature("Other", "2024-01-02", 0.01, 0.01),
                    sector_feature("Energy", "2024-01-02", 0.02, 0.02),
                ]
            ),
            ["sector", "trade_date"],
        )
        events = pd.DataFrame(
            [
                event_row("1001", "TWSE", "2024-01-02"),
                event_row("2001", "TWSE", "2024-01-02"),
            ]
        )

        market_filtered = apply_event_context_filters(conn, events, market_regime="taiex_above_20ma")
        sector_filtered = apply_event_context_filters(conn, events, sector_filter="sector_return_positive_day0")
        top_quartile = apply_event_context_filters(conn, events, sector_filter="sector_top_quartile_day0")
        ma60_filtered = apply_event_context_filters(conn, events, market_regime="taiex_above_60ma")

    assert set(market_filtered["symbol"]) == {"1001", "2001"}
    assert set(sector_filtered["symbol"]) == {"1001"}
    assert set(top_quartile["symbol"]) == {"1001"}
    assert ma60_filtered.empty


def test_sector_map_import_rejects_duplicate_keys(tmp_path: Path) -> None:
    csv_path = tmp_path / "sector.csv"
    write_csv(
        csv_path,
        [
            {"symbol": "1001", "market": "TWSE", "sector": "Tech"},
            {"symbol": "1001", "market": "TWSE", "sector": "Finance"},
        ],
    )

    with pytest.raises(MarketContextError, match="duplicate key"):
        import_sector_map_csv(db_path=tmp_path / "taiwan_trading.duckdb", csv_path=csv_path)


def daily_row(symbol: str, trade_date: str, daily_return: float, *, closed_limit_up: bool = False) -> dict[str, object]:
    close = 100.0 * (1.0 + daily_return)
    return {
        "symbol": symbol,
        "trade_date": pd.Timestamp(trade_date),
        "market": "TWSE",
        "name": f"Stock {symbol}",
        "open": 100.0,
        "high": max(100.0, close),
        "low": min(100.0, close),
        "close": close,
        "volume_shares": 1_000,
        "turnover_twd": 100_000.0,
        "daily_return": daily_return,
        "closed_limit_up": closed_limit_up,
        "source": "test",
    }


def event_row(symbol: str, market: str, trade_date: str) -> dict[str, object]:
    return {
        "event_id": f"{market}:{symbol}:{trade_date}",
        "symbol": symbol,
        "trade_date": pd.Timestamp(trade_date),
        "market": market,
        "event_type": "near_limit_8_9",
        "day0_return": 0.085,
        "day0_open": 100.0,
        "day0_high": 108.5,
        "day0_low": 100.0,
        "day0_close": 108.5,
        "day0_volume_shares": 1_000,
        "day0_turnover_twd": 100_000.0,
        "close_location": 1.0,
        "volume_ratio_20d": 2.0,
        "touched_limit_up": False,
        "closed_limit_up": False,
        "failed_limit_up": False,
        "next_trade_date": pd.Timestamp("2024-01-03"),
    }


def sector_feature(sector: str, trade_date: str, equal_return: float, momentum_5d: float) -> dict[str, object]:
    return {
        "sector": sector,
        "trade_date": pd.Timestamp(trade_date),
        "equal_weight_return": equal_return,
        "value_weight_return": equal_return,
        "num_advancers": 1 if equal_return > 0 else 0,
        "num_decliners": 1 if equal_return < 0 else 0,
        "num_limit_up": 0,
        "sector_momentum_5d": momentum_5d,
        "sector_momentum_20d": momentum_5d,
    }
