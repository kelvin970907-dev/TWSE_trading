from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.backtests.walk_forward import (
    generate_walk_forward_windows,
    run_walk_forward,
    walk_forward_score,
)
from src.db import get_connection, init_db, upsert_dataframe


def synthetic_walk_forward_daily_prices() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    events = [
        (pd.Timestamp("2023-01-31"), pd.Timestamp("2023-02-01")),
        (pd.Timestamp("2024-01-31"), pd.Timestamp("2024-02-01")),
        (pd.Timestamp("2024-04-30"), pd.Timestamp("2024-05-01")),
    ]

    for event_date, day1_date in events:
        for day in pd.date_range(event_date - pd.Timedelta(days=30), periods=20, freq="D"):
            rows.append(base_price_row(day, close=100.0, daily_return=0.0, volume=1_000, turnover=100_000.0))

        rows.append(
            base_price_row(
                event_date,
                open_price=101.0,
                high=109.0,
                low=104.0,
                close=108.5,
                daily_return=0.085,
                volume=2_000,
                turnover=250_000_000.0,
            )
        )
        rows.append(
            base_price_row(
                day1_date,
                open_price=100.0,
                high=101.0,
                low=99.0,
                close=100.5,
                daily_return=0.0,
                volume=1_000,
                turnover=100_000.0,
            )
        )

    return pd.DataFrame(rows).drop_duplicates(subset=["symbol", "trade_date"], keep="last")


def base_price_row(
    trade_date: pd.Timestamp,
    *,
    open_price: float = 100.0,
    high: float = 100.0,
    low: float = 100.0,
    close: float,
    daily_return: float,
    volume: int,
    turnover: float,
) -> dict[str, object]:
    return {
        "symbol": "1001",
        "trade_date": trade_date,
        "market": "TWSE",
        "name": "Walk Forward Test",
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "volume_shares": volume,
        "turnover_twd": turnover,
        "daily_return": daily_return,
        "limit_up_price": 110.0,
        "touched_limit_up": False,
        "closed_limit_up": False,
        "source": "test",
    }


def test_generate_walk_forward_windows_expanding_quarters() -> None:
    windows = list(
        generate_walk_forward_windows(
            initial_train_start="2023-01-01",
            first_test_start="2024-01-01",
            final_test_end="2024-06-30",
            test_months=3,
        )
    )

    assert [window.window_id for window in windows] == ["WF001", "WF002"]
    assert windows[0].train_start == pd.Timestamp("2023-01-01")
    assert windows[0].train_end == pd.Timestamp("2023-12-31")
    assert windows[0].test_start == pd.Timestamp("2024-01-01")
    assert windows[0].test_end == pd.Timestamp("2024-03-31")
    assert windows[1].train_end == pd.Timestamp("2024-03-31")
    assert windows[1].test_start == pd.Timestamp("2024-04-01")
    assert windows[1].test_end == pd.Timestamp("2024-06-30")


def test_walk_forward_score_penalizes_sample_size_and_drawdown() -> None:
    assert walk_forward_score(avg_net_return=0.01, num_trades=4, max_drawdown=0.0) == pytest.approx(0.02)
    assert walk_forward_score(avg_net_return=0.01, num_trades=0, max_drawdown=0.0) == -float("inf")
    assert walk_forward_score(avg_net_return=0.01, num_trades=4, max_drawdown=-0.02) == pytest.approx(0.01)


def test_run_walk_forward_writes_reports_and_oos_curve(tmp_path: Path) -> None:
    db_path = tmp_path / "taiwan_trading.duckdb"
    output_dir = tmp_path / "reports"
    init_db(db_path)
    with get_connection(db_path) as conn:
        upsert_dataframe(conn, "daily_prices", synthetic_walk_forward_daily_prices(), ["symbol", "trade_date"])

    results, selected, equity = run_walk_forward(
        db_path=db_path,
        output_dir=output_dir,
        initial_train_start="2023-01-01",
        first_test_start="2024-01-01",
        final_test_end="2024-06-30",
        test_months=3,
        top_n=1,
        min_train_trades=1,
        fixed_notional_twd=200_000.0,
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
    assert len(selected) == 2
    assert not equity.empty
    assert set(results["window_id"]) == {"WF001", "WF002"}
    assert set(results["selected_rank"]) == {1}
    assert set(results["path_assumption"]) == {"close_only"}
    assert set(results["oos_trades"]) == {1}
    assert (results["oos_net_pnl"] > 0).all()
    assert "path_assumption_selection_share" in selected.columns
    assert set(selected["path_assumption_selection_share"]) == {1.0}
    assert equity["overall_cumulative_net_pnl"].iloc[-1] == pytest.approx(equity["daily_net_pnl"].sum())

    assert (output_dir / "walk_forward_results.csv").exists()
    assert (output_dir / "walk_forward_selected_configs.csv").exists()
    assert (output_dir / "walk_forward_oos_equity_curve.csv").exists()
