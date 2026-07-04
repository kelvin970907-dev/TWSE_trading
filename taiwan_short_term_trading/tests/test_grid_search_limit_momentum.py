from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.backtests.grid_search_limit_momentum import (
    all_event_type_sets,
    iter_parameter_grid,
    run_grid_search,
)
from src.db import get_connection, init_db, upsert_dataframe


def synthetic_grid_daily_prices() -> pd.DataFrame:
    rows: list[dict[str, object]] = []

    for year, event_date, day1_date in [
        (2024, pd.Timestamp("2024-01-31"), pd.Timestamp("2024-02-01")),
        (2025, pd.Timestamp("2025-01-31"), pd.Timestamp("2025-02-03")),
    ]:
        for day in pd.date_range(f"{year}-01-01", periods=20, freq="D"):
            rows.append(
                {
                    "symbol": "1001",
                    "trade_date": day,
                    "market": "TWSE",
                    "name": "Grid Test",
                    "open": 100.0,
                    "high": 100.0,
                    "low": 100.0,
                    "close": 100.0,
                    "volume_shares": 1_000,
                    "turnover_twd": 100_000.0,
                    "daily_return": 0.0,
                    "limit_up_price": 110.0,
                    "touched_limit_up": False,
                    "closed_limit_up": False,
                    "source": "test",
                }
            )

        rows.append(
            {
                "symbol": "1001",
                "trade_date": event_date,
                "market": "TWSE",
                "name": "Grid Test",
                "open": 101.0,
                "high": 109.0,
                "low": 104.0,
                "close": 108.5,
                "volume_shares": 2_000,
                "turnover_twd": 250_000_000.0,
                "daily_return": 0.085,
                "limit_up_price": 110.0,
                "touched_limit_up": False,
                "closed_limit_up": False,
                "source": "test",
            }
        )
        rows.append(
            {
                "symbol": "1001",
                "trade_date": day1_date,
                "market": "TWSE",
                "name": "Grid Test",
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
                "volume_shares": 1_000,
                "turnover_twd": 100_000.0,
                "daily_return": 0.0,
                "limit_up_price": 110.0,
                "touched_limit_up": False,
                "closed_limit_up": False,
                "source": "test",
            }
        )

    return pd.DataFrame(rows)


def synthetic_split_boundary_daily_prices() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for day in pd.date_range("2024-12-01", periods=20, freq="D"):
        rows.append(
            {
                "symbol": "1001",
                "trade_date": day,
                "market": "TWSE",
                "name": "Boundary Test",
                "open": 100.0,
                "high": 100.0,
                "low": 100.0,
                "close": 100.0,
                "volume_shares": 1_000,
                "turnover_twd": 100_000.0,
                "daily_return": 0.0,
                "limit_up_price": 110.0,
                "touched_limit_up": False,
                "closed_limit_up": False,
                "source": "test",
            }
        )
    rows.append(
        {
            "symbol": "1001",
            "trade_date": pd.Timestamp("2024-12-31"),
            "market": "TWSE",
            "name": "Boundary Test",
            "open": 101.0,
            "high": 109.0,
            "low": 104.0,
            "close": 108.5,
            "volume_shares": 2_000,
            "turnover_twd": 250_000_000.0,
            "daily_return": 0.085,
            "limit_up_price": 110.0,
            "touched_limit_up": False,
            "closed_limit_up": False,
            "source": "test",
        }
    )
    rows.append(
        {
            "symbol": "1001",
            "trade_date": pd.Timestamp("2025-01-02"),
            "market": "TWSE",
            "name": "Boundary Test",
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.5,
            "volume_shares": 1_000,
            "turnover_twd": 100_000.0,
            "daily_return": 0.0,
            "limit_up_price": 110.0,
            "touched_limit_up": False,
            "closed_limit_up": False,
            "source": "test",
        }
    )
    return pd.DataFrame(rows)


def test_event_type_grid_contains_singles_and_combinations() -> None:
    event_sets = all_event_type_sets()

    assert len(event_sets) == 15
    assert ("near_limit_8_9",) in event_sets
    assert ("near_limit_8_9", "near_limit_9_10") in event_sets
    assert (
        "near_limit_8_9",
        "near_limit_9_10",
        "closed_limit_up",
        "touched_limit_not_closed",
    ) in event_sets


def test_iter_parameter_grid_builds_expected_count() -> None:
    params = list(
        iter_parameter_grid(
            event_type_sets=[("near_limit_8_9",)],
            min_turnover_values=[50_000_000.0],
            min_volume_ratio_values=[1.5],
            min_close_location_values=[0.7],
            take_profit_values=[0.01],
            stop_loss_values=[0.01],
            path_assumptions=["pessimistic", "close_only"],
            markets=["TWSE", "BOTH"],
        )
    )

    assert len(params) == 4
    assert {param.path_assumption for param in params} == {"pessimistic", "close_only"}
    assert {param.market for param in params} == {"TWSE", "BOTH"}


