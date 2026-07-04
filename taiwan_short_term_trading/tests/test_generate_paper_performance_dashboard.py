from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.db import get_connection, init_db, upsert_dataframe
from src.live.evaluate_closed_limit_up_signals import EVALUATION_COLUMNS
from src.live.generate_paper_performance_dashboard import (
    build_unique_symbol_date,
    generate_paper_performance_dashboard,
    profit_factor,
)
from src.live.manual_fill_log import MANUAL_FILL_COLUMNS


def test_dashboard_works_with_empty_ledger(tmp_path: Path) -> None:
    db_path = tmp_path / "taiwan_trading.duckdb"
    output_dir = tmp_path / "live_signals"
    ledger_path = output_dir / "closed_limit_up_paper_ledger.csv"
    output_dir.mkdir(parents=True)
    init_db(db_path)
    pd.DataFrame(columns=EVALUATION_COLUMNS).to_csv(ledger_path, index=False)

    by_profile, by_date, by_symbol, unique, md_path = generate_paper_performance_dashboard(
        db_path=db_path,
        ledger_path=ledger_path,
        output_dir=output_dir,
    )

    assert by_profile.empty
    assert by_date.empty
    assert by_symbol.empty
    assert unique.empty
    assert md_path.exists()
    assert "Ledger rows: 0" in md_path.read_text(encoding="utf-8")
    assert (output_dir / "paper_performance_by_profile.csv").exists()


def test_profit_factor_calculation() -> None:
    assert profit_factor(200.0, 100.0) == pytest.approx(2.0)
    assert profit_factor(100.0, 0.0) == float("inf")
    assert pd.isna(profit_factor(0.0, 0.0))


def test_dashboard_multiple_profiles_and_manual_fill_join(tmp_path: Path) -> None:
    db_path = tmp_path / "taiwan_trading.duckdb"
    output_dir = tmp_path / "live_signals"
    ledger_path = output_dir / "closed_limit_up_paper_ledger.csv"
    output_dir.mkdir(parents=True)
    init_db(db_path)
    ledger = pd.DataFrame(
        [
            ledger_row("profile_a", "2024-01-10", "7001", net_pnl=200.0, net_return=0.02),
            ledger_row("profile_a", "2024-01-11", "7002", net_pnl=-100.0, net_return=-0.01),
            ledger_row("profile_b", "2024-01-10", "7003", status="missing_day1_data"),
        ],
        columns=EVALUATION_COLUMNS,
    )
    ledger.to_csv(ledger_path, index=False)
    insert_manual_observation(db_path, profile_name="profile_a", signal_date="2024-01-10", symbol="7001")

    by_profile, by_date, by_symbol, _unique, md_path = generate_paper_performance_dashboard(
        db_path=db_path,
        ledger_path=ledger_path,
        output_dir=output_dir,
    )

    profile_a = by_profile[by_profile["profile_name"].eq("profile_a")].iloc[0]
    assert profile_a["evaluated_trades"] == 2
    assert profile_a["cumulative_net_pnl"] == pytest.approx(100.0)
    assert profile_a["profit_factor"] == pytest.approx(2.0)
    assert profile_a["manual_observations_completed"] == 1
    assert profile_a["manual_observations_missing"] == 1
    assert profile_a["execution_confirmed_trades"] == 1

    profile_b = by_profile[by_profile["profile_name"].eq("profile_b")].iloc[0]
    assert profile_b["pending_trades"] == 1
    assert profile_b["evaluated_trades"] == 0

    assert len(by_date) == 3
    assert set(by_symbol["symbol"]) == {"7001", "7002", "7003"}
    assert "profile_a" in md_path.read_text(encoding="utf-8")


def test_unique_symbol_date_view_deduplicates_profiles() -> None:
    ledger = pd.DataFrame(
        [
            ledger_row("profile_a", "2024-01-10", "7001", net_pnl=100.0, net_return=0.01, notional=100_000.0),
            ledger_row("profile_b", "2024-01-10", "7001", net_pnl=150.0, net_return=0.015, notional=200_000.0),
            ledger_row("profile_b", "2024-01-10", "7002", net_pnl=-50.0, net_return=-0.005, notional=100_000.0),
        ],
        columns=EVALUATION_COLUMNS,
    )

    unique = build_unique_symbol_date(ledger)

    assert len(unique) == 2
    row = unique[unique["symbol"].eq("7001")].iloc[0]
    assert row["selected_profile"] == "profile_b"
    assert row["net_pnl"] == pytest.approx(150.0)
    assert row["profile_level_net_pnl"] == pytest.approx(250.0)
    assert row["dedup_pnl_difference"] == pytest.approx(-100.0)


def ledger_row(
    profile_name: str,
    signal_date: str,
    symbol: str,
    *,
    status: str = "evaluated",
    net_pnl: float = 0.0,
    net_return: float = 0.0,
    notional: float = 100_000.0,
) -> dict[str, object]:
    row = {column: "" for column in EVALUATION_COLUMNS}
    row.update(
        {
            "profile_name": profile_name,
            "candidate_hash": "",
            "signal_date": signal_date,
            "evaluation_date": "2024-01-12",
            "symbol": symbol,
            "name": f"Stock {symbol}",
            "market": "TPEX",
            "sector": "Technology/Electronics",
            "industry": "Electronic Components",
            "status": status,
            "planned_entry_price": 50.0,
            "planned_shares": int(notional // 50.0),
            "planned_buy_notional_twd": notional,
            "day1_trade_date": "2024-01-11" if status == "evaluated" else "",
            "day1_open": 51.0 if status == "evaluated" else "",
            "day1_high": 52.0 if status == "evaluated" else "",
            "day1_low": 49.0 if status == "evaluated" else "",
            "day1_close": 50.5 if status == "evaluated" else "",
            "theoretical_exit_price": 51.0 if status == "evaluated" else "",
            "gross_return": 0.02 if status == "evaluated" else "",
            "open_to_high_return": 52.0 / 51.0 - 1.0 if status == "evaluated" else "",
            "open_to_low_return": 49.0 / 51.0 - 1.0 if status == "evaluated" else "",
            "open_to_close_return": 50.5 / 51.0 - 1.0 if status == "evaluated" else "",
            "gross_pnl": net_pnl + 50.0 if status == "evaluated" else "",
            "net_pnl": net_pnl if status == "evaluated" else "",
            "net_return": net_return if status == "evaluated" else "",
            "paper_fill_assumed": True,
            "actual_broker_fill_known": False,
            "actual_broker_filled": "",
            "notes": "unit test",
            "execution_warning": "paper",
        }
    )
    return row


def insert_manual_observation(
    db_path: Path,
    *,
    profile_name: str,
    signal_date: str,
    symbol: str,
) -> None:
    row = {column: None for column in MANUAL_FILL_COLUMNS}
    row.update(
        {
            "observation_id": "obs-1",
            "signal_date": pd.Timestamp(signal_date),
            "profile_name": profile_name,
            "candidate_hash": "",
            "symbol": symbol,
            "market": "TPEX",
            "name": f"Stock {symbol}",
            "intended_entry_price": 50.0,
            "limit_up_price": 50.0,
            "was_order_submitted": True,
            "order_type": "limit",
            "order_quantity_shares": 2000,
            "order_price": 50.0,
            "actual_filled_shares": 2000,
            "actual_avg_fill_price": 50.0,
            "fill_status": "fully_filled",
            "created_at": pd.Timestamp("2024-01-10 13:30:00"),
        }
    )
    with get_connection(db_path) as conn:
        upsert_dataframe(conn, "manual_fill_observations", pd.DataFrame([row]), ["observation_id"])
