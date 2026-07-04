from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.backtests.costs import calculate_trade_costs
from src.backtests.intraday_opening_continuation import (
    add_intraday_vwap,
    run_intraday_opening_continuation,
    simulate_intraday_exit,
)
from src.db import get_connection, init_db, upsert_dataframe


def synthetic_event_candidates() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "event_id": "TWSE:2330:20240131:closed_limit_up:test",
                "symbol": "2330",
                "trade_date": pd.Timestamp("2024-01-31"),
                "market": "TWSE",
                "event_type": "closed_limit_up",
                "day0_return": 0.10,
                "day0_open": 95.0,
                "day0_high": 100.0,
                "day0_low": 94.0,
                "day0_close": 100.0,
                "day0_volume_shares": 1_000_000,
                "day0_turnover_twd": 100_000_000.0,
                "close_location": 1.0,
                "volume_ratio_20d": 3.0,
                "touched_limit_up": True,
                "closed_limit_up": True,
                "failed_limit_up": False,
                "next_trade_date": pd.Timestamp("2024-02-01"),
            }
        ]
    )


def synthetic_intraday_bars(*, vwap_fail: bool = False) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for minute, close, volume in [
        ("09:00", 100.0, 300),
        ("09:01", 100.1, 300),
        ("09:02", 100.2, 400),
    ]:
        rows.append(intraday_row("2024-01-30", minute, open_price=close, high=close + 0.1, low=close - 0.1, close=close, volume=volume))

    for minute, open_price, high, low, close, volume in [
        ("09:00", 102.0, 102.5, 101.9, 102.2, 2_000),
        ("09:01", 102.2, 102.7, 102.0, 102.6, 2_000),
        ("09:02", 102.6, 103.2, 102.5, 103.0, 2_000),
    ]:
        rows.append(intraday_row("2024-02-01", minute, open_price=open_price, high=high, low=low, close=close, volume=volume))

    if vwap_fail:
        rows.append(intraday_row("2024-02-01", "09:03", open_price=103.0, high=103.2, low=102.2, close=102.3, volume=1_000))
        rows.append(intraday_row("2024-02-01", "13:20", open_price=102.3, high=102.5, low=102.0, close=102.4, volume=1_000))
    else:
        rows.append(intraday_row("2024-02-01", "09:03", open_price=103.0, high=106.2, low=102.8, close=106.0, volume=1_000))
        rows.append(intraday_row("2024-02-01", "13:20", open_price=106.0, high=106.1, low=105.8, close=106.0, volume=1_000))

    return pd.DataFrame(rows)


def intraday_row(
    date: str,
    minute: str,
    *,
    open_price: float,
    high: float,
    low: float,
    close: float,
    volume: int,
) -> dict[str, object]:
    bar_time = pd.Timestamp(f"{date} {minute}:00")
    return {
        "symbol": "2330",
        "trade_date": pd.Timestamp(date),
        "market": "TWSE",
        "bar_time": bar_time,
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "volume_shares": volume,
        "turnover_twd": close * volume,
        "source": "test",
    }


def seed_intraday_strategy_db(db_path: Path, *, vwap_fail: bool = False) -> None:
    init_db(db_path)
    with get_connection(db_path) as conn:
        upsert_dataframe(conn, "event_candidates", synthetic_event_candidates(), ["event_id"])
        upsert_dataframe(conn, "intraday_bars", synthetic_intraday_bars(vwap_fail=vwap_fail), ["symbol", "bar_time"])


def test_run_intraday_opening_continuation_enters_and_takes_profit(tmp_path: Path) -> None:
    db_path = tmp_path / "taiwan_trading.duckdb"
    report_dir = tmp_path / "reports"
    seed_intraday_strategy_db(db_path)

    trades, summary = run_intraday_opening_continuation(
        db_path=db_path,
        event_types=["closed_limit_up"],
        entry_window_minutes=3,
        min_open_gap_pct=0.005,
        max_open_gap_pct=0.05,
        allowed_open_drawdown_pct=0.01,
        min_open_volume_ratio=2.0,
        take_profit_pct=0.03,
        stop_loss_pct=0.015,
        vwap_fail_exit=True,
        time_exit="13:20",
        fixed_notional_twd=200_000,
        output_dir=report_dir,
    )

    assert len(trades) == 1
    trade = trades.iloc[0]
    assert trade["entry_price"] == pytest.approx(103.0)
    assert trade["entry_time"] == pd.Timestamp("2024-02-01 09:02:00")
    assert trade["exit_reason"] == "take_profit"
    assert trade["exit_price"] == pytest.approx(106.09)
    assert trade["open_gap_pct"] == pytest.approx(0.02)
    assert trade["open_volume_ratio"] == pytest.approx(6.0)
    assert trade["shares"] == 1000

    expected_costs = calculate_trade_costs(
        side="long",
        entry_price=103.0,
        exit_price=106.09,
        shares=1000,
        commission_rate=0.001425,
        commission_discount=0.28,
        sell_tax_rate=0.0015,
        slippage_bps_per_side=5.0,
        minimum_commission_twd=20.0,
    )
    assert trade["net_pnl"] == pytest.approx(expected_costs["net_pnl"])

    with get_connection(db_path, read_only=True) as conn:
        stored_count = conn.execute(
            "SELECT COUNT(*) FROM backtest_trades WHERE strategy_name = 'intraday_opening_continuation'"
        ).fetchone()[0]

    assert stored_count == 1
    assert (report_dir / "intraday_opening_trades.csv").exists()
    assert (report_dir / "intraday_opening_summary.csv").exists()
    overall = summary[summary["summary_level"] == "overall"].iloc[0]
    assert overall["number_of_trades"] == 1


