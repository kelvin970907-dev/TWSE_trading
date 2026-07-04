from __future__ import annotations

import pandas as pd

from src.reports.diagnose_closed_limit_up_execution import (
    compute_execution_features,
    run_execution_stress_tests,
    run_fill_haircut_monte_carlo,
    summarize_execution_buckets,
)


def synthetic_execution_trades() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "window_id": "CLUWF001",
                "selected_rank": 1,
                "config_hash": "hard",
                "trade_id": "hard",
                "event_id": "hard-event",
                "symbol": "7001",
                "market": "TPEX",
                "signal_date": "2024-01-02",
                "entry_date": "2024-01-02",
                "exit_date": "2024-01-03",
                "day0_open": 55.0,
                "day0_high": 55.0,
                "day0_low": 54.8,
                "day0_close": 55.0,
                "limit_up_price": 55.0,
                "day0_turnover_twd": 800_000_000.0,
                "day0_volume_shares": 10_000_000,
                "trades": 1000,
                "volume_ratio_20d": 2.0,
                "consecutive_limit_up_count": 1,
                "prior_5d_return": 0.05,
                "prior_20d_return": 0.10,
                "day1_open_gap": 0.02,
                "net_return": 0.015,
                "net_pnl": 1500.0,
                "buy_notional": 100_000.0,
                "fill_quality_score": 65,
                "base_fill_reason": "test",
            },
            {
                "window_id": "CLUWF001",
                "selected_rank": 1,
                "config_hash": "fillable",
                "trade_id": "fillable",
                "event_id": "fillable-event",
                "symbol": "7002",
                "market": "TPEX",
                "signal_date": "2024-01-04",
                "entry_date": "2024-01-04",
                "exit_date": "2024-01-05",
                "day0_open": 50.0,
                "day0_high": 55.0,
                "day0_low": 52.0,
                "day0_close": 55.0,
                "limit_up_price": 55.0,
                "day0_turnover_twd": 700_000_000.0,
                "day0_volume_shares": 12_000_000,
                "trades": 1200,
                "volume_ratio_20d": 3.0,
                "consecutive_limit_up_count": 1,
                "prior_5d_return": 0.04,
                "prior_20d_return": 0.09,
                "day1_open_gap": 0.03,
                "net_return": 0.02,
                "net_pnl": 2000.0,
                "buy_notional": 100_000.0,
                "fill_quality_score": 85,
                "base_fill_reason": "test",
            },
            {
                "window_id": "CLUWF001",
                "selected_rank": 1,
                "config_hash": "possible",
                "trade_id": "possible",
                "event_id": "possible-event",
                "symbol": "7003",
                "market": "TPEX",
                "signal_date": "2024-01-08",
                "entry_date": "2024-01-08",
                "exit_date": "2024-01-09",
                "day0_open": 50.0,
                "day0_high": 51.5,
                "day0_low": 50.5,
                "day0_close": 51.5,
                "limit_up_price": 51.5,
                "day0_turnover_twd": 300_000_000.0,
                "day0_volume_shares": 6_000_000,
                "trades": 900,
                "volume_ratio_20d": 2.5,
                "consecutive_limit_up_count": 1,
                "prior_5d_return": 0.03,
                "prior_20d_return": 0.08,
                "day1_open_gap": -0.01,
                "net_return": -0.02,
                "net_pnl": -2000.0,
                "buy_notional": 100_000.0,
                "fill_quality_score": 60,
                "base_fill_reason": "test",
            },
        ]
    )


def test_hard_fill_bucket_detection() -> None:
    diagnostic = compute_execution_features(synthetic_execution_trades())
    hard = diagnostic[diagnostic["trade_id"] == "hard"].iloc[0]

    assert hard["execution_risk_bucket"] == "likely_hard_fill"
    assert bool(hard["day0_high_equals_close"]) is True
    assert bool(hard["day0_close_equals_limit_up"]) is True


def test_likely_fillable_bucket_detection() -> None:
    diagnostic = compute_execution_features(synthetic_execution_trades())
    fillable = diagnostic[diagnostic["trade_id"] == "fillable"].iloc[0]

    assert fillable["execution_risk_bucket"] == "likely_fillable"
    assert fillable["day0_range_pct"] >= 0.03
    assert fillable["day0_turnover_twd"] >= 500_000_000


def test_removing_hard_fill_trades_changes_summary() -> None:
    diagnostic = compute_execution_features(synthetic_execution_trades())
    stress = run_execution_stress_tests(diagnostic)

    baseline = stress[stress["scenario"] == "baseline_best_strategy"].iloc[0]
    remove_hard = stress[stress["scenario"] == "remove_likely_hard_fill"].iloc[0]

    assert baseline["kept_trades"] == 3
    assert baseline["total_net_pnl"] == 1500.0
    assert remove_hard["kept_trades"] == 2
    assert remove_hard["removed_trades"] == 1
    assert remove_hard["total_net_pnl"] == 0.0

    bucket_summary = summarize_execution_buckets(diagnostic)
    assert set(bucket_summary["execution_risk_bucket"].astype(str)) >= {
        "likely_hard_fill",
        "possible_fill",
        "likely_fillable",
    }


def test_monte_carlo_fill_haircut_columns() -> None:
    diagnostic = compute_execution_features(synthetic_execution_trades())
    monte_carlo = run_fill_haircut_monte_carlo(
        diagnostic,
        fill_rates=(0.25, 0.50),
        iterations=50,
        random_seed=7,
    )

    expected = {
        "fill_rate",
        "iterations",
        "mean_filled_trades",
        "median_filled_trades",
        "mean_total_net_pnl",
        "median_total_net_pnl",
        "p05_total_net_pnl",
        "p95_total_net_pnl",
        "mean_avg_net_return",
        "median_avg_net_return",
        "p05_avg_net_return",
        "p95_avg_net_return",
    }
    assert set(monte_carlo.columns) == expected
    assert monte_carlo["fill_rate"].tolist() == [0.25, 0.50]
    assert set(monte_carlo["iterations"]) == {50}
