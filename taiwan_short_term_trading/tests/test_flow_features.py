from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.data_collectors.collect_institutional_flows import (
    InstitutionalFlowImportError,
    import_institutional_flows_csv,
    read_institutional_flows_csv,
)
from src.data_collectors.collect_margin_short import import_margin_short_csv, read_margin_short_csv
from src.db import get_connection, init_db
from src.features.flow_features import add_event_flow_features, apply_flow_feature_filters


def synthetic_events() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "event_id": "TWSE:1001:20240131:near_limit_8_9:test",
                "symbol": "1001",
                "trade_date": pd.Timestamp("2024-01-31"),
                "market": "TWSE",
                "event_type": "near_limit_8_9",
                "day0_volume_shares": 10_000,
                "day0_turnover_twd": 1_000_000.0,
            },
            {
                "event_id": "TWSE:1002:20240131:near_limit_9_10:test",
                "symbol": "1002",
                "trade_date": pd.Timestamp("2024-01-31"),
                "market": "TWSE",
                "event_type": "near_limit_9_10",
                "day0_volume_shares": 10_000,
                "day0_turnover_twd": 1_000_000.0,
            },
        ]
    )


def synthetic_institutional_flows() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "symbol": "1001",
                "trade_date": pd.Timestamp("2024-01-31"),
                "market": "TWSE",
                "foreign_net_buy_twd": -20_000.0,
                "investment_trust_net_buy_twd": 30_000.0,
                "dealer_net_buy_twd": 5_000.0,
            },
            {
                "symbol": "1002",
                "trade_date": pd.Timestamp("2024-01-31"),
                "market": "TWSE",
                "foreign_net_buy_twd": -100_000.0,
                "investment_trust_net_buy_twd": -10_000.0,
                "dealer_net_buy_twd": 0.0,
            },
        ]
    )


def synthetic_margin_short() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "symbol": "1001",
                "trade_date": pd.Timestamp("2024-01-30"),
                "market": "TWSE",
                "margin_buy_balance": 40_000,
                "margin_sell_balance": 1_000,
                "short_sale_balance": 2_000,
                "day_trade_volume": 3_000,
            },
            {
                "symbol": "1001",
                "trade_date": pd.Timestamp("2024-01-31"),
                "market": "TWSE",
                "margin_buy_balance": 50_000,
                "margin_sell_balance": 1_100,
                "short_sale_balance": 2_500,
                "day_trade_volume": 3_500,
            },
            {
                "symbol": "1002",
                "trade_date": pd.Timestamp("2024-01-30"),
                "market": "TWSE",
                "margin_buy_balance": 70_000,
                "margin_sell_balance": 1_000,
                "short_sale_balance": 1_000,
                "day_trade_volume": 2_000,
            },
            {
                "symbol": "1002",
                "trade_date": pd.Timestamp("2024-01-31"),
                "market": "TWSE",
                "margin_buy_balance": 90_000,
                "margin_sell_balance": 1_100,
                "short_sale_balance": 800,
                "day_trade_volume": 2_500,
            },
        ]
    )


def test_add_event_flow_features_computes_ratios_changes_and_proxies() -> None:
    features = add_event_flow_features(
        synthetic_events(),
        institutional_flows=synthetic_institutional_flows(),
        margin_short=synthetic_margin_short(),
    )

    first = features[features["symbol"] == "1001"].iloc[0]
    second = features[features["symbol"] == "1002"].iloc[0]

    assert first["foreign_net_buy_to_turnover"] == pytest.approx(-0.02)
    assert first["investment_trust_net_buy_to_turnover"] == pytest.approx(0.03)
    assert first["total_institutional_net_buy_twd"] == pytest.approx(15_000.0)
    assert first["margin_balance_change_1d"] == pytest.approx(10_000)
    assert first["short_balance_change_1d"] == pytest.approx(500)
    assert first["short_squeeze_proxy"] == pytest.approx(0.05)
    assert first["margin_crowding_proxy"] == pytest.approx(5.0)

    assert second["foreign_net_buy_to_turnover"] == pytest.approx(-0.10)
    assert second["short_balance_change_1d"] == pytest.approx(-200)
    assert second["margin_crowding_proxy"] == pytest.approx(9.0)