def test_run_grid_search_writes_train_test_results_and_resumes(tmp_path: Path) -> None:
    db_path = tmp_path / "taiwan_trading.duckdb"
    output_dir = tmp_path / "reports"
    init_db(db_path)
    with get_connection(db_path) as conn:
        upsert_dataframe(conn, "daily_prices", synthetic_grid_daily_prices(), ["symbol", "trade_date"])

    results = run_grid_search(
        db_path=db_path,
        output_dir=output_dir,
        train_start="2024-01-01",
        train_end="2024-12-31",
        test_start="2025-01-01",
        test_end="2025-12-31",
        fixed_notional_twd=200_000.0,
        min_train_trades_for_ranking=1,
        top_n=10,
        show_progress=False,
        event_type_sets=[("near_limit_8_9",)],
        min_turnover_values=[50_000_000.0],
        min_volume_ratio_values=[1.5],
        min_close_location_values=[0.7],
        take_profit_values=[0.01],
        stop_loss_values=[0.01],
        path_assumptions=["pessimistic", "close_only"],
        markets=["TWSE"],
    )

    assert len(results) == 2
    assert (output_dir / "grid_search_results.csv").exists()
    assert (output_dir / "grid_search_top_train.csv").exists()
    assert (output_dir / "grid_search_top_test.csv").exists()
    assert set(results["path_assumption"]) == {"pessimistic", "close_only"}
    assert set(results["train_trades"]) == {1}
    assert set(results["test_trades"]) == {1}

    close_only = results[results["path_assumption"] == "close_only"].iloc[0]
    pessimistic = results[results["path_assumption"] == "pessimistic"].iloc[0]
    assert close_only["train_net_pnl"] > pessimistic["train_net_pnl"]
    assert close_only["test_net_pnl"] > pessimistic["test_net_pnl"]
    assert close_only["train_rank_eligible"]

    resumed = run_grid_search(
        db_path=db_path,
        output_dir=output_dir,
        train_start="2024-01-01",
        train_end="2024-12-31",
        test_start="2025-01-01",
        test_end="2025-12-31",
        fixed_notional_twd=200_000.0,
        min_train_trades_for_ranking=1,
        top_n=10,
        show_progress=False,
        event_type_sets=[("near_limit_8_9",)],
        min_turnover_values=[50_000_000.0],
        min_volume_ratio_values=[1.5],
        min_close_location_values=[0.7],
        take_profit_values=[0.01],
        stop_loss_values=[0.01],
        path_assumptions=["pessimistic", "close_only"],
        markets=["TWSE"],
    )

    assert len(resumed) == 2


def test_run_grid_search_applies_flow_filter_values(tmp_path: Path) -> None:
    db_path = tmp_path / "taiwan_trading.duckdb"
    output_dir = tmp_path / "reports"
    init_db(db_path)
    with get_connection(db_path) as conn:
        upsert_dataframe(conn, "daily_prices", synthetic_grid_daily_prices(), ["symbol", "trade_date"])
        upsert_dataframe(
            conn,
            "institutional_flows",
            pd.DataFrame(
                [
                    {
                        "symbol": "1001",
                        "trade_date": pd.Timestamp("2024-01-31"),
                        "market": "TWSE",
                        "foreign_net_buy_twd": 0.0,
                        "investment_trust_net_buy_twd": 10_000.0,
                        "dealer_net_buy_twd": 0.0,
                        "total_institutional_net_buy_twd": 10_000.0,
                        "source": "test",
                    },
                    {
                        "symbol": "1001",
                        "trade_date": pd.Timestamp("2025-01-31"),
                        "market": "TWSE",
                        "foreign_net_buy_twd": 0.0,
                        "investment_trust_net_buy_twd": 10_000.0,
                        "dealer_net_buy_twd": 0.0,
                        "total_institutional_net_buy_twd": 10_000.0,
                        "source": "test",
                    },
                ]
            ),
            ["symbol", "trade_date"],
        )

    results = run_grid_search(
        db_path=db_path,
        output_dir=output_dir,
        train_start="2024-01-01",
        train_end="2024-12-31",
        test_start="2025-01-01",
        test_end="2025-12-31",
        fixed_notional_twd=200_000.0,
        min_train_trades_for_ranking=1,
        top_n=10,
        show_progress=False,
        event_type_sets=[("near_limit_8_9",)],
        min_turnover_values=[50_000_000.0],
        min_volume_ratio_values=[1.5],
        min_close_location_values=[0.7],
        take_profit_values=[0.01],
        stop_loss_values=[0.01],
        path_assumptions=["close_only"],
        markets=["TWSE"],
        require_investment_trust_buying_values=[True],
    )

    assert len(results) == 1
    row = results.iloc[0]
    assert bool(row["require_investment_trust_buying"]) is True
    assert row["train_trades"] == 1
    assert row["test_trades"] == 1


def test_grid_search_split_uses_entry_date_to_avoid_boundary_leakage(tmp_path: Path) -> None:
    db_path = tmp_path / "taiwan_trading.duckdb"
    output_dir = tmp_path / "reports"
    init_db(db_path)
    with get_connection(db_path) as conn:
        upsert_dataframe(conn, "daily_prices", synthetic_split_boundary_daily_prices(), ["symbol", "trade_date"])

    results = run_grid_search(
        db_path=db_path,
        output_dir=output_dir,
        train_start="2024-01-01",
        train_end="2024-12-31",
        test_start="2025-01-01",
        test_end="2025-12-31",
        fixed_notional_twd=200_000.0,
        min_train_trades_for_ranking=0,
        top_n=10,
        show_progress=False,
        event_type_sets=[("near_limit_8_9",)],
        min_turnover_values=[50_000_000.0],
        min_volume_ratio_values=[1.5],
        min_close_location_values=[0.7],
        take_profit_values=[0.01],
        stop_loss_values=[0.01],
        path_assumptions=["close_only"],
        markets=["TWSE"],
    )

    row = results.iloc[0]
    assert row["train_trades"] == 0
    assert row["test_trades"] == 1
