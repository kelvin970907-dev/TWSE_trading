from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.db import get_connection, init_db, upsert_dataframe
from src.live.generate_closed_limit_up_signals import (
    EXECUTION_WARNING,
    board_lot_shares,
    generate_closed_limit_up_signals,
    generate_closed_limit_up_signals_for_profiles,
)
from src.live.strategy_profiles import (
    ALL_PROFILE_NAMES,
    BROAD_CHALLENGER,
    ORIGINAL_CHAMPION,
    load_strategy_profiles,
)


def test_strategy_profile_registry_returns_all_three_profiles() -> None:
    profiles = load_strategy_profiles()

    assert set(profiles) == set(ALL_PROFILE_NAMES)
    assert profiles[ORIGINAL_CHAMPION].market == "TPEX"
    assert profiles[BROAD_CHALLENGER].market == "BOTH"
    assert profiles[BROAD_CHALLENGER].candidate_hash == "097dd332"


def test_generate_signals_selects_qualifying_tpex_and_writes_outputs(tmp_path: Path) -> None:
    db_path = tmp_path / "taiwan_trading.duckdb"
    output_dir = tmp_path / "live_signals"
    seed_signal_database(db_path)

    orders, skipped, csv_path, md_path = generate_closed_limit_up_signals(
        db_path=db_path,
        capital_twd=1_000_000,
        signal_date="latest",
        output_dir=output_dir,
    )

    assert len(orders) == 5
    assert set(orders["profile_name"]) == {ORIGINAL_CHAMPION}
    assert set(orders["market"]) == {"TPEX"}
    assert "7010" not in set(orders["symbol"])
    assert "7011" not in set(orders["symbol"])
    assert "8001" not in set(orders["symbol"])
    assert set(orders["sector"]).isdisjoint({"Healthcare", "Materials"})
    assert orders["planned_shares"].min() >= 1000
    assert set(orders["capped_notional_twd"]) == {200_000.0}
    assert (orders["execution_warning"] == EXECUTION_WARNING).all()
    assert csv_path.exists()
    assert md_path.exists()
    assert "Next-Day Evaluation Template" in md_path.read_text(encoding="utf-8")

    skip_counts = dict(zip(skipped["reason"], skipped["count"], strict=True))
    assert skip_counts["max_positions"] == 1
    assert skip_counts["avoided_weak_sector"] == 2


def test_signal_generator_applies_board_lot_skip(tmp_path: Path) -> None:
    db_path = tmp_path / "taiwan_trading.duckdb"
    output_dir = tmp_path / "live_signals"
    seed_signal_database(db_path, symbols=["7099"], signal_price=90.0)

    orders, skipped, _csv_path, _md_path = generate_closed_limit_up_signals(
        db_path=db_path,
        capital_twd=300_000,
        signal_date="2024-01-10",
        output_dir=output_dir,
    )

    assert orders.empty
    assert dict(zip(skipped["reason"], skipped["count"], strict=True))["board_lot_or_capital"] == 1
    assert board_lot_shares(60_000, 90.0) == 0


def test_signal_generator_can_generate_all_profiles(tmp_path: Path) -> None:
    db_path = tmp_path / "taiwan_trading.duckdb"
    output_dir = tmp_path / "live_signals"
    seed_signal_database(db_path)

    results, combined_csv, combined_md = generate_closed_limit_up_signals_for_profiles(
        db_path=db_path,
        capital_twd=1_000_000,
        signal_date="latest",
        output_dir=output_dir,
        profile="all",
    )

    assert set(results) == set(ALL_PROFILE_NAMES)
    assert combined_csv is not None
    assert combined_md is not None
    assert combined_csv.exists()
    assert combined_md.exists()
    for profile_name, (_orders, _skipped, csv_path, md_path) in results.items():
        assert csv_path.name.startswith(f"closed_limit_up_signals_{profile_name}_")
        assert csv_path.exists()
        assert md_path.exists()

    original_orders = results[ORIGINAL_CHAMPION][0]
    assert len(original_orders) == 5
    # The challenger profiles require market-regime data, so this synthetic fixture
    # intentionally exercises the zero-order profile path.
    assert results[BROAD_CHALLENGER][0].empty


