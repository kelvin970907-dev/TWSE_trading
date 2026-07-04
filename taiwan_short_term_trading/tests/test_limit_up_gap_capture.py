from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.backtests.costs import calculate_trade_costs
from src.backtests.event_study import generate_event_candidates
from src.backtests.limit_up_gap_capture import (
    apply_limit_up_gap_filters,
    build_limit_up_gap_frame,
    run_limit_up_gap_capture,
    summarize_limit_up_gap_study,
)
from src.db import get_connection, init_db, upsert_dataframe


def synthetic_limit_up_daily_prices() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    dates = pd.date_range("2024-01-01", periods=9, freq="D")
    closes = {
        "2001": [100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 115.5, 127.05, 125.0],
        "2002": [100.0, 99.0, 98.0, 97.0, 96.0, 95.0, 94.0, 103.4, 104.0],
    }
    limit_up_dates = {
        "2001": {pd.Timestamp("2024-01-07"), pd.Timestamp("2024-01-08")},
        "2002": {pd.Timestamp("2024-01-08")},
    }

    for symbol, values in closes.items():
        prev_close: float | None = None
        for trade_date, close in zip(dates, values):
            closed_limit_up = trade_date in limit_up_dates[symbol]
            daily_return = 0.0 if prev_close is None else close / prev_close - 1.0
            if trade_date == pd.Timestamp("2024-01-09") and symbol == "2001":
                open_price = 133.4025
                high = 136.0
                low = 124.0
                close_price = 125.0
            elif trade_date == pd.Timestamp("2024-01-09") and symbol == "2002":
                open_price = 105.468
                high = 108.0
                low = 103.0
                close_price = 104.0
            elif closed_limit_up:
                open_price = close
                high = close
                low = close
                close_price = close
            else:
                open_price = close
                high = close + 1.0
                low = close - 1.0
                close_price = close

            rows.append(
                {
                    "symbol": symbol,
                    "trade_date": trade_date,
                    "market": "TWSE",
                    "name": f"Stock {symbol}",
                    "open": open_price,
                    "high": high,
                    "low": low,
                    "close": close_price,
                    "volume_shares": 10_000 if closed_limit_up else 2_000,
                    "turnover_twd": 250_000_000.0 if closed_limit_up else 20_000_000.0,
                    "trades": 100,
                    "prev_close": prev_close,
                    "daily_return": daily_return,
                    "limit_up_price": close_price if closed_limit_up else (prev_close or close_price) * 1.1,
                    "limit_down_price": close_price * 0.9,
                    "touched_limit_up": closed_limit_up,
                    "touched_limit_down": False,
                    "closed_limit_up": closed_limit_up,
                    "closed_limit_down": False,
                    "source": "test",
                    "created_at": pd.Timestamp("2024-01-10"),
                }
            )
            prev_close = close_price
    return pd.DataFrame(rows)


def closed_limit_up_events(daily: pd.DataFrame) -> pd.DataFrame:
    return generate_event_candidates(
        daily,
        start="2024-01-08",
        end="2024-01-08",
        markets=["TWSE"],
    ).query("event_type == 'closed_limit_up'").reset_index(drop=True)


