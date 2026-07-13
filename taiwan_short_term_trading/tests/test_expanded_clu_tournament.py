from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.backtests.event_study import build_event_candidates
from src.backtests.expanded_clu_tournament import (
    ExpandedCLUConfig,
    add_theme_breadth_features,
    apply_expanded_filters,
    broad_champion_config,
    expanded_config_hash,
    iter_expanded_grid,
    run_expanded_clu_tournament,
)
from src.db import get_connection, init_db, upsert_dataframe


def test_expanded_grid_contains_requested_feature_families() -> None:
    configs = iter_expanded_grid()
    hashes = {expanded_config_hash(config) for config in configs}

    assert len(configs) > 1_000
    assert expanded_config_hash(broad_champion_config()) in hashes
    assert any(config.min_fill_quality_score == 90 for config in configs)
    assert any(config.min_range_pct == 0.05 for config in configs)
    assert any(config.only_first_limit_up for config in configs)
    assert any(config.sector_filter == "technology_only" for config in configs)
    assert any(config.same_sector_limitups_min == 5 for config in configs)
    assert any(config.market_regime_filter == "taiex_above_ma60" for config in configs)
    assert any(config.max_positions_per_day == 8 for config in configs)
    assert any(config.max_notional_per_symbol_pct == 0.10 for config in configs)


def test_theme_breadth_and_filters_work() -> None:
    events = pd.DataFrame(
        [
            event_row("1001", "Technology/Electronics", "Semiconductor", 0.04),
            event_row("1002", "Technology/Electronics", "Components", 0.02),
            event_row("1003", "Industrials/Other", "Machinery", 0.04),
        ]
    )
    events = add_theme_breadth_features(events)

    config = ExpandedCLUConfig(
        min_range_pct=0.03,
        same_sector_limitups_min=2,
        market_regime_filter="none",
        sector_filter="technology_only",
    )
    filtered = apply_expanded_filters(events, config, weak_industries=set())

    assert filtered["symbol"].tolist() == ["1001"]
    assert int(events.loc[events["symbol"] == "1001", "same_sector_limitup_count"].iloc[0]) == 2


def test_expanded_tournament_fast_writes_outputs(tmp_path: Path) -> None:
    db_path = build_expanded_db(tmp_path)
    output_dir = tmp_path / "reports"

    result = run_expanded_clu_tournament(
        db_path=db_path,
        output_dir=output_dir,
        start="2024-01-01",
        end="2024-03-31",
        stage="fast",
        max_configs=6,
        show_progress=False,
    )

    assert len(result.fast_results) == 6
    assert result.exact_results.empty
    assert result.report_path.exists()
    assert (output_dir / "expanded_clu_tournament_fast.csv").exists()
    assert (output_dir / "expanded_clu_tournament_top.csv").exists()
    assert (output_dir / "expanded_clu_tournament_report.md").exists()


def event_row(symbol: str, sector: str, industry: str, range_pct: float) -> dict[str, object]:
    return {
        "event_id": f"{symbol}_20240215_closed_limit_up",
        "symbol": symbol,
        "market": "TPEX",
        "trade_date": pd.Timestamp("2024-02-15"),
        "next_trade_date": pd.Timestamp("2024-02-16"),
        "event_type": "closed_limit_up",
        "day0_close": 55.0,
        "day0_turnover_twd": 700_000_000.0,
        "volume_ratio_20d": 2.0,
        "fill_quality_score": 70.0,
        "fillable_moderate": True,
        "day0_range_pct": range_pct,
        "day0_high_low_close_lock": False,
        "turnover_percentile_252d": 0.9,
        "prior_5d_return": 0.05,
        "prior_20d_return": 0.20,
        "consecutive_limit_up_count": 1,
        "first_limit_up_in_sequence": True,
        "sector": sector,
        "industry": industry,
        "index_data_available": True,
        "bear_regime": False,
        "weak_market_day": False,
        "taiex_above_ma20": True,
        "taiex_above_ma60": True,
        "taiex_5d_return": 0.01,
    }


