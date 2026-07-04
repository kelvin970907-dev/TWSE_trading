from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.reports.generate_limit_momentum_report import PLOT_FILENAMES, generate_limit_momentum_report


def test_generate_limit_momentum_report_writes_markdown_tables_and_plots(tmp_path: Path) -> None:
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    write_sample_report_csvs(reports_dir)

    output = generate_limit_momentum_report(
        reports_dir=reports_dir,
        db_path=tmp_path / "missing.duckdb",
    )

    text = output.read_text(encoding="utf-8")
    assert output == reports_dir / "limit_momentum_research_report.md"
    assert "## 1. Executive summary" in text
    assert "## 6. Do +8-9% non-limit stocks continue upward after next open?" in text
    assert "Tentatively yes" in text
    assert "near_limit_8_9" in text
    assert "feature_performance_by_bucket.csv" in text
    assert "grid_search_results.csv" in text
    assert "plots/event_count_by_year.png" in text

    for filename in PLOT_FILENAMES.values():
        plot_path = reports_dir / "plots" / filename
        assert plot_path.exists()
        assert plot_path.stat().st_size > 0


def write_sample_report_csvs(reports_dir: Path) -> None:
    event_daily = pd.DataFrame(
        [
            {
                "event_id": "e1",
                "symbol": "1001",
                "trade_date": "2024-01-31",
                "market": "TWSE",
                "event_type": "near_limit_8_9",
                "day0_return": 0.085,
                "day0_turnover_twd": 120_000_000,
                "next_open_return": 0.012,
                "next_close_return": 0.008,
                "open_to_close_return": -0.004,
                "approx_net_next_open_return": 0.004,
                "approx_net_next_close_return": 0.001,
                "win_next_open": True,
                "win_next_close": True,
                "hit_plus_2_intraday": False,
                "year": 2024,
            },
            {
                "event_id": "e2",
                "symbol": "1002",
                "trade_date": "2024-02-29",
                "market": "TWSE",
                "event_type": "near_limit_9_10",
                "day0_return": 0.095,
                "day0_turnover_twd": 220_000_000,
                "next_open_return": -0.003,
                "next_close_return": -0.01,
                "open_to_close_return": -0.007,
                "approx_net_next_open_return": -0.011,
                "approx_net_next_close_return": -0.018,
                "win_next_open": False,
                "win_next_close": False,
                "hit_plus_2_intraday": False,
                "year": 2024,
            },
        ]
    )
    event_daily.to_csv(reports_dir / "event_study_daily.csv", index=False)

    event_summary = pd.DataFrame(
        [
            {
                "summary_level": "by_event_type",
                "event_type": "near_limit_8_9",
                "event_count": 1,
                "day1_observation_count": 1,
                "avg_day0_return": 0.085,
                "mean_next_open_return": 0.012,
                "p_value_next_open_return_gt_0": 0.08,
                "mean_next_close_return": 0.008,
                "p_value_next_close_return_gt_0": 0.11,
                "mean_open_to_close_return": -0.004,
                "mean_approx_net_next_open_return": 0.004,
                "mean_approx_net_next_close_return": 0.001,
                "win_next_open_rate": 1.0,
                "win_next_close_rate": 1.0,
                "hit_plus_2_intraday_rate": 0.0,
            },
            {
                "summary_level": "by_event_type",
                "event_type": "near_limit_9_10",
                "event_count": 1,
                "day1_observation_count": 1,
                "avg_day0_return": 0.095,
                "mean_next_open_return": -0.003,
                "p_value_next_open_return_gt_0": 0.7,
                "mean_next_close_return": -0.01,
                "p_value_next_close_return_gt_0": 0.8,
                "mean_open_to_close_return": -0.007,
                "mean_approx_net_next_open_return": -0.011,
                "mean_approx_net_next_close_return": -0.018,
                "win_next_open_rate": 0.0,
                "win_next_close_rate": 0.0,
                "hit_plus_2_intraday_rate": 0.0,
            },
        ]
    )
    event_summary.to_csv(reports_dir / "event_study_summary.csv", index=False)

    pd.DataFrame(
        [
            {
                "feature_name": "foreign_net_buy_to_turnover",
                "feature_bucket": "0_3%",
                "event_type": "near_limit_8_9",
                "event_count": 1,
                "mean_next_open_return": 0.012,
                "mean_next_close_return": 0.008,
                "mean_approx_net_open_to_close_return": -0.01,
            }
        ]
    ).to_csv(reports_dir / "feature_performance_by_bucket.csv", index=False)

    strategy_trades = pd.DataFrame(
        [
            {
                "trade_id": "t1",
                "event_type": "near_limit_8_9",
                "entry_date": "2024-02-01",
                "net_pnl": 800,
                "net_return": 0.008,
                "exit_reason": "close_exit",
                "day0_turnover_twd": 120_000_000,
                "take_profit_pct": 0.03,
                "stop_loss_pct": 0.02,
            },
            {
                "trade_id": "t2",
                "event_type": "near_limit_9_10",
                "entry_date": "2024-03-01",
                "net_pnl": -500,
                "net_return": -0.005,
                "exit_reason": "stop_loss",
                "day0_turnover_twd": 220_000_000,
                "take_profit_pct": 0.02,
                "stop_loss_pct": 0.01,
            },
        ]
    )
    strategy_trades.to_csv(reports_dir / "strategy_limit_momentum_trades.csv", index=False)

    pd.DataFrame(
        [
            {
                "summary_level": "overall",
                "path_assumption": "close_only",
                "number_of_trades": 2,
                "win_rate": 0.5,
                "average_gross_return": 0.006,
                "average_net_return": 0.0015,
                "median_net_return": 0.0015,
                "total_net_pnl": 300,
                "profit_factor": 1.6,
                "max_drawdown": -500,
                "average_turnover": 170_000_000,
            },
            {
                "summary_level": "overall",
                "path_assumption": "pessimistic",
                "number_of_trades": 2,
                "win_rate": 0.5,
                "average_net_return": -0.001,
                "median_net_return": -0.001,
                "total_net_pnl": -200,
                "profit_factor": 0.8,
                "max_drawdown": -600,
                "average_turnover": 170_000_000,
            },
        ]
    ).to_csv(reports_dir / "strategy_limit_momentum_summary.csv", index=False)

    pd.DataFrame(
        [
            {
                "summary_level": "overall",
                "number_of_trades": 1,
                "win_rate": 1.0,
                "average_gross_return": 0.02,
                "average_net_return": 0.012,
                "median_net_return": 0.012,
                "total_net_pnl": 1200,
                "profit_factor": float("inf"),
                "max_drawdown": 0,
                "average_holding_minutes": 30,
                "average_open_gap_pct": 0.02,
                "average_open_volume_ratio": 3.0,
            }
        ]
    ).to_csv(reports_dir / "intraday_opening_summary.csv", index=False)

    grid_results = pd.DataFrame(
        [
            {
                "event_types": "near_limit_8_9",
                "market": "TWSE",
                "path_assumption": "pessimistic",
                "min_turnover_twd": 50_000_000,
                "min_volume_ratio_20d": 1.5,
                "min_close_location": 0.8,
                "take_profit_pct": 0.03,
                "stop_loss_pct": 0.02,
                "train_trades": 10,
                "train_avg_net_return": 0.005,
                "train_max_drawdown": -1000,
                "test_trades": 4,
                "test_avg_net_return": 0.004,
                "test_net_pnl": 1600,
                "test_max_drawdown": -500,
                "test_capacity_proxy_twd": 6_000_000,
                "test_avg_liquidity_twd": 160_000_000,
            },
            {
                "event_types": "near_limit_9_10",
                "market": "TWSE",
                "path_assumption": "close_only",
                "min_turnover_twd": 50_000_000,
                "min_volume_ratio_20d": 1.5,
                "min_close_location": 0.8,
                "take_profit_pct": 0.02,
                "stop_loss_pct": 0.01,
                "train_trades": 8,
                "train_avg_net_return": -0.001,
                "train_max_drawdown": -1200,
                "test_trades": 3,
                "test_avg_net_return": -0.002,
                "test_net_pnl": -600,
                "test_max_drawdown": -700,
                "test_capacity_proxy_twd": 5_000_000,
                "test_avg_liquidity_twd": 130_000_000,
            },
        ]
    )
    grid_results.to_csv(reports_dir / "grid_search_results.csv", index=False)
    grid_results.head(1).to_csv(reports_dir / "grid_search_top_train.csv", index=False)
    grid_results.head(1).to_csv(reports_dir / "grid_search_top_test.csv", index=False)

    walk_forward_results = pd.DataFrame(
        [
            {
                "window_id": "WF001",
                "selected_rank": 1,
                "event_types": "near_limit_8_9",
                "market": "TWSE",
                "path_assumption": "pessimistic",
                "score": 0.02,
                "train_trades": 10,
                "train_avg_net_return": 0.005,
                "oos_trades": 4,
                "oos_avg_net_return": 0.004,
                "oos_net_pnl": 1600,
                "oos_max_drawdown": -500,
                "oos_capacity_proxy_twd": 6_000_000,
            }
        ]
    )
    walk_forward_results.to_csv(reports_dir / "walk_forward_results.csv", index=False)
    walk_forward_results.to_csv(reports_dir / "walk_forward_selected_configs.csv", index=False)

    pd.DataFrame(
        [
            {
                "window_id": "WF001",
                "selected_rank": 1,
                "config_hash": "abc",
                "trade_date": "2025-01-02",
                "daily_trades": 1,
                "daily_net_pnl": 1000,
                "daily_buy_notional": 100_000,
                "daily_return": 0.01,
                "cumulative_net_pnl": 1000,
                "overall_cumulative_net_pnl": 1000,
            },
            {
                "window_id": "WF001",
                "selected_rank": 1,
                "config_hash": "abc",
                "trade_date": "2025-01-03",
                "daily_trades": 1,
                "daily_net_pnl": -300,
                "daily_buy_notional": 100_000,
                "daily_return": -0.003,
                "cumulative_net_pnl": 700,
                "overall_cumulative_net_pnl": 700,
            },
        ]
    ).to_csv(reports_dir / "walk_forward_oos_equity_curve.csv", index=False)
