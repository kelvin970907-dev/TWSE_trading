from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.backtests.costs import calculate_trade_costs
from src.db import get_connection, init_db, upsert_dataframe
from src.live.evaluate_closed_limit_up_signals import (
    LEDGER_OUTPUT,
    THEORETICAL_REMINDER,
    evaluate_closed_limit_up_signals,
)
from src.live.generate_closed_limit_up_signals import OUTPUT_COLUMNS


def test_zero_order_signal_file_evaluates_cleanly(tmp_path: Path) -> None:
    db_path = tmp_path / "taiwan_trading.duckdb"
    output_dir = tmp_path / "live_signals"
    signals_path = output_dir / "closed_limit_up_signals_2024-01-10.csv"
    output_dir.mkdir(parents=True)
    init_db(db_path)
    pd.DataFrame(columns=OUTPUT_COLUMNS).to_csv(signals_path, index=False)

    evaluations, summary, eval_csv, eval_md, ledger = evaluate_closed_limit_up_signals(
        db_path=db_path,
        signals_csv=signals_path,
        output_dir=output_dir,
    )

    assert evaluations.empty
    assert summary.loc[summary["metric"] == "planned_orders", "value"].iloc[0] == 0
    assert eval_csv.exists()
    assert eval_md.exists()
    assert ledger.exists()
    assert "no planned paper orders" in eval_md.read_text(encoding="utf-8").lower()


def test_one_order_evaluates_with_cost_model_and_updates_ledger(tmp_path: Path) -> None:
    db_path = tmp_path / "taiwan_trading.duckdb"
    output_dir = tmp_path / "live_signals"
    signals_path = output_dir / "closed_limit_up_signals_2024-01-10.csv"
    output_dir.mkdir(parents=True)
    seed_daily_prices(db_path, include_day1=True)
    write_signal_csv(signals_path)

    evaluations, summary, _eval_csv, eval_md, ledger = evaluate_closed_limit_up_signals(
        db_path=db_path,
        signals_csv=signals_path,
        output_dir=output_dir,
    )

    row = evaluations.iloc[0]
    expected = calculate_trade_costs(
        side="long",
        entry_price=50.0,
        exit_price=52.0,
        shares=4000,
        sell_tax_rate=0.003,
        slippage_bps_per_side=5.0,
        minimum_commission_twd=20.0,
        is_day_trade=False,
        normal_sell_tax_rate=0.003,
    )
    assert row["status"] == "evaluated"
    assert row["day1_trade_date"] == "2024-01-11"
    assert row["gross_return"] == pytest.approx(0.04)
    assert row["open_to_high_return"] == pytest.approx(54.0 / 52.0 - 1.0)
    assert row["open_to_low_return"] == pytest.approx(51.0 / 52.0 - 1.0)
    assert row["open_to_close_return"] == pytest.approx(53.0 / 52.0 - 1.0)
    assert row["net_pnl"] == pytest.approx(expected["net_pnl"])
    assert row["net_return"] == pytest.approx(expected["net_return"])
    assert row["sell_tax"] == pytest.approx(expected["sell_tax"])

    summary_values = dict(zip(summary["metric"], summary["value"], strict=True))
    assert summary_values["planned_orders"] == 1
    assert summary_values["evaluated_orders"] == 1
    assert summary_values["net_pnl"] == pytest.approx(expected["net_pnl"])
    assert THEORETICAL_REMINDER in eval_md.read_text(encoding="utf-8")

    ledger_frame = pd.read_csv(ledger)
    assert len(ledger_frame) == 1
    evaluate_closed_limit_up_signals(db_path=db_path, signals_csv=signals_path, output_dir=output_dir)
    deduped = pd.read_csv(output_dir / LEDGER_OUTPUT)
    assert len(deduped) == 1


