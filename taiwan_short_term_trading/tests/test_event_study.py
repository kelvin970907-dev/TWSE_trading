from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.backtests.event_study import (
    build_daily_study_frame,
    build_event_candidates,
    generate_event_candidates,
    run_daily_event_study,
    summarize_event_candidates,
    summarize_daily_study,
)
from src.db import get_connection, init_db, upsert_dataframe


def synthetic_daily_prices() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    event_specs = {
        "1001": {"close": 108.5, "high": 108.5, "low": 108.5, "daily_return": 0.085, "touched": False, "closed": False},
        "1002": {"close": 109.5, "high": 109.5, "low": 103.0, "daily_return": 0.095, "touched": False, "closed": False},
        "1003": {"close": 105.0, "high": 110.0, "low": 100.0, "daily_return": 0.050, "touched": True, "closed": False},
        "1004": {"close": 110.0, "high": 110.0, "low": 108.0, "daily_return": 0.100, "touched": True, "closed": True},
    }

    for symbol in event_specs:
        for day in pd.date_range("2024-01-01", periods=20, freq="D"):
            rows.append(
                {
                    "symbol": symbol,
                    "trade_date": day,
                    "market": "TWSE",
                    "name": f"Stock {symbol}",
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

        spec = event_specs[symbol]
        rows.append(
            {
                "symbol": symbol,
                "trade_date": pd.Timestamp("2024-01-31"),
                "market": "TWSE",
                "name": f"Stock {symbol}",
                "open": 101.0,
                "high": spec["high"],
                "low": spec["low"],
                "close": spec["close"],
                "volume_shares": 2_000,
                "turnover_twd": 250_000.0,
                "daily_return": spec["daily_return"],
                "limit_up_price": 110.0,
                "touched_limit_up": spec["touched"],
                "closed_limit_up": spec["closed"],
                "source": "test",
            }
        )
        rows.append(
            {
                "symbol": symbol,
                "trade_date": pd.Timestamp("2024-02-01"),
                "market": "TWSE",
                "name": f"Stock {symbol}",
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


def test_generate_event_candidates_from_synthetic_daily_prices() -> None:
    events = generate_event_candidates(
        synthetic_daily_prices(),
        start="2024-01-31",
        end="2024-01-31",
        markets=["TWSE"],
    )

    counts = events["event_type"].value_counts().to_dict()
    assert counts == {
        "near_limit_8_9": 1,
        "near_limit_9_10": 1,
        "touched_limit_not_closed": 1,
        "closed_limit_up": 1,
        "failed_limit_up": 1,
    }

    near_8_9 = events[events["event_type"] == "near_limit_8_9"].iloc[0]
    failed = events[events["event_type"] == "failed_limit_up"].iloc[0]

    assert near_8_9["symbol"] == "1001"
    assert near_8_9["event_id"].startswith("TWSE:1001:20240131:near_limit_8_9")
    assert near_8_9["volume_ratio_20d"] == pytest.approx(2.0)
    assert near_8_9["next_trade_date"] == pd.Timestamp("2024-02-01")
    assert pd.isna(near_8_9["close_location"])
    assert failed["symbol"] == "1003"
    assert failed["close_location"] == pytest.approx(0.5)
    assert bool(failed["failed_limit_up"]) is True


def test_build_event_candidates_stores_and_summarizes(tmp_path: Path) -> None:
    db_path = tmp_path / "taiwan_trading.duckdb"
    init_db(db_path)
    daily = synthetic_daily_prices()

    with get_connection(db_path) as conn:
        upsert_dataframe(conn, "daily_prices", daily, ["symbol", "trade_date"])
        upsert_dataframe(
            conn,
            "institutional_flows",
            pd.DataFrame(
                [
                    {
                        "symbol": "1001",
                        "trade_date": pd.Timestamp("2024-01-31"),
                        "market": "TWSE",
                        "foreign_net_buy_twd": 25_000.0,
                        "investment_trust_net_buy_twd": 5_000.0,
                        "dealer_net_buy_twd": -2_500.0,
                        "total_institutional_net_buy_twd": 27_500.0,
                        "source": "test",
                    }
                ]
            ),
            ["symbol", "trade_date"],
        )
        upsert_dataframe(
            conn,
            "margin_short",
            pd.DataFrame(
                [
                    {
                        "symbol": "1001",
                        "trade_date": pd.Timestamp("2024-01-30"),
                        "market": "TWSE",
                        "margin_buy_balance": 8_000,
                        "margin_sell_balance": 0,
                        "short_sale_balance": 500,
                        "day_trade_volume": 1_000,
                        "source": "test",
                    },
                    {
                        "symbol": "1001",
                        "trade_date": pd.Timestamp("2024-01-31"),
                        "market": "TWSE",
                        "margin_buy_balance": 10_000,
                        "margin_sell_balance": 0,
                        "short_sale_balance": 800,
                        "day_trade_volume": 1_200,
                        "source": "test",
                    },
                ]
            ),
            ["symbol", "trade_date"],
        )

    assert build_event_candidates(
        db_path=db_path,
        start="2024-01-31",
        end="2024-01-31",
        markets=["TWSE"],
    ) == 5
    assert build_event_candidates(
        db_path=db_path,
        start="2024-01-31",
        end="2024-01-31",
        markets=["TWSE"],
    ) == 5

    with get_connection(db_path, read_only=True) as conn:
        stored_count = conn.execute("SELECT COUNT(*) FROM event_candidates").fetchone()[0]
        flow_row = conn.execute(
            """
            SELECT foreign_net_buy_to_turnover, margin_balance_change_1d, short_balance_change_1d
            FROM event_candidates
            WHERE symbol = '1001'
              AND event_type = 'near_limit_8_9'
            """
        ).fetchone()
        summary = summarize_event_candidates(
            conn,
            start="2024-01-31",
            end="2024-01-31",
            markets=["TWSE"],
        )

    assert stored_count == 5
    assert flow_row[0] == pytest.approx(0.1)
    assert flow_row[1] == 2_000
    assert flow_row[2] == 300
    by_type = summary["by_event_type"].set_index("event_type")
    assert by_type.loc["near_limit_8_9", "event_count"] == 1
    assert by_type.loc["near_limit_8_9", "avg_day0_return"] == pytest.approx(0.085)
    assert by_type.loc["near_limit_8_9", "median_turnover_twd"] == 250_000.0
    assert summary["top_20_symbols"]["event_count"].sum() == 5


def test_build_daily_study_frame_computes_next_day_metrics() -> None:
    daily = synthetic_daily_prices()
    events = generate_event_candidates(
        daily,
        start="2024-01-31",
        end="2024-01-31",
        markets=["TWSE"],
    )
    day1 = daily.rename(
        columns={
            "trade_date": "day1_trade_date",
            "open": "day1_open",
            "high": "day1_high",
            "low": "day1_low",
            "close": "day1_close",
            "volume_shares": "day1_volume_shares",
            "turnover_twd": "day1_turnover_twd",
        }
    )[
        [
            "symbol",
            "market",
            "day1_trade_date",
            "day1_open",
            "day1_high",
            "day1_low",
            "day1_close",
            "day1_volume_shares",
            "day1_turnover_twd",
        ]
    ]

    study = build_daily_study_frame(
        events,
        day1,
        tax_bps=15.0,
        commission_bps=20.0,
        slippage_bps=5.0,
    )

    near_8_9 = study[study["event_type"] == "near_limit_8_9"].iloc[0]
    assert near_8_9["day1_trade_date"] == pd.Timestamp("2024-02-01")
    assert near_8_9["next_open_return"] == pytest.approx(100.0 / 108.5 - 1.0)
    assert near_8_9["next_high_return"] == pytest.approx(101.0 / 108.5 - 1.0)
    assert near_8_9["next_low_return"] == pytest.approx(99.0 / 108.5 - 1.0)
    assert near_8_9["next_close_return"] == pytest.approx(100.0 / 108.5 - 1.0)
    assert near_8_9["open_to_close_return"] == pytest.approx(0.0)
    assert near_8_9["open_to_high_return"] == pytest.approx(0.01)
    assert near_8_9["open_to_low_return"] == pytest.approx(-0.01)
    assert bool(near_8_9["win_next_open"]) is False
    assert bool(near_8_9["hit_plus_2_intraday"]) is False
    assert near_8_9["approx_daytrade_cost_bps"] == 40.0
    assert near_8_9["approx_net_next_open_return"] == pytest.approx(
        near_8_9["next_open_return"] - 0.004
    )
    assert near_8_9["turnover_bucket"] == "<10m"
    assert near_8_9["volume_ratio_20d_bucket"] == "1_2"
    assert near_8_9["close_location_bucket"] == "unknown"

    summary = summarize_daily_study(study)
    by_type = summary[
        (summary["summary_level"] == "by_event_type")
        & (summary["event_type"] == "near_limit_8_9")
    ].iloc[0]
    assert by_type["event_count"] == 1
    assert by_type["day1_observation_count"] == 1
    assert by_type["mean_next_open_return"] == pytest.approx(near_8_9["next_open_return"])
    assert by_type["mean_approx_net_next_open_return"] == pytest.approx(
        near_8_9["approx_net_next_open_return"]
    )


def test_run_daily_event_study_writes_reports(tmp_path: Path) -> None:
    db_path = tmp_path / "taiwan_trading.duckdb"
    report_dir = tmp_path / "reports"
    init_db(db_path)
    daily = synthetic_daily_prices()

    with get_connection(db_path) as conn:
        upsert_dataframe(conn, "daily_prices", daily, ["symbol", "trade_date"])

    study, summary = run_daily_event_study(
        db_path=db_path,
        start="2024-01-31",
        end="2024-01-31",
        markets=["TWSE"],
        output_dir=report_dir,
        tax_bps=15.0,
        commission_bps=20.0,
        slippage_bps=5.0,
    )

    assert len(study) == 5
    assert not summary.empty
    assert (report_dir / "event_study_daily.csv").exists()
    assert (report_dir / "event_study_summary.csv").exists()
    assert (report_dir / "feature_performance_by_bucket.csv").exists()

    written_daily = pd.read_csv(report_dir / "event_study_daily.csv")
    written_summary = pd.read_csv(report_dir / "event_study_summary.csv")
    written_feature_summary = pd.read_csv(report_dir / "feature_performance_by_bucket.csv")
    assert len(written_daily) == 5
    assert "mean_approx_net_next_close_return" in written_summary.columns
    assert "feature_name" in written_feature_summary.columns