def test_intraday_opening_continuation_respects_gap_filter(tmp_path: Path) -> None:
    db_path = tmp_path / "taiwan_trading.duckdb"
    seed_intraday_strategy_db(db_path)

    trades, _ = run_intraday_opening_continuation(
        db_path=db_path,
        event_types=["closed_limit_up"],
        min_open_gap_pct=0.03,
        max_open_gap_pct=0.05,
        fixed_notional_twd=200_000,
        output_dir=tmp_path / "reports",
    )

    assert trades.empty


def test_intraday_opening_continuation_vwap_fail_exit(tmp_path: Path) -> None:
    db_path = tmp_path / "taiwan_trading.duckdb"
    seed_intraday_strategy_db(db_path, vwap_fail=True)

    trades, _ = run_intraday_opening_continuation(
        db_path=db_path,
        event_types=["closed_limit_up"],
        take_profit_pct=0.10,
        stop_loss_pct=0.05,
        vwap_fail_exit=True,
        fixed_notional_twd=200_000,
        output_dir=tmp_path / "reports",
    )

    assert len(trades) == 1
    assert trades.iloc[0]["exit_reason"] == "vwap_fail"
    assert trades.iloc[0]["exit_price"] == pytest.approx(102.3)


def test_intraday_opening_continuation_applies_sector_filter(tmp_path: Path) -> None:
    db_path = tmp_path / "taiwan_trading.duckdb"
    seed_intraday_strategy_db(db_path)
    with get_connection(db_path) as conn:
        upsert_dataframe(
            conn,
            "stock_sector_map",
            pd.DataFrame(
                [{"symbol": "2330", "market": "TWSE", "sector": "Tech", "industry": "Semis", "source": "test"}]
            ),
            ["symbol", "market"],
        )
        upsert_dataframe(
            conn,
            "sector_daily_features",
            pd.DataFrame(
                [
                    {
                        "sector": "Tech",
                        "trade_date": pd.Timestamp("2024-01-31"),
                        "equal_weight_return": 0.02,
                        "value_weight_return": 0.02,
                        "num_advancers": 1,
                        "num_decliners": 0,
                        "num_limit_up": 1,
                        "sector_momentum_5d": -0.03,
                        "sector_momentum_20d": 0.04,
                    }
                ]
            ),
            ["sector", "trade_date"],
        )

    trades, _ = run_intraday_opening_continuation(
        db_path=db_path,
        event_types=["closed_limit_up"],
        sector_filter="sector_return_positive_day0",
        fixed_notional_twd=200_000,
        output_dir=tmp_path / "reports_pass",
    )
    blocked_trades, _ = run_intraday_opening_continuation(
        db_path=db_path,
        event_types=["closed_limit_up"],
        sector_filter="sector_momentum_5d_positive",
        fixed_notional_twd=200_000,
        output_dir=tmp_path / "reports_fail",
    )

    assert len(trades) == 1
    assert blocked_trades.empty


def test_intraday_opening_continuation_applies_flow_filters(tmp_path: Path) -> None:
    db_path = tmp_path / "taiwan_trading.duckdb"
    seed_intraday_strategy_db(db_path)
    with get_connection(db_path) as conn:
        conn.execute(
            """
            UPDATE event_candidates
            SET foreign_net_buy_to_turnover = -0.02,
                investment_trust_net_buy_twd = 10000,
                margin_crowding_proxy = 4.0,
                short_balance_change_1d = 200
            """
        )

    passing, _ = run_intraday_opening_continuation(
        db_path=db_path,
        event_types=["closed_limit_up"],
        require_foreign_not_selling_heavily=True,
        require_investment_trust_buying=True,
        avoid_margin_overcrowded=True,
        prefer_short_balance_rising_before_limit_up=True,
        fixed_notional_twd=200_000,
        output_dir=tmp_path / "reports_pass",
    )

    with get_connection(db_path) as conn:
        conn.execute(
            """
            UPDATE event_candidates
            SET foreign_net_buy_to_turnover = -0.20,
                investment_trust_net_buy_twd = -1000,
                margin_crowding_proxy = 10.0,
                short_balance_change_1d = -50
            """
        )

    blocked, _ = run_intraday_opening_continuation(
        db_path=db_path,
        event_types=["closed_limit_up"],
        require_foreign_not_selling_heavily=True,
        require_investment_trust_buying=True,
        avoid_margin_overcrowded=True,
        prefer_short_balance_rising_before_limit_up=True,
        fixed_notional_twd=200_000,
        output_dir=tmp_path / "reports_blocked",
    )

    assert len(passing) == 1
    assert blocked.empty


def test_simulate_intraday_exit_checks_stop_before_take_profit() -> None:
    bars = add_intraday_vwap(
        pd.DataFrame(
            [
                intraday_row("2024-02-01", "09:02", open_price=100.0, high=100.0, low=100.0, close=100.0, volume=1000),
                intraday_row("2024-02-01", "09:03", open_price=100.0, high=104.0, low=98.0, close=103.0, volume=1000),
            ]
        )
    )

    exit_plan = simulate_intraday_exit(
        day_bars=bars,
        entry_time=pd.Timestamp("2024-02-01 09:02:00"),
        entry_price=100.0,
        day0_close=100.0,
        take_profit_pct=0.03,
        stop_loss_pct=0.01,
        vwap_fail_exit=False,
        time_exit="13:20",
        hold_locked_limit_up=False,
        locked_limit_up_pct=0.095,
    )

    assert exit_plan["exit_reason"] == "stop_loss"
    assert exit_plan["exit_price"] == pytest.approx(99.0)