def test_build_limit_up_gap_frame_computes_gap_sequence_and_costs() -> None:
    daily = synthetic_limit_up_daily_prices()
    events = closed_limit_up_events(daily)

    study = build_limit_up_gap_frame(
        events,
        daily,
        slippage_bps_per_side=0.0,
        minimum_commission_twd=0.0,
        cost_shares=1000,
    )

    assert len(study) == 2
    second_limit = study[study["symbol"] == "2001"].iloc[0]
    first_limit = study[study["symbol"] == "2002"].iloc[0]

    assert second_limit["consecutive_limit_up_count"] == 2
    assert second_limit["prior_consecutive_limit_up_count"] == 1
    assert bool(second_limit["first_limit_up_in_sequence"]) is False
    assert bool(first_limit["first_limit_up_in_sequence"]) is True

    assert second_limit["day1_open_gap"] == pytest.approx(0.05)
    assert second_limit["day1_open_to_close"] == pytest.approx(125.0 / 133.4025 - 1.0)

    expected_overnight = calculate_trade_costs(
        side="long",
        entry_price=127.05,
        exit_price=133.4025,
        shares=1000,
        slippage_bps_per_side=0.0,
        minimum_commission_twd=0.0,
        is_day_trade=False,
    )
    assert second_limit["overnight_net_return"] == pytest.approx(expected_overnight["net_return"])
    assert second_limit["overnight_sell_tax"] == pytest.approx(expected_overnight["sell_tax"])

    expected_daytrade = calculate_trade_costs(
        side="long",
        entry_price=133.4025,
        exit_price=125.0,
        shares=1000,
        slippage_bps_per_side=0.0,
        minimum_commission_twd=0.0,
        is_day_trade=True,
    )
    assert second_limit["daytrade_net_open_to_close_return"] == pytest.approx(expected_daytrade["net_return"])
    assert second_limit["day0_price_bucket"] == "100_200"
    assert second_limit["day0_volume_shock_bucket"] == "2_5x"


def test_limit_up_gap_filters_and_summary() -> None:
    daily = synthetic_limit_up_daily_prices()
    study = build_limit_up_gap_frame(
        closed_limit_up_events(daily),
        daily,
        slippage_bps_per_side=0.0,
        minimum_commission_twd=0.0,
    )

    first_only = apply_limit_up_gap_filters(study, first_limit_up_only=True)
    assert first_only["symbol"].tolist() == ["2002"]

    capped_prior = apply_limit_up_gap_filters(study, max_prior_consecutive_limit_ups=0)
    assert capped_prior["symbol"].tolist() == ["2002"]

    min_price = apply_limit_up_gap_filters(study, min_price=120.0)
    assert min_price["symbol"].tolist() == ["2001"]

    summary = summarize_limit_up_gap_study(study)
    overall = summary[summary["summary_level"] == "overall"].iloc[0]
    assert overall["event_count"] == 2
    assert overall["day1_observation_count"] == 2
    assert overall["mean_day1_open_gap"] == pytest.approx(study["day1_open_gap"].mean())
    assert overall["mean_overnight_net_return"] == pytest.approx(study["overnight_net_return"].mean())

    by_count = summary[
        (summary["summary_level"] == "by_consecutive_limit_up_count")
        & (summary["consecutive_limit_up_count"] == 2)
    ].iloc[0]
    assert by_count["event_count"] == 1
    assert by_count["mean_day1_open_gap"] == pytest.approx(0.05)


def test_run_limit_up_gap_capture_writes_reports(tmp_path: Path) -> None:
    db_path = tmp_path / "taiwan_trading.duckdb"
    report_dir = tmp_path / "reports"
    daily = synthetic_limit_up_daily_prices()
    init_db(db_path)
    with get_connection(db_path) as conn:
        upsert_dataframe(conn, "daily_prices", daily, ["symbol", "trade_date"])

    study, summary = run_limit_up_gap_capture(
        db_path=db_path,
        start="2024-01-08",
        end="2024-01-08",
        markets=["TWSE"],
        min_turnover_twd=100_000_000.0,
        min_volume_ratio_20d=2.0,
        output_dir=report_dir,
        slippage_bps_per_side=0.0,
        minimum_commission_twd=0.0,
    )

    assert len(study) == 2
    assert not summary.empty
    assert (report_dir / "limit_up_gap_capture_event_study.csv").exists()
    assert (report_dir / "limit_up_gap_capture_summary.csv").exists()

    written = pd.read_csv(report_dir / "limit_up_gap_capture_event_study.csv")
    assert "overnight_net_return" in written.columns
    assert "daytrade_net_open_to_close_return" in written.columns
