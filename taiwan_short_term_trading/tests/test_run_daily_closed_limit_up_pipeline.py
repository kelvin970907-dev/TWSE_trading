from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.db import get_connection, init_db, upsert_dataframe
from src.live.generate_closed_limit_up_signals import OUTPUT_COLUMNS
from src.live.run_daily_closed_limit_up_pipeline import (
    LEDGER_OUTPUT,
    run_daily_closed_limit_up_pipeline,
)
from src.live.strategy_profiles import ALL_PROFILE_NAMES, ORIGINAL_CHAMPION
from src.live.strategy_profiles import BROAD_CHALLENGER, CONSERVATIVE_TPEX


def test_dry_run_does_not_write_signal_or_ledger_outputs(tmp_path: Path) -> None:
    db_path = tmp_path / "taiwan_trading.duckdb"
    output_dir = tmp_path / "live_signals"
    seed_zero_order_database(db_path)

    result = run_daily_closed_limit_up_pipeline(
        db_path=db_path,
        output_dir=output_dir,
        skip_data_update=True,
        skip_index_update=True,
        skip_sector_update=True,
        dry_run=True,
    )

    assert result.dry_run is True
    assert result.report_path is not None
    assert result.report_path.exists()
    assert not list(output_dir.glob("closed_limit_up_signals_*.csv"))
    assert not (output_dir / LEDGER_OUTPUT).exists()


def test_zero_order_signal_day_works_and_report_is_generated(tmp_path: Path) -> None:
    db_path = tmp_path / "taiwan_trading.duckdb"
    output_dir = tmp_path / "live_signals"
    seed_zero_order_database(db_path)

    result = run_daily_closed_limit_up_pipeline(
        db_path=db_path,
        output_dir=output_dir,
        skip_data_update=True,
        skip_index_update=True,
        skip_sector_update=True,
    )

    assert result.raw_candidates == 1
    assert result.selected_orders == 0
    assert result.generated_signal_file is not None
    assert result.generated_signal_file.exists()
    assert result.report_path is not None
    assert "Selected paper orders: `0`" in result.report_path.read_text(encoding="utf-8")


def test_all_profile_report_handles_zero_order_profiles(tmp_path: Path) -> None:
    db_path = tmp_path / "taiwan_trading.duckdb"
    output_dir = tmp_path / "live_signals"
    seed_profile_mix_database(db_path)

    result = run_daily_closed_limit_up_pipeline(
        db_path=db_path,
        output_dir=output_dir,
        skip_data_update=True,
        skip_index_update=True,
        skip_sector_update=True,
        profile="all",
    )

    assert set(result.selected_orders_by_profile) == set(ALL_PROFILE_NAMES)
    assert result.selected_orders_by_profile[ORIGINAL_CHAMPION] == 5
    assert any(count == 0 for count in result.selected_orders_by_profile.values())
    assert result.generated_signal_file is not None
    assert result.generated_signal_file.name.startswith("closed_limit_up_signals_all_profiles_")
    assert all(not symbols for symbols in result.overlapping_symbols.values())
    report_text = result.report_path.read_text(encoding="utf-8") if result.report_path else ""
    assert "Profile Comparison" in report_text


def test_stale_taiex_skips_market_regime_profiles_but_original_runs(tmp_path: Path) -> None:
    db_path = tmp_path / "taiwan_trading.duckdb"
    output_dir = tmp_path / "live_signals"
    seed_profile_mix_database(db_path)
    with get_connection(db_path) as conn:
        upsert_dataframe(
            conn,
            "index_daily_prices",
            pd.DataFrame([taiex_row(pd.Timestamp("2024-01-09"))]),
            ["index_symbol", "trade_date"],
        )

    result = run_daily_closed_limit_up_pipeline(
        db_path=db_path,
        output_dir=output_dir,
        skip_data_update=True,
        skip_index_update=True,
        skip_sector_update=True,
        profile="all",
    )

    assert result.taiex_freshness_status == "stale"
    assert result.selected_orders_by_profile[ORIGINAL_CHAMPION] == 5
    assert result.selected_orders_by_profile[BROAD_CHALLENGER] == 0
    assert BROAD_CHALLENGER in result.skipped_profiles_due_to_stale_regime
    assert CONSERVATIVE_TPEX in result.skipped_profiles_due_to_stale_regime
    assert any("Skipped market-regime profile" in warning for warning in result.warnings)
    assert result.report_path is not None
    report_text = result.report_path.read_text(encoding="utf-8")
    assert "TAIEX freshness status: `stale`" in report_text


