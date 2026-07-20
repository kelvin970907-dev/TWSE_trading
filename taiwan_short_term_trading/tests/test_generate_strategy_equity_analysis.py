from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.reports.generate_strategy_equity_analysis import (
    INITIAL_CAPITAL_TWD,
    PAPER_PROFILE_SUMMARY_OUTPUT,
    PLOT_OUTPUTS,
    build_paper_curve_frames,
    calculate_drawdown_daily,
    calculate_monthly_returns,
    expand_daily_equity,
    longest_drawdown_duration,
    refresh_paper_strategy_analysis,
    resolve_reports_and_analysis_dirs,
)


def test_expand_daily_equity_carries_forward_no_trade_dates() -> None:
    dates = pd.DatetimeIndex(pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"]))
    raw = pd.DataFrame(
        [
            {
                "date": pd.Timestamp("2024-01-03"),
                "end_equity": 1_010_000.0,
                "positions_entered": 1,
                "gross_entry_notional": 200_000.0,
                "capital_utilization": 0.2,
            }
        ]
    )

    expanded = expand_daily_equity(raw, dates, strategy="test_strategy")

    assert expanded["end_equity"].tolist() == [INITIAL_CAPITAL_TWD, 1_010_000.0, 1_010_000.0]
    assert expanded["daily_net_pnl"].tolist() == [0.0, 10_000.0, 0.0]
    assert expanded["positions_entered"].tolist() == [0, 1, 0]


def test_longest_drawdown_duration_counts_unrecovered_period() -> None:
    daily = pd.DataFrame(
        {
            "strategy": ["test"] * 5,
            "date": pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08"]),
            "end_equity": [1_000_000.0, 990_000.0, 995_000.0, 980_000.0, 1_001_000.0],
        }
    )
    drawdown = calculate_drawdown_daily(daily)

    duration = longest_drawdown_duration(drawdown)

    assert duration["trading_days"] == 3
    assert duration["calendar_days"] == 2


def test_monthly_returns_include_rolling_windows() -> None:
    dates = pd.DatetimeIndex(pd.date_range("2024-01-31", periods=6, freq="ME"))
    raw = pd.DataFrame(
        {
            "date": dates,
            "end_equity": [1_010_000.0, 1_020_100.0, 1_030_301.0, 1_040_604.01, 1_051_010.05, 1_061_520.15],
            "positions_entered": [1, 1, 1, 1, 1, 1],
            "gross_entry_notional": [100_000.0] * 6,
            "capital_utilization": [0.1] * 6,
        }
    )
    daily = expand_daily_equity(raw, dates, strategy="test_strategy")

    monthly = calculate_monthly_returns(daily)

    assert len(monthly) == 6
    assert monthly["rolling_3m_return"].dropna().iloc[0] == pytest.approx(0.030301, rel=1e-6)
    assert monthly["rolling_6m_return"].dropna().iloc[0] == pytest.approx(0.06152015, rel=1e-6)


def test_resolve_reports_and_analysis_dirs_targets_strategy_analysis(tmp_path: Path) -> None:
    reports_root, analysis_dir = resolve_reports_and_analysis_dirs(tmp_path / "reports")

    assert reports_root == tmp_path / "reports"
    assert analysis_dir == tmp_path / "reports" / "strategy_analysis"

    reports_root_again, analysis_dir_again = resolve_reports_and_analysis_dirs(analysis_dir)
    assert reports_root_again == tmp_path / "reports"
    assert analysis_dir_again == analysis_dir


def test_paper_curve_frames_and_refresh_outputs(tmp_path: Path) -> None:
    reports_root = tmp_path / "reports"
    ledger_path = reports_root / "live_signals" / "closed_limit_up_paper_ledger.csv"
    ledger_path.parent.mkdir(parents=True)
    pd.DataFrame(
        [
            {
                "profile_name": "profile_a",
                "status": "evaluated",
                "day1_trade_date": "2026-07-02",
                "signal_date": "2026-07-01",
                "net_pnl": 1000.0,
                "net_return": 0.01,
            },
            {
                "profile_name": "profile_a",
                "status": "evaluated",
                "day1_trade_date": "2026-07-03",
                "signal_date": "2026-07-02",
                "net_pnl": -500.0,
                "net_return": -0.005,
            },
            {
                "profile_name": "profile_b",
                "status": "missing_day1_data",
                "day1_trade_date": "",
                "signal_date": "2026-07-02",
                "net_pnl": 0.0,
                "net_return": 0.0,
            },
        ]
    ).to_csv(ledger_path, index=False)

    equity, drawdown, summary = build_paper_curve_frames(ledger_path)
    refreshed_summary, paths = refresh_paper_strategy_analysis(output_dir=reports_root, ledger_path=ledger_path)

    assert equity["end_equity"].iloc[-1] == pytest.approx(INITIAL_CAPITAL_TWD + 500.0)
    assert drawdown["drawdown_twd"].min() == pytest.approx(-500.0)
    assert summary.iloc[0]["profile_name"] == "profile_a"
    assert refreshed_summary.iloc[0]["net_pnl"] == pytest.approx(500.0)
    assert (reports_root / "strategy_analysis" / PAPER_PROFILE_SUMMARY_OUTPUT).exists()
    assert paths["paper_equity"] == reports_root / "strategy_analysis" / PLOT_OUTPUTS["paper_equity"]
    assert paths["paper_equity"].exists()
    assert paths["paper_drawdown"].exists()


def test_paper_refresh_writes_placeholders_with_empty_ledger(tmp_path: Path) -> None:
    reports_root = tmp_path / "reports"

    summary, paths = refresh_paper_strategy_analysis(output_dir=reports_root)

    assert summary.empty
    assert (reports_root / "strategy_analysis" / PAPER_PROFILE_SUMMARY_OUTPUT).exists()
    assert paths["paper_equity"].exists()
    assert paths["paper_drawdown"].exists()
