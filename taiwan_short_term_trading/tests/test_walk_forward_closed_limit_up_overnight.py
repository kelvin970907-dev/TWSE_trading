from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.backtests.walk_forward_closed_limit_up_overnight import (
    OvernightWFParameters,
    calculate_robust_score,
    generate_walk_forward_windows,
    run_walk_forward_closed_limit_up_overnight,
)
from src.db import get_connection, init_db, upsert_dataframe


def synthetic_closed_limit_up_walk_forward_prices() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    event_dates = [
        pd.Timestamp("2023-06-15"),
        pd.Timestamp("2024-02-15"),
        pd.Timestamp("2024-05-15"),
    ]
    previous_close = 100.0
    for event_date in event_dates:
        history_dates = pd.bdate_range(end=event_date - pd.Timedelta(days=1), periods=22)
        for trade_date in history_dates:
            close = 100.0
            rows.append(
                price_row(
                    trade_date=trade_date,
                    close=close,
                    prev_close=previous_close,
                    volume=1000,
                    turnover=20_000_000.0,
                )
            )
            previous_close = close

        event_close = previous_close * 1.10
        rows.append(
            price_row(
                trade_date=event_date,
                open_price=previous_close * 1.04,
                high=event_close,
                low=previous_close * 1.03,
                close=event_close,
                prev_close=previous_close,
                volume=5000,
                turnover=600_000_000.0,
                touched_limit_up=True,
                closed_limit_up=True,
                limit_up_price=event_close,
            )
        )
        previous_close = event_close

        day1_date = next_business_day(event_date)
        day1_open = event_close * 1.03
        rows.append(
            price_row(
                trade_date=day1_date,
                open_price=day1_open,
                high=day1_open * 1.01,
                low=day1_open * 0.99,
                close=day1_open * 0.995,
                prev_close=previous_close,
                volume=3000,
                turnover=100_000_000.0,
            )
        )
        previous_close = day1_open * 0.995

    return pd.DataFrame(rows).drop_duplicates(subset=["symbol", "trade_date"], keep="last")


def price_row(
    *,
    trade_date: pd.Timestamp,
    close: float,
    prev_close: float,
    volume: int,
    turnover: float,
    open_price: float | None = None,
    high: float | None = None,
    low: float | None = None,
    touched_limit_up: bool = False,
    closed_limit_up: bool = False,
    limit_up_price: float | None = None,
) -> dict[str, object]:
    open_value = close if open_price is None else open_price
    high_value = close + 0.5 if high is None else high
    low_value = close - 0.5 if low is None else low
    return {
        "symbol": "6001",
        "trade_date": trade_date,
        "market": "TWSE",
        "name": "WF Overnight Test",
        "open": open_value,
        "high": high_value,
        "low": low_value,
        "close": close,
        "volume_shares": volume,
        "turnover_twd": turnover,
        "trades": 100,
        "prev_close": prev_close,
        "daily_return": close / prev_close - 1.0,
        "limit_up_price": limit_up_price if limit_up_price is not None else prev_close * 1.10,
        "limit_down_price": prev_close * 0.90,
        "touched_limit_up": touched_limit_up,
        "touched_limit_down": False,
        "closed_limit_up": closed_limit_up,
        "closed_limit_down": False,
        "source": "test",
        "created_at": pd.Timestamp("2024-01-01"),
    }


def next_business_day(date: pd.Timestamp) -> pd.Timestamp:
    return pd.bdate_range(start=date + pd.Timedelta(days=1), periods=1)[0]


def test_generate_closed_limit_up_walk_forward_windows() -> None:
    windows = list(
        generate_walk_forward_windows(
            start="2023-01-01",
            first_test_start="2024-01-01",
            end="2024-06-24",
            test_months=3,
        )
    )

    assert [window.window_id for window in windows] == ["CLUWF001", "CLUWF002"]
    assert windows[0].train_start == pd.Timestamp("2023-01-01")
    assert windows[0].train_end == pd.Timestamp("2023-12-31")
    assert windows[0].test_start == pd.Timestamp("2024-01-01")
    assert windows[0].test_end == pd.Timestamp("2024-03-31")
    assert windows[1].train_end == pd.Timestamp("2024-03-31")
    assert windows[1].test_start == pd.Timestamp("2024-04-01")
    assert windows[1].test_end == pd.Timestamp("2024-06-24")


def test_closed_limit_up_robust_score_penalizes_bad_metrics() -> None:
    good = {
        "trades": 100,
        "avg_net_return": 0.01,
        "median_net_return": 0.01,
        "profit_factor": 2.0,
        "max_drawdown_pct": -0.01,
    }
    weak = {**good, "trades": 10, "median_net_return": -0.001, "profit_factor": 1.0}

    assert calculate_robust_score(good, overfit_penalty=0.0) == pytest.approx(0.5)
    assert calculate_robust_score(weak, overfit_penalty=1.0) < 0


def test_run_closed_limit_up_walk_forward_writes_outputs(tmp_path: Path) -> None:
    db_path = tmp_path / "taiwan_trading.duckdb"
    output_dir = tmp_path / "reports"
    init_db(db_path)
    with get_connection(db_path) as conn:
        upsert_dataframe(
            conn,
            "daily_prices",
            synthetic_closed_limit_up_walk_forward_prices(),
            ["symbol", "trade_date"],
        )

    results, selected, oos_trades, summary = run_walk_forward_closed_limit_up_overnight(
        db_path=db_path,
        start="2023-01-01",
        end="2024-06-24",
        output_dir=output_dir,
        first_test_start="2024-01-01",
        test_months=3,
        top_n=1,
        min_train_trades=1,
        fixed_notional_twd=200_000.0,
        slippage_bps_per_side=0.0,
        minimum_commission_twd=0.0,
        show_progress=False,
        markets=["TWSE"],
        min_turnover_values=[100_000_000.0],
        min_volume_ratio_values=[1.5],
        max_consecutive_limit_up_values=[1],
        only_first_limit_up_values=[True],
        min_price_values=[10.0],
        max_price_values=[200.0],
        fill_assumptions=["moderate", "conservative"],
        min_fill_quality_score_values=[40.0],
    )

    assert len(results) == 2
    assert len(selected) == 2
    assert len(oos_trades) == 2
    assert set(results["window_id"]) == {"CLUWF001", "CLUWF002"}
    assert set(results["selected_rank"]) == {1}
    assert set(results["oos_trades"]) == {1}
    assert (results["oos_net_pnl"] > 0).all()
    assert "fill_assumption_selection_share" in selected.columns
    assert summary[summary["summary_level"] == "overall"].iloc[0]["trades"] == 2

    assert (output_dir / "walk_forward_closed_limit_up_overnight_results.csv").exists()
    assert (output_dir / "walk_forward_closed_limit_up_overnight_selected_configs.csv").exists()
    assert (output_dir / "walk_forward_closed_limit_up_overnight_oos_trades.csv").exists()
    assert (output_dir / "walk_forward_closed_limit_up_overnight_summary.csv").exists()


def test_parameter_payload_is_stable() -> None:
    params = OvernightWFParameters(
        market="TWSE",
        min_turnover_twd=100_000_000,
        min_volume_ratio_20d=2.0,
        max_consecutive_limit_ups=1,
        only_first_limit_up=True,
        min_price=10.0,
        max_price=100.0,
        fill_assumption="moderate",
        min_fill_quality_score=40.0,
    )

    assert params.payload()["fill_assumption"] == "moderate"
    assert params.payload()["only_first_limit_up"] is True