def test_fresh_taiex_allows_broad_challenger(tmp_path: Path) -> None:
    db_path = tmp_path / "taiwan_trading.duckdb"
    output_dir = tmp_path / "live_signals"
    seed_profile_mix_database(db_path)
    with get_connection(db_path) as conn:
        upsert_dataframe(
            conn,
            "index_daily_prices",
            pd.DataFrame([taiex_row(pd.Timestamp("2024-01-10"))]),
            ["index_symbol", "trade_date"],
        )

    result = run_daily_closed_limit_up_pipeline(
        db_path=db_path,
        output_dir=output_dir,
        skip_data_update=True,
        skip_index_update=True,
        skip_sector_update=True,
        profile="all",
    )

    assert result.taiex_freshness_status == "fresh"
    assert BROAD_CHALLENGER not in result.skipped_profiles_due_to_stale_regime
    assert result.selected_orders_by_profile[BROAD_CHALLENGER] > 0


def test_pending_evaluation_is_reported(tmp_path: Path) -> None:
    db_path = tmp_path / "taiwan_trading.duckdb"
    output_dir = tmp_path / "live_signals"
    output_dir.mkdir(parents=True)
    seed_pending_evaluation_database(db_path)
    write_signal_csv(output_dir / "closed_limit_up_signals_2024-01-10.csv")

    result = run_daily_closed_limit_up_pipeline(
        db_path=db_path,
        output_dir=output_dir,
        skip_data_update=True,
        skip_index_update=True,
        skip_sector_update=True,
        signal_date="latest",
    )

    assert result.pending_evaluations == ["closed_limit_up_signals_2024-01-10.csv"]
    assert (output_dir / "closed_limit_up_eval_2024-01-10.csv").exists()
    assert result.manual_fill_summary["missing_manual_observations"] == 1
    assert any("Manual fill observations are missing" in warning for warning in result.warnings)


def test_evaluated_ledger_summary_is_computed(tmp_path: Path) -> None:
    db_path = tmp_path / "taiwan_trading.duckdb"
    output_dir = tmp_path / "live_signals"
    output_dir.mkdir(parents=True)
    seed_evaluated_database(db_path)
    write_signal_csv(output_dir / "closed_limit_up_signals_2024-01-10.csv")

    result = run_daily_closed_limit_up_pipeline(
        db_path=db_path,
        output_dir=output_dir,
        skip_data_update=True,
        skip_index_update=True,
        skip_sector_update=True,
        signal_date="latest",
    )

    assert result.evaluated_signal_files == ["closed_limit_up_signals_2024-01-10.csv"]
    assert result.ledger_summary["total_evaluated_paper_trades"] == 1
    assert result.ledger_summary["cumulative_net_pnl"] > 0
    assert result.ledger_summary["win_rate"] == 1.0


def test_missing_taiex_warning_works(tmp_path: Path) -> None:
    db_path = tmp_path / "taiwan_trading.duckdb"
    output_dir = tmp_path / "live_signals"
    seed_zero_order_database(db_path)

    result = run_daily_closed_limit_up_pipeline(
        db_path=db_path,
        output_dir=output_dir,
        skip_data_update=True,
        skip_index_update=True,
        skip_sector_update=True,
    )

    assert any("TAIEX index_daily_prices are missing" in warning for warning in result.warnings)


def seed_zero_order_database(db_path: Path) -> None:
    init_db(db_path)
    signal_date = pd.Timestamp("2024-01-10")
    rows = []
    for offset in range(1, 6):
        rows.append(daily_row("7001", signal_date - pd.Timedelta(days=offset), close=50.0, closed_limit_up=False))
    rows.append(
        daily_row(
            "7001",
            signal_date,
            close=55.0,
            closed_limit_up=True,
            turnover=100_000_000.0,
            volume=500_000,
            high=55.0,
            low=52.0,
            open_price=52.5,
        )
    )
    with get_connection(db_path) as conn:
        upsert_dataframe(conn, "daily_prices", pd.DataFrame(rows), ["symbol", "trade_date"])
        upsert_dataframe(conn, "stock_sector_map", sector_map(["7001"]), ["symbol", "market"])


def seed_profile_mix_database(db_path: Path) -> None:
    init_db(db_path)
    signal_date = pd.Timestamp("2024-01-10")
    rows = []
    symbols = ["7001", "7002", "7003", "7004", "7005", "7006"]
    for symbol in symbols:
        for offset in range(1, 6):
            rows.append(
                daily_row(
                    symbol,
                    signal_date - pd.Timedelta(days=offset),
                    close=50.0,
                    closed_limit_up=False,
                    volume=100_000,
                )
            )
        rows.append(
            daily_row(
                symbol,
                signal_date,
                close=55.0,
                closed_limit_up=True,
                turnover=700_000_000.0 + int(symbol),
                volume=500_000,
                high=55.0,
                low=52.0,
                open_price=52.5,
            )
        )
    with get_connection(db_path) as conn:
        upsert_dataframe(conn, "daily_prices", pd.DataFrame(rows), ["symbol", "trade_date"])
        upsert_dataframe(conn, "stock_sector_map", sector_map(symbols), ["symbol", "market"])


