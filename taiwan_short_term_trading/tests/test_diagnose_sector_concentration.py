from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.db import get_connection, init_db, upsert_dataframe
from src.reports.diagnose_sector_concentration import (
    CONCENTRATION_OUTPUT,
    DIAGNOSTIC_OUTPUT,
    INDUSTRY_SUMMARY_OUTPUT,
    REPORT_OUTPUT,
    SECTOR_SUMMARY_OUTPUT,
    STRESS_OUTPUT,
    generate_sector_concentration_diagnostic,
)


def test_sector_concentration_diagnostic_joins_and_writes_outputs(tmp_path: Path) -> None:
    db_path = tmp_path / "taiwan_trading.duckdb"
    oos_path = tmp_path / "oos_trades.csv"
    output_dir = tmp_path / "reports"
    seed_sector_map(db_path)
    write_oos_trades(oos_path)

    diagnostic, sector_summary, industry_summary, concentration, stress, report_path = (
        generate_sector_concentration_diagnostic(
            db_path=db_path,
            oos_trades_path=oos_path,
            output_dir=output_dir,
        )
    )

    assert len(diagnostic) == 4
    assert set(diagnostic["sector"]) == {"Technology/Electronics", "Healthcare", "MISSING_SECTOR"}
    assert diagnostic.loc[diagnostic["symbol"] == "7001", "name"].iloc[0] == "Alpha"
    assert int(diagnostic["sector_missing"].sum()) == 1
    assert sector_summary.loc[sector_summary["sector"] == "Technology/Electronics", "trades"].iloc[0] == 2
    assert "Semiconductor" in set(industry_summary["industry"])
    assert (output_dir / DIAGNOSTIC_OUTPUT).exists()
    assert (output_dir / SECTOR_SUMMARY_OUTPUT).exists()
    assert (output_dir / INDUSTRY_SUMMARY_OUTPUT).exists()
    assert (output_dir / CONCENTRATION_OUTPUT).exists()
    assert (output_dir / STRESS_OUTPUT).exists()
    assert report_path == output_dir / REPORT_OUTPUT
    assert "Closed-Limit-Up Sector Concentration Diagnostic" in report_path.read_text(encoding="utf-8")


def test_concentration_metrics_are_calculated_correctly(tmp_path: Path) -> None:
    db_path = tmp_path / "taiwan_trading.duckdb"
    oos_path = tmp_path / "oos_trades.csv"
    seed_sector_map(db_path)
    write_oos_trades(oos_path)

    _, _, _, concentration, _, _ = generate_sector_concentration_diagnostic(
        db_path=db_path,
        oos_trades_path=oos_path,
        output_dir=tmp_path / "reports",
    )

    sector_row = concentration[concentration["level"] == "sector"].iloc[0]
    assert sector_row["groups"] == 3
    assert sector_row["top_1_trade_share"] == pytest.approx(0.5)
    assert sector_row["top_1_pnl_share"] == pytest.approx(1_500.0 / 1_600.0)
    assert sector_row["hhi_by_trades"] == pytest.approx(0.375)


def test_removing_top_sector_changes_summary(tmp_path: Path) -> None:
    db_path = tmp_path / "taiwan_trading.duckdb"
    oos_path = tmp_path / "oos_trades.csv"
    seed_sector_map(db_path)
    write_oos_trades(oos_path)

    _, _, _, _, stress, _ = generate_sector_concentration_diagnostic(
        db_path=db_path,
        oos_trades_path=oos_path,
        output_dir=tmp_path / "reports",
    )

    all_row = stress[stress["scenario"] == "trade_all"].iloc[0]
    removed = stress[stress["scenario"] == "remove_top_1_sector_by_pnl"].iloc[0]
    assert all_row["trades"] == 4
    assert all_row["net_pnl"] == pytest.approx(1_600.0)
    assert removed["trades"] == 2
    assert removed["net_pnl"] == pytest.approx(100.0)


def seed_sector_map(db_path: Path) -> None:
    init_db(db_path)
    with get_connection(db_path) as conn:
        upsert_dataframe(
            conn,
            "stock_sector_map",
            pd.DataFrame(
                [
                    {
                        "symbol": "7001",
                        "market": "TPEX",
                        "name": "Alpha",
                        "sector": "Technology/Electronics",
                        "industry": "Semiconductor",
                        "source": "unit",
                    },
                    {
                        "symbol": "7002",
                        "market": "TPEX",
                        "name": "Beta",
                        "sector": "Technology/Electronics",
                        "industry": "Semiconductor",
                        "source": "unit",
                    },
                    {
                        "symbol": "7003",
                        "market": "TPEX",
                        "name": "Gamma",
                        "sector": "Healthcare",
                        "industry": "Biotechnology/Medical",
                        "source": "unit",
                    },
                ]
            ),
            ["symbol", "market"],
        )


def write_oos_trades(path: Path) -> None:
    rows = [
        oos_row("7001", "2024-01-02", 0.02, 1_000.0),
        oos_row("7002", "2024-01-03", 0.01, 500.0),
        oos_row("7003", "2024-01-04", -0.005, -100.0),
        oos_row("7004", "2024-01-05", 0.004, 200.0),
    ]
    excluded = oos_row("8001", "2024-01-08", 0.05, 5_000.0)
    excluded["market_param"] = "TWSE"
    excluded["market"] = "TWSE"
    rows.append(excluded)
    pd.DataFrame(rows).to_csv(path, index=False)


def oos_row(symbol: str, signal_date: str, net_return: float, net_pnl: float) -> dict[str, object]:
    signal_ts = pd.Timestamp(signal_date)
    return {
        "window_id": "window",
        "selected_rank": 1,
        "config_hash": "best_config",
        "trade_id": f"trade_{symbol}",
        "event_id": f"event_{symbol}",
        "symbol": symbol,
        "market": "TPEX",
        "signal_date": signal_ts.date().isoformat(),
        "entry_date": signal_ts.date().isoformat(),
        "exit_date": (signal_ts + pd.Timedelta(days=1)).date().isoformat(),
        "net_return": net_return,
        "net_pnl": net_pnl,
        "buy_notional": 100_000.0,
        "market_param": "TPEX",
        "fill_assumption_param": "moderate",
        "min_fill_quality_score_param": 60.0,
        "min_turnover_twd_param": 500_000_000.0,
        "min_volume_ratio_20d_param": 1.5,
        "max_consecutive_limit_ups_param": 3,
        "day0_close": 50.0,
        "fill_quality_score": 70.0,
        "day0_turnover_twd": 700_000_000.0,
        "volume_ratio_20d": 2.0,
        "consecutive_limit_up_count": 1,
    }