def test_missing_day1_data_is_reported(tmp_path: Path) -> None:
    db_path = tmp_path / "taiwan_trading.duckdb"
    output_dir = tmp_path / "live_signals"
    signals_path = output_dir / "closed_limit_up_signals_2024-01-10.csv"
    output_dir.mkdir(parents=True)
    seed_daily_prices(db_path, include_day1=False)
    write_signal_csv(signals_path)

    evaluations, summary, _eval_csv, _eval_md, _ledger = evaluate_closed_limit_up_signals(
        db_path=db_path,
        signals_csv=signals_path,
        output_dir=output_dir,
    )

    assert evaluations["status"].tolist() == ["missing_day1_data"]
    summary_values = dict(zip(summary["metric"], summary["value"], strict=True))
    assert summary_values["planned_orders"] == 1
    assert summary_values["evaluated_orders"] == 0
    assert summary_values["missing_day1_data"] == 1


def test_ledger_dedupes_by_profile_symbol_date_market(tmp_path: Path) -> None:
    db_path = tmp_path / "taiwan_trading.duckdb"
    output_dir = tmp_path / "live_signals"
    output_dir.mkdir(parents=True)
    seed_daily_prices(db_path, include_day1=True)
    first = output_dir / "closed_limit_up_signals_profile_a_2024-01-10.csv"
    second = output_dir / "closed_limit_up_signals_profile_b_2024-01-10.csv"
    write_signal_csv(first, profile_name="profile_a")
    write_signal_csv(second, profile_name="profile_b")

    evaluate_closed_limit_up_signals(db_path=db_path, signals_csv=first, output_dir=output_dir)
    evaluate_closed_limit_up_signals(db_path=db_path, signals_csv=second, output_dir=output_dir)
    evaluate_closed_limit_up_signals(db_path=db_path, signals_csv=first, output_dir=output_dir)

    ledger = pd.read_csv(output_dir / LEDGER_OUTPUT)
    assert len(ledger) == 2
    assert set(ledger["profile_name"]) == {"profile_a", "profile_b"}


def seed_daily_prices(db_path: Path, *, include_day1: bool) -> None:
    init_db(db_path)
    rows = [
        daily_row("7001", pd.Timestamp("2024-01-10"), open_price=48.0, high=50.0, low=47.0, close=50.0),
    ]
    if include_day1:
        rows.append(daily_row("7001", pd.Timestamp("2024-01-11"), open_price=52.0, high=54.0, low=51.0, close=53.0))
    with get_connection(db_path) as conn:
        upsert_dataframe(conn, "daily_prices", pd.DataFrame(rows), ["symbol", "trade_date"])


def daily_row(
    symbol: str,
    trade_date: pd.Timestamp,
    *,
    open_price: float,
    high: float,
    low: float,
    close: float,
) -> dict[str, object]:
    return {
        "symbol": symbol,
        "trade_date": trade_date,
        "market": "TPEX",
        "name": f"Stock {symbol}",
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "volume_shares": 1_000_000,
        "turnover_twd": 700_000_000.0,
        "trades": 1_000,
        "prev_close": close / 1.1,
        "daily_return": 0.10,
        "limit_up_price": close,
        "limit_down_price": close * 0.9,
        "touched_limit_up": True,
        "touched_limit_down": False,
        "closed_limit_up": True,
        "closed_limit_down": False,
        "source": "unit_test",
        "created_at": pd.Timestamp("2024-01-10"),
    }


def write_signal_csv(path: Path, *, profile_name: str = "") -> None:
    row = {column: "" for column in OUTPUT_COLUMNS}
    row.update(
        {
            "profile_name": profile_name,
            "signal_date": "2024-01-10",
            "symbol": "7001",
            "name": "Stock 7001",
            "market": "TPEX",
            "sector": "Technology/Electronics",
            "industry": "Electronic Components",
            "close_price": 50.0,
            "limit_up_price": 50.0,
            "turnover_twd": 700_000_000.0,
            "volume_ratio_20d": 2.0,
            "consecutive_limit_up_count": 1,
            "fill_quality_score": 80.0,
            "planned_entry_price": 50.0,
            "planned_exit": "Day1 open",
            "target_notional_twd": 300_000.0,
            "capped_notional_twd": 200_000.0,
            "planned_shares": 4000,
            "planned_buy_notional_twd": 200_000.0,
            "estimated_buy_cost": 179.6,
            "estimated_cash_required": 200_179.6,
            "ranking": 1,
            "notes": "unit test",
            "execution_warning": "paper",
        }
    )
    pd.DataFrame([row], columns=OUTPUT_COLUMNS).to_csv(path, index=False)