def test_apply_flow_feature_filters_keeps_only_qualified_events() -> None:
    features = add_event_flow_features(
        synthetic_events(),
        institutional_flows=synthetic_institutional_flows(),
        margin_short=synthetic_margin_short(),
    )

    filtered = apply_flow_feature_filters(
        features,
        require_foreign_not_selling_heavily=True,
        require_investment_trust_buying=True,
        avoid_margin_overcrowded=True,
        prefer_short_balance_rising_before_limit_up=True,
        foreign_heavy_sell_threshold=-0.05,
        margin_crowding_threshold=6.0,
    )

    assert filtered["symbol"].tolist() == ["1001"]


def test_institutional_flow_csv_importer_normalizes_numbers_and_upserts(tmp_path: Path) -> None:
    db_path = tmp_path / "taiwan_trading.duckdb"
    csv_path = tmp_path / "institutional.csv"
    csv_path.write_text(
        "\n".join(
            [
                "symbol,trade_date,market,foreign_net_buy_twd,investment_trust_net_buy_twd,dealer_net_buy_twd",
                '1001,2024-01-31,TWSE,"10,000",2000,-500',
            ]
        ),
        encoding="utf-8",
    )

    frame = read_institutional_flows_csv(csv_path, source="test_csv")
    assert frame.iloc[0]["foreign_net_buy_twd"] == pytest.approx(10_000.0)
    assert frame.iloc[0]["total_institutional_net_buy_twd"] == pytest.approx(11_500.0)

    init_db(db_path)
    assert import_institutional_flows_csv(db_path=db_path, csv_path=csv_path, source="test_csv") == 1
    with get_connection(db_path, read_only=True) as conn:
        stored = conn.execute("SELECT total_institutional_net_buy_twd FROM institutional_flows").fetchone()[0]
    assert stored == pytest.approx(11_500.0)


def test_margin_short_csv_importer_defaults_optional_columns_and_upserts(tmp_path: Path) -> None:
    db_path = tmp_path / "taiwan_trading.duckdb"
    csv_path = tmp_path / "margin_short.csv"
    csv_path.write_text(
        "\n".join(
            [
                "symbol,trade_date,market,margin_buy_balance,short_sale_balance",
                '1001,2024-01-31,TPEX,"50,000",1200',
            ]
        ),
        encoding="utf-8",
    )

    frame = read_margin_short_csv(csv_path, source="test_csv")
    assert frame.iloc[0]["margin_buy_balance"] == 50_000
    assert frame.iloc[0]["margin_sell_balance"] == 0
    assert frame.iloc[0]["day_trade_volume"] == 0

    init_db(db_path)
    assert import_margin_short_csv(db_path=db_path, csv_path=csv_path, source="test_csv") == 1
    with get_connection(db_path, read_only=True) as conn:
        stored = conn.execute("SELECT margin_buy_balance, short_sale_balance FROM margin_short").fetchone()
    assert stored == (50_000, 1200)


def test_institutional_flow_csv_rejects_duplicate_keys(tmp_path: Path) -> None:
    csv_path = tmp_path / "duplicate_flows.csv"
    csv_path.write_text(
        "\n".join(
            [
                "symbol,trade_date,market,foreign_net_buy_twd,investment_trust_net_buy_twd,dealer_net_buy_twd",
                "1001,2024-01-31,TWSE,1,2,3",
                "1001,2024-01-31,TWSE,4,5,6",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(InstitutionalFlowImportError, match="duplicate"):
        read_institutional_flows_csv(csv_path)
