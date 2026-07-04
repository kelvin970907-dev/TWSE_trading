from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.data_collectors.collect_market_context import import_index_daily_csv
from src.db import init_db
from src.reports.diagnose_market_regime import (
    REGIME_REPORT_OUTPUT,
    REGIME_STRESS_OUTPUT,
    REGIME_SUMMARY_OUTPUT,
    REGIME_TRADES_OUTPUT,
    generate_market_regime_diagnostic,
    join_taiex_regime_features,
    run_market_regime_stress_tests,
)


def test_generate_market_regime_diagnostic_outputs_files(tmp_path: Path) -> None:
    db_path = tmp_path / "taiwan_trading.duckdb"
    oos_path = tmp_path / "oos_trades.csv"
    output_dir = tmp_path / "reports"
    signal_dates = seed_taiex_index(db_path, tmp_path)
    write_oos_trades(oos_path, signal_dates)

    trades, summary, stress, report_path = generate_market_regime_diagnostic(
        db_path=db_path,
        oos_trades_path=oos_path,
        output_dir=output_dir,
    )

    assert len(trades) == 3
    assert trades["index_data_available"].all()
    assert {
        "taiex_day0_return",
        "taiex_5d_return",
        "taiex_20d_return",
        "bull_regime",
        "bear_regime",
        "correction_regime",
        "strong_market_day",
        "weak_market_day",
    }.issubset(trades.columns)
    assert (output_dir / REGIME_TRADES_OUTPUT).exists()
    assert (output_dir / REGIME_SUMMARY_OUTPUT).exists()
    assert (output_dir / REGIME_STRESS_OUTPUT).exists()
    assert report_path == output_dir / REGIME_REPORT_OUTPUT
    assert "Closed-Limit-Up Market Regime Diagnostic" in report_path.read_text(encoding="utf-8")

    overall = summary[(summary["summary_level"] == "overall") & (summary["group"] == "all")].iloc[0]
    assert overall["trades"] == 3
    all_regimes = stress[stress["scenario"] == "trade_all_regimes"].iloc[0]
    assert all_regimes["trades"] == 3
    assert all_regimes["total_net_pnl"] == pytest.approx(2_100.0)


def test_market_regime_stress_tests_reduce_trade_sets(tmp_path: Path) -> None:
    db_path = tmp_path / "taiwan_trading.duckdb"
    signal_dates = seed_taiex_index(db_path, tmp_path)
    filtered_trades = best_strategy_oos_rows(signal_dates[:3])

    joined = join_taiex_regime_features(filtered_trades, db_path=db_path)
    stress = run_market_regime_stress_tests(joined)

    all_trades = stress.loc[stress["scenario"] == "trade_all_regimes", "trades"].iloc[0]
    bull_trades = stress.loc[stress["scenario"] == "trade_only_bull_regime", "trades"].iloc[0]
    not_bear_trades = stress.loc[stress["scenario"] == "trade_only_not_bear_regime", "trades"].iloc[0]

    assert all_trades == 3
    assert 0 <= bull_trades <= all_trades
    assert 0 <= not_bear_trades <= all_trades
    assert set(stress["scenario"]) == {
        "trade_all_regimes",
        "trade_only_bull_regime",
        "trade_only_not_bear_regime",
        "trade_only_taiex_day0_return_ge_0",
        "avoid_correction_regime",
        "avoid_weak_market_day",
        "bull_regime_and_avoid_weak_market_day",
    }


def test_market_regime_diagnostic_requires_index_data(tmp_path: Path) -> None:
    db_path = tmp_path / "taiwan_trading.duckdb"
    oos_path = tmp_path / "oos_trades.csv"
    init_db(db_path)
    write_oos_trades(oos_path, [pd.Timestamp("2024-03-01"), pd.Timestamp("2024-04-01")])

    with pytest.raises(ValueError, match="No TAIEX rows found"):
        generate_market_regime_diagnostic(
            db_path=db_path,
            oos_trades_path=oos_path,
            output_dir=tmp_path / "reports",
        )


def seed_taiex_index(db_path: Path, tmp_path: Path) -> list[pd.Timestamp]:
    dates = pd.bdate_range("2023-12-01", "2024-05-31")
    closes = []
    for i, _date in enumerate(dates):
        if i < 80:
            close = 100.0 + i * 0.45
        else:
            close = 136.0 - (i - 80) * 1.25
        closes.append(close)
    rows = []
    for date, close in zip(dates, closes, strict=True):
        rows.append(
            {
                "trade_date": date.date().isoformat(),
                "open": close * 0.995,
                "high": close * 1.01,
                "low": close * 0.99,
                "close": close,
                "volume": 1_000_000 + len(rows) * 1_000,
                "turnover_twd": 20_000_000_000 + len(rows) * 1_000_000,
            }
        )
    csv_path = tmp_path / "taiex.csv"
    pd.DataFrame(rows).to_csv(csv_path, index=False)
    import_index_daily_csv(db_path=db_path, csv_path=csv_path, default_index_symbol="TAIEX", source="unit_test")
    return [dates[65], dates[95], dates[115]]


def write_oos_trades(path: Path, signal_dates: list[pd.Timestamp]) -> None:
    rows = best_strategy_oos_rows(signal_dates).to_dict("records")
    excluded = rows[0].copy()
    excluded["trade_id"] = "excluded_wrong_market"
    excluded["symbol"] = "9999"
    excluded["market"] = "TWSE"
    excluded["market_param"] = "TWSE"
    rows.append(excluded)
    pd.DataFrame(rows).to_csv(path, index=False)


def best_strategy_oos_rows(signal_dates: list[pd.Timestamp]) -> pd.DataFrame:
    returns = [0.02, -0.01, 0.012]
    pnls = [2_000.0, -1_000.0, 1_100.0]
    rows = []
    for i, signal_date in enumerate(signal_dates):
        signal_ts = pd.Timestamp(signal_date).normalize()
        rows.append(
            {
                "window_id": f"window_{i}",
                "selected_rank": 1,
                "config_hash": "best_config",
                "trade_id": f"trade_{i}",
                "event_id": f"event_{i}",
                "symbol": f"7{i:03d}",
                "market": "TPEX",
                "signal_date": signal_ts.date().isoformat(),
                "entry_date": signal_ts.date().isoformat(),
                "exit_date": (signal_ts + pd.Timedelta(days=1)).date().isoformat(),
                "net_return": returns[i % len(returns)],
                "net_pnl": pnls[i % len(pnls)],
                "buy_notional": 100_000.0,
                "market_param": "TPEX",
                "fill_assumption_param": "moderate",
                "min_fill_quality_score_param": 60.0,
                "min_turnover_twd_param": 500_000_000.0,
                "min_volume_ratio_20d_param": 1.5,
                "max_consecutive_limit_ups_param": 3,
                "day0_close": 50.0 + i,
                "fill_quality_score": 70.0,
                "day0_turnover_twd": 700_000_000.0,
                "volume_ratio_20d": 2.0,
                "consecutive_limit_up_count": 1,
            }
        )
    return pd.DataFrame(rows).replace({np.nan: None})
