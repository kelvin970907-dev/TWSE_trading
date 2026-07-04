from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.backtests.closed_limit_up_overnight import (
    STRATEGY_NAME,
    apply_strategy_filters,
    build_overnight_trades,
    run_closed_limit_up_overnight,
    summarize_overnight_trades,
)
from src.backtests.costs import calculate_trade_costs
from src.backtests.event_study import generate_event_candidates
from src.backtests.limit_up_gap_capture import build_limit_up_gap_frame
from src.db import get_connection, init_db, upsert_dataframe


def synthetic_overnight_daily_prices() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    dates = pd.date_range("2024-01-01", periods=9, freq="D")
    closes_by_symbol = {
        "3001": [50.0, 51.0, 52.0, 53.0, 54.0, 55.0, 56.0, 61.6, 64.0],
        "3002": [45.0, 46.0, 47.0, 48.0, 49.0, 50.0, 55.0, 60.5, 59.0],
        "3003": [120.0, 121.0, 122.0, 123.0, 124.0, 125.0, 126.0, 138.6, 140.0],
    }
    closed_limit_dates = {
        "3001": {pd.Timestamp("2024-01-08")},
        "3002": {pd.Timestamp("2024-01-07"), pd.Timestamp("2024-01-08")},
        "3003": {pd.Timestamp("2024-01-08")},
    }
    day1_opens = {
        "3001": 64.68,
        "3002": 61.105,
        "3003": 145.53,
    }

    for symbol, closes in closes_by_symbol.items():
        prev_close: float | None = None
        for trade_date, base_close in zip(dates, closes):
            closed_limit_up = trade_date in closed_limit_dates[symbol]
            daily_return = 0.0 if prev_close is None else base_close / prev_close - 1.0
            if trade_date == pd.Timestamp("2024-01-09"):
                open_price = day1_opens[symbol]
                high = open_price * 1.01
                low = open_price * 0.99
                close = base_close
            elif closed_limit_up:
                open_price = base_close
                high = base_close
                low = base_close
                close = base_close
            else:
                open_price = base_close
                high = base_close + 0.5
                low = base_close - 0.5
                close = base_close

            rows.append(
                {
                    "symbol": symbol,
                    "trade_date": trade_date,
                    "market": "TWSE",
                    "name": f"Stock {symbol}",
                    "open": open_price,
                    "high": high,
                    "low": low,
                    "close": close,
                    "volume_shares": 10_000 if closed_limit_up else 2_000,
                    "turnover_twd": 250_000_000.0 if closed_limit_up else 20_000_000.0,
                    "trades": 100,
                    "prev_close": prev_close,
                    "daily_return": daily_return,
                    "limit_up_price": close if closed_limit_up else (prev_close or close) * 1.1,
                    "limit_down_price": close * 0.9,
                    "touched_limit_up": closed_limit_up,
                    "touched_limit_down": False,
                    "closed_limit_up": closed_limit_up,
                    "closed_limit_down": False,
                    "source": "test",
                    "created_at": pd.Timestamp("2024-01-10"),
                }
            )
            prev_close = close
    return pd.DataFrame(rows)


def closed_limit_study() -> pd.DataFrame:
    daily = synthetic_overnight_daily_prices()
    events = generate_event_candidates(
        daily,
        start="2024-01-08",
        end="2024-01-08",
        markets=["TWSE"],
    )
    events = events[events["event_type"] == "closed_limit_up"].reset_index(drop=True)
    return build_limit_up_gap_frame(
        events,
        daily,
        slippage_bps_per_side=0.0,
        minimum_commission_twd=0.0,
    )