def test_signal_generator_uses_signal_date_override(tmp_path: Path) -> None:
    db_path = tmp_path / "taiwan_trading.duckdb"
    output_dir = tmp_path / "live_signals"
    seed_signal_database(db_path)

    orders, _skipped, csv_path, _md_path = generate_closed_limit_up_signals(
        db_path=db_path,
        signal_date="2024-01-09",
        output_dir=output_dir,
    )

    assert orders.empty
    assert csv_path.name == f"closed_limit_up_signals_{ORIGINAL_CHAMPION}_2024-01-09.csv"


def seed_signal_database(
    db_path: Path,
    *,
    symbols: list[str] | None = None,
    signal_price: float = 55.0,
) -> None:
    init_db(db_path)
    signal_date = pd.Timestamp("2024-01-10")
    default_symbols = ["7001", "7002", "7003", "7004", "7005", "7006", "7010", "7011", "8001"]
    selected_symbols = symbols or default_symbols
    rows = []
    for symbol in selected_symbols:
        market = "TWSE" if symbol == "8001" else "TPEX"
        for offset in range(1, 6):
            date = signal_date - pd.Timedelta(days=offset)
            rows.append(daily_row(symbol, market, date, close=50.0, closed_limit_up=False, volume=100_000))
        price = signal_price if symbol != "7011" else 70.0
        rows.append(
            daily_row(
                symbol,
                market,
                signal_date,
                close=price,
                closed_limit_up=True,
                volume=500_000,
                turnover=700_000_000.0 + int(symbol) * 1_000.0,
                high=price,
                low=price * 0.95,
                open_price=price * 0.96,
            )
        )

    sector_rows = []
    for symbol in selected_symbols:
        if symbol == "8001":
            market = "TWSE"
            sector = "Technology/Electronics"
            industry = "Semiconductor"
        elif symbol == "7010":
            market = "TPEX"
            sector = "Healthcare"
            industry = "Biotech"
        elif symbol == "7011":
            market = "TPEX"
            sector = "Materials"
            industry = "Chemicals"
        else:
            market = "TPEX"
            sector = "Technology/Electronics"
            industry = "Electronic Components"
        sector_rows.append(
            {
                "symbol": symbol,
                "market": market,
                "name": f"Stock {symbol}",
                "sector": sector,
                "industry": industry,
                "source": "unit_test",
            }
        )

    with get_connection(db_path) as conn:
        upsert_dataframe(conn, "daily_prices", pd.DataFrame(rows), ["symbol", "trade_date"])
        upsert_dataframe(conn, "stock_sector_map", pd.DataFrame(sector_rows), ["symbol", "market"])


def daily_row(
    symbol: str,
    market: str,
    trade_date: pd.Timestamp,
    *,
    close: float,
    closed_limit_up: bool,
    volume: int,
    turnover: float = 20_000_000.0,
    high: float | None = None,
    low: float | None = None,
    open_price: float | None = None,
) -> dict[str, object]:
    previous_close = close / 1.10 if closed_limit_up else close
    high_value = high if high is not None else close * 1.01
    low_value = low if low is not None else close * 0.99
    open_value = open_price if open_price is not None else close
    return {
        "symbol": symbol,
        "trade_date": trade_date,
        "market": market,
        "name": f"Stock {symbol}",
        "open": open_value,
        "high": high_value,
        "low": low_value,
        "close": close,
        "volume_shares": volume,
        "turnover_twd": turnover,
        "trades": 1_000,
        "prev_close": previous_close,
        "daily_return": close / previous_close - 1.0 if previous_close else None,
        "limit_up_price": close if closed_limit_up else previous_close * 1.10,
        "limit_down_price": previous_close * 0.90,
        "touched_limit_up": closed_limit_up,
        "touched_limit_down": False,
        "closed_limit_up": closed_limit_up,
        "closed_limit_down": False,
        "source": "unit_test",
        "created_at": pd.Timestamp("2024-01-10"),
    }