def seed_pending_evaluation_database(db_path: Path) -> None:
    init_db(db_path)
    rows = [
        daily_row("7001", pd.Timestamp("2024-01-10"), close=50.0, closed_limit_up=True),
        daily_row("7999", pd.Timestamp("2024-01-11"), close=20.0, closed_limit_up=False),
    ]
    with get_connection(db_path) as conn:
        upsert_dataframe(conn, "daily_prices", pd.DataFrame(rows), ["symbol", "trade_date"])
        upsert_dataframe(conn, "stock_sector_map", sector_map(["7001", "7999"]), ["symbol", "market"])


def seed_evaluated_database(db_path: Path) -> None:
    init_db(db_path)
    rows = [
        daily_row("7001", pd.Timestamp("2024-01-10"), close=50.0, closed_limit_up=True),
        daily_row(
            "7001",
            pd.Timestamp("2024-01-11"),
            open_price=52.0,
            high=53.0,
            low=51.0,
            close=52.5,
            closed_limit_up=False,
        ),
    ]
    with get_connection(db_path) as conn:
        upsert_dataframe(conn, "daily_prices", pd.DataFrame(rows), ["symbol", "trade_date"])
        upsert_dataframe(conn, "stock_sector_map", sector_map(["7001"]), ["symbol", "market"])


def daily_row(
    symbol: str,
    trade_date: pd.Timestamp,
    *,
    close: float,
    closed_limit_up: bool,
    turnover: float = 700_000_000.0,
    volume: int = 500_000,
    high: float | None = None,
    low: float | None = None,
    open_price: float | None = None,
) -> dict[str, object]:
    previous_close = close / 1.10 if closed_limit_up else close
    return {
        "symbol": symbol,
        "trade_date": trade_date,
        "market": "TPEX",
        "name": f"Stock {symbol}",
        "open": open_price if open_price is not None else close,
        "high": high if high is not None else close * 1.01,
        "low": low if low is not None else close * 0.99,
        "close": close,
        "volume_shares": volume,
        "turnover_twd": turnover,
        "trades": 1_000,
        "prev_close": previous_close,
        "daily_return": close / previous_close - 1.0,
        "limit_up_price": close if closed_limit_up else previous_close * 1.10,
        "limit_down_price": previous_close * 0.90,
        "touched_limit_up": closed_limit_up,
        "touched_limit_down": False,
        "closed_limit_up": closed_limit_up,
        "closed_limit_down": False,
        "source": "unit_test",
        "created_at": pd.Timestamp("2024-01-10"),
    }


def sector_map(symbols: list[str]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "symbol": symbol,
                "market": "TPEX",
                "name": f"Stock {symbol}",
                "sector": "Technology/Electronics",
                "industry": "Electronic Components",
                "source": "unit_test",
            }
            for symbol in symbols
        ]
    )


def write_signal_csv(path: Path) -> None:
    row = {column: "" for column in OUTPUT_COLUMNS}
    row.update(
        {
            "signal_date": "2024-01-10",
            "symbol": "7001",
            "name": "Stock 7001",
            "market": "TPEX",
            "sector": "Technology/Electronics",
            "industry": "Electronic Components",
            "close_price": 50.0,
            "planned_entry_price": 50.0,
            "planned_exit": "Day1 open",
            "planned_shares": 4000,
            "planned_buy_notional_twd": 200_000.0,
        }
    )
    pd.DataFrame([row], columns=OUTPUT_COLUMNS).to_csv(path, index=False)


def taiex_row(trade_date: pd.Timestamp) -> dict[str, object]:
    return {
        "index_symbol": "TAIEX",
        "trade_date": trade_date,
        "open": 100.0,
        "high": 101.0,
        "low": 99.0,
        "close": 100.5,
        "volume": 1_000_000.0,
        "turnover_twd": 1_000_000_000.0,
        "daily_return": 0.01,
        "ma5": 99.0,
        "ma20": 98.0,
        "ma60": 97.0,
        "close_above_ma20": True,
        "close_above_ma60": True,
        "drawdown_from_60d_high": -0.01,
        "source": "unit_test",
    }