def test_build_overnight_trades_uses_normal_sell_tax_and_board_lots() -> None:
    study = closed_limit_study()
    trades = build_overnight_trades(
        study,
        fixed_notional_twd=100_000,
        commission_rate=0.001425,
        commission_discount=0.28,
        sell_tax_rate=0.003,
        slippage_bps_per_side=0.0,
        minimum_commission_twd=0.0,
    )

    assert trades["symbol"].tolist() == ["3001", "3002"]
    assert set(trades["shares"]) == {1000}
    assert "3003" not in trades["symbol"].tolist()

    first = trades[trades["symbol"] == "3001"].iloc[0]
    expected = calculate_trade_costs(
        side="long",
        entry_price=61.6,
        exit_price=64.68,
        shares=1000,
        commission_rate=0.001425,
        commission_discount=0.28,
        sell_tax_rate=0.003,
        slippage_bps_per_side=0.0,
        minimum_commission_twd=0.0,
        is_day_trade=False,
        normal_sell_tax_rate=0.003,
    )
    assert first["entry_date"] == pd.Timestamp("2024-01-08")
    assert first["exit_date"] == pd.Timestamp("2024-01-09")
    assert first["gross_return"] == pytest.approx(0.05)
    assert first["sell_tax"] == pytest.approx(expected["sell_tax"])
    assert first["net_return"] == pytest.approx(expected["net_return"])
    assert first["tax"] == pytest.approx(expected["sell_tax"])
    assert first["exit_reason"] == "day1_open_exit"


def test_strategy_filters_and_summary() -> None:
    study = closed_limit_study()
    first_only = apply_strategy_filters(study, only_first_limit_up=True)
    assert set(first_only["symbol"]) == {"3001", "3003"}

    capped = apply_strategy_filters(study, max_consecutive_limit_ups=1)
    assert set(capped["symbol"]) == {"3001", "3003"}

    prior_cap = apply_strategy_filters(study, exclude_if_prior_5d_return_above=0.10)
    assert "3002" not in set(prior_cap["symbol"])

    trades = build_overnight_trades(
        apply_strategy_filters(study, only_first_limit_up=True),
        fixed_notional_twd=100_000,
        commission_rate=0.001425,
        commission_discount=0.28,
        sell_tax_rate=0.003,
        slippage_bps_per_side=0.0,
        minimum_commission_twd=0.0,
    )
    summary = summarize_overnight_trades(trades)
    overall = summary[summary["summary_level"] == "overall"].iloc[0]
    assert overall["trades"] == 1
    assert overall["win_rate"] == 1.0
    assert overall["average_net_return"] == pytest.approx(trades["net_return"].mean())

    by_symbol = summary[summary["summary_level"] == "by_symbol"].iloc[0]
    assert by_symbol["symbol"] == "3001"
    assert by_symbol["trade_share"] == 1.0


def test_run_closed_limit_up_overnight_writes_reports_and_stores_trades(tmp_path: Path) -> None:
    db_path = tmp_path / "taiwan_trading.duckdb"
    report_dir = tmp_path / "reports"
    daily = synthetic_overnight_daily_prices()
    init_db(db_path)
    with get_connection(db_path) as conn:
        upsert_dataframe(conn, "daily_prices", daily, ["symbol", "trade_date"])

    trades, summary = run_closed_limit_up_overnight(
        db_path=db_path,
        start="2024-01-08",
        end="2024-01-08",
        markets=["TWSE"],
        fixed_notional_twd=100_000,
        min_turnover_twd=100_000_000,
        min_volume_ratio_20d=2.0,
        max_consecutive_limit_ups=1,
        only_first_limit_up=True,
        output_dir=report_dir,
        slippage_bps_per_side=0.0,
        minimum_commission_twd=0.0,
    )

    assert trades["symbol"].tolist() == ["3001"]
    assert not summary.empty
    assert (report_dir / "closed_limit_up_overnight_trades.csv").exists()
    assert (report_dir / "closed_limit_up_overnight_summary.csv").exists()

    with get_connection(db_path, read_only=True) as conn:
        stored_count = conn.execute(
            "SELECT COUNT(*) FROM backtest_trades WHERE strategy_name = ?",
            [STRATEGY_NAME],
        ).fetchone()[0]
        stored_tax = conn.execute(
            "SELECT tax FROM backtest_trades WHERE strategy_name = ?",
            [STRATEGY_NAME],
        ).fetchone()[0]

    assert stored_count == 1
    assert stored_tax == pytest.approx(trades.iloc[0]["sell_tax"])
