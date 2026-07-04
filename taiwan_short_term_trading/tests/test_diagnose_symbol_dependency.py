from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.db import get_connection, init_db, upsert_dataframe
from src.reports.diagnose_symbol_dependency import (
    COOLDOWN_TESTS_OUTPUT,
    HOTNESS_SUMMARY_OUTPUT,
    REMOVE_TOP_TESTS_OUTPUT,
    REPORT_OUTPUT,
    SYMBOL_CONCENTRATION_OUTPUT,
    SYMBOL_SUMMARY_OUTPUT,
    generate_symbol_dependency_diagnostic,
)


def test_symbol_dependency_diagnostic_outputs_expected_metrics(tmp_path: Path) -> None:
    db_path = tmp_path / "taiwan_trading.duckdb"
    oos_path = tmp_path / "oos.csv"
    output_dir = tmp_path / "reports"
    seed_database(db_path)
    write_oos_trades(oos_path)

    symbol_summary, concentration, hotness, cooldown, remove_top, report_path = generate_symbol_dependency_diagnostic(
        db_path=db_path,
        oos_trades_path=oos_path,
        output_dir=output_dir,
    )

    assert set(symbol_summary["symbol"]) == {"7001", "7002", "7003"}
    alpha = symbol_summary[symbol_summary["symbol"] == "7001"].iloc[0]
    assert alpha["name"] == "Alpha"
    assert alpha["trades"] == 3
    assert alpha["total_net_pnl"] == pytest.approx(1_400.0)
    assert alpha["active_quarters"] == 1
    assert alpha["max_trades_single_quarter"] == 3
    assert concentration["top_1_symbol_trade_share"].iloc[0] == pytest.approx(3 / 5)
    assert concentration["top_1_symbol_pnl_share"].iloc[0] == pytest.approx(1_400 / 1_900)

    hotness_counts = dict(zip(hotness["symbol_hotness_bucket"], hotness["trades"], strict=True))
    assert hotness_counts["first_seen"] == 3
    assert hotness_counts["repeat_within_20d"] == 1
    assert hotness_counts["repeat_21_60d"] == 1

    cooldown_5 = cooldown[cooldown["scenario"] == "cooldown_5_trading_days"].iloc[0]
    assert cooldown_5["trades"] == 4
    assert cooldown_5["total_net_pnl"] == pytest.approx(1_400.0)

    removed_top = remove_top[remove_top["scenario"] == "remove_top_1_symbol_by_pnl"].iloc[0]
    assert removed_top["trades"] == 2
    assert removed_top["total_net_pnl"] == pytest.approx(500.0)

    assert (output_dir / SYMBOL_SUMMARY_OUTPUT).exists()
    assert (output_dir / SYMBOL_CONCENTRATION_OUTPUT).exists()
    assert (output_dir / HOTNESS_SUMMARY_OUTPUT).exists()
    assert (output_dir / COOLDOWN_TESTS_OUTPUT).exists()
    assert (output_dir / REMOVE_TOP_TESTS_OUTPUT).exists()
    assert report_path == output_dir / REPORT_OUTPUT
    assert "Symbol Dependency Diagnostic" in report_path.read_text(encoding="utf-8")


def seed_database(db_path: Path) -> None:
    init_db(db_path)
    dates = pd.bdate_range("2024-01-02", "2024-03-01")
    daily_rows = []
    for date in dates:
        for symbol, name in [("7001", "Alpha"), ("7002", "Beta"), ("7003", "Gamma")]:
            daily_rows.append(daily_price_row(symbol, name, date))
    with get_connection(db_path) as conn:
        upsert_dataframe(conn, "daily_prices", pd.DataFrame(daily_rows), ["symbol", "trade_date"])
        upsert_dataframe(
            conn,
            "stock_sector_map",
            pd.DataFrame(
                [
                    {"symbol": "7001", "market": "TPEX", "name": "Alpha", "sector": "Technology/Electronics", "industry": "Semiconductor", "source": "unit"},
                    {"symbol": "7002", "market": "TPEX", "name": "Beta", "sector": "Technology/Electronics", "industry": "Electronic Components", "source": "unit"},
                    {"symbol": "7003", "market": "TPEX", "name": "Gamma", "sector": "Industrials/Other", "industry": "Other", "source": "unit"},
                ]
            ),
            ["symbol", "market"],
        )


def daily_price_row(symbol: str, name: str, trade_date: pd.Timestamp) -> dict[str, object]:
    return {
        "symbol": symbol,
        "trade_date": trade_date,
        "market": "TPEX",
        "name": name,
        "open": 50.0,
        "high": 55.0,
        "low": 49.0,
        "close": 54.0,
        "volume_shares": 1_000_000,
        "turnover_twd": 60_000_000.0,
        "daily_return": 0.08,
        "source": "unit",
    }


def write_oos_trades(path: Path) -> None:
    rows = [
        oos_row("7001", "2024-01-02", 0.02, 1_000.0),
        oos_row("7001", "2024-01-03", 0.01, 500.0),
        oos_row("7001", "2024-02-15", -0.002, -100.0),
        oos_row("7002", "2024-01-04", 0.004, 300.0),
        oos_row("7003", "2024-03-01", 0.003, 200.0),
    ]
    excluded = oos_row("8001", "2024-01-05", 0.10, 5_000.0)
    excluded["market"] = "TWSE"
    excluded["market_param"] = "TWSE"
    rows.append(excluded)
    pd.DataFrame(rows).to_csv(path, index=False)


def oos_row(symbol: str, signal_date: str, net_return: float, net_pnl: float) -> dict[str, object]:
    signal_ts = pd.Timestamp(signal_date)
    return {
        "window_id": "window",
        "selected_rank": 1,
        "config_hash": "best_config",
        "trade_id": f"trade_{symbol}_{signal_date}",
        "event_id": f"event_{symbol}_{signal_date}",
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