def build_expanded_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "taiwan_trading.duckdb"
    init_db(db_path)
    event_date = pd.Timestamp("2024-02-15")
    daily_prices = pd.DataFrame(
        [
            *symbol_rows("7001", "TPEX", "Alpha Tech", event_date, "Technology/Electronics"),
            *symbol_rows("7002", "TPEX", "Beta Tech", event_date, "Technology/Electronics"),
            *symbol_rows("1301", "TWSE", "Gamma Tech", event_date, "Technology/Electronics"),
        ]
    )
    sectors = pd.DataFrame(
        [
            {"symbol": "7001", "market": "TPEX", "name": "Alpha Tech", "sector": "Technology/Electronics", "industry": "Semiconductor", "source": "test"},
            {"symbol": "7002", "market": "TPEX", "name": "Beta Tech", "sector": "Technology/Electronics", "industry": "Components", "source": "test"},
            {"symbol": "1301", "market": "TWSE", "name": "Gamma Tech", "sector": "Technology/Electronics", "industry": "Components", "source": "test"},
        ]
    )
    index_rows = pd.DataFrame(
        [
            {
                "index_symbol": "TAIEX",
                "trade_date": date,
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0 + i,
                "volume": 1_000_000.0,
                "turnover_twd": 10_000_000_000.0,
                "daily_return": 0.001,
                "ma5": 98.0,
                "ma20": 97.0,
                "ma60": 96.0,
                "close_above_ma20": True,
                "close_above_ma60": True,
                "drawdown_from_60d_high": -0.01,
                "source": "test",
            }
            for i, date in enumerate(pd.bdate_range("2024-01-01", "2024-02-20"))
        ]
    )
    with get_connection(db_path) as conn:
        upsert_dataframe(conn, "daily_prices", daily_prices, ["symbol", "trade_date"])
        upsert_dataframe(conn, "stock_sector_map", sectors, ["symbol", "market"])
        upsert_dataframe(conn, "index_daily_prices", index_rows, ["index_symbol", "trade_date"])
    build_event_candidates(db_path=db_path, start="2024-01-01", end="2024-03-31", markets=["TWSE", "TPEX"])
    return db_path


def symbol_rows(symbol: str, market: str, name: str, event_date: pd.Timestamp, sector: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    previous_close = 50.0
    for date in pd.bdate_range(end=event_date - pd.Timedelta(days=1), periods=24):
        rows.append(price_row(symbol, market, name, date, close=previous_close, prev_close=previous_close, volume=100_000))
    event_close = previous_close * 1.10
    rows.append(
        price_row(
            symbol,
            market,
            name,
            event_date,
            close=event_close,
            prev_close=previous_close,
            volume=2_000_000,
            turnover=700_000_000,
            open_price=previous_close * 1.04,
            high=event_close,
            low=previous_close * 1.02,
            touched_limit_up=True,
            closed_limit_up=True,
            limit_up_price=event_close,
        )
    )
    day1 = pd.bdate_range(start=event_date + pd.Timedelta(days=1), periods=1)[0]
    rows.append(
        price_row(
            symbol,
            market,
            name,
            day1,
            close=event_close * 1.035,
            prev_close=event_close,
            volume=500_000,
            open_price=event_close * 1.03,
            high=event_close * 1.04,
            low=event_close,
        )
    )
    return rows


def price_row(
    symbol: str,
    market: str,
    name: str,
    trade_date: pd.Timestamp,
    *,
    close: float,
    prev_close: float,
    volume: int,
    turnover: float | None = None,
    open_price: float | None = None,
    high: float | None = None,
    low: float | None = None,
    touched_limit_up: bool = False,
    closed_limit_up: bool = False,
    limit_up_price: float | None = None,
) -> dict[str, object]:
    open_value = close if open_price is None else open_price
    high_value = close * 1.002 if high is None else high
    low_value = close * 0.998 if low is None else low
    return {
        "symbol": symbol,
        "trade_date": trade_date,
        "market": market,
        "name": name,
        "open": open_value,
        "high": high_value,
        "low": low_value,
        "close": close,
        "volume_shares": volume,
        "turnover_twd": turnover if turnover is not None else volume * close,
        "trades": 100,
        "prev_close": prev_close,
        "daily_return": close / prev_close - 1.0 if prev_close else 0.0,
        "limit_up_price": limit_up_price if limit_up_price is not None else prev_close * 1.10,
        "limit_down_price": prev_close * 0.90,
        "touched_limit_up": touched_limit_up,
        "touched_limit_down": False,
        "closed_limit_up": closed_limit_up,
        "closed_limit_down": False,
        "source": "test",
        "created_at": pd.Timestamp("2024-01-01"),
    }
