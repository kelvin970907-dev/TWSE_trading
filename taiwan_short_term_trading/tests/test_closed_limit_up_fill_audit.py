from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.backtests.closed_limit_up_fill_audit import (
    FillAuditThresholds,
    add_fill_proxy_features,
    evaluate_fill_assumption,
    run_closed_limit_up_fill_audit,
)
from src.db import get_connection, init_db, upsert_dataframe


def candidate_trade_rows() -> pd.DataFrame:
    base = {
        "trade_id": "trade",
        "event_id": "event",
        "symbol": "4000",
        "market": "TWSE",
        "signal_date": pd.Timestamp("2024-01-08"),
        "entry_date": pd.Timestamp("2024-01-08"),
        "exit_date": pd.Timestamp("2024-01-09"),
        "entry_price": 55.0,
        "exit_price": 56.0,
        "shares": 1000,
        "gross_return": 56.0 / 55.0 - 1.0,
        "net_return": 0.012,
        "gross_pnl": 1000.0,
        "net_pnl": 700.0,
        "day0_open": 50.0,
        "day0_high": 55.0,
        "day0_low": 50.0,
        "day0_close": 55.0,
        "day0_turnover_twd": 600_000_000.0,
        "volume_ratio_20d": 4.0,
        "close_location": 0.90,
        "consecutive_limit_up_count": 1,
        "prior_5d_return": 0.05,
        "prior_20d_return": 0.10,
        "first_limit_up_in_sequence": True,
        "day0_volume_shock_bucket": "2_5x",
        "turnover_bucket": "500m_1b",
    }
    locked = {
        **base,
        "trade_id": "locked",
        "event_id": "locked-event",
        "symbol": "4001",
        "day0_open": 55.0,
        "day0_high": 55.0,
        "day0_low": 55.0,
        "day0_close": 55.0,
        "day0_turnover_twd": 250_000_000.0,
        "close_location": 1.0,
        "net_pnl": 500.0,
    }
    moderate_only = {
        **base,
        "trade_id": "moderate",
        "event_id": "moderate-event",
        "symbol": "4002",
        "day0_high": 55.0,
        "day0_low": 53.9,
        "day0_close": 54.95,
        "day0_turnover_twd": 150_000_000.0,
        "volume_ratio_20d": 2.5,
        "close_location": 0.95,
        "net_pnl": -300.0,
    }
    cheap_crowded = {
        **base,
        "trade_id": "cheap",
        "event_id": "cheap-event",
        "symbol": "4003",
        "day0_high": 8.8,
        "day0_low": 8.0,
        "day0_close": 8.8,
        "day0_turnover_twd": 80_000_000.0,
        "volume_ratio_20d": 12.0,
        "close_location": 1.0,
        "net_pnl": -500.0,
    }
    return pd.DataFrame([locked, base, moderate_only, cheap_crowded])


def synthetic_fill_audit_daily_prices() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    dates = pd.date_range("2024-01-01", periods=9, freq="D")
    closes = [50.0, 51.0, 52.0, 53.0, 54.0, 55.0, 56.0, 61.6, 64.0]
    prev_close: float | None = None
    for trade_date, close in zip(dates, closes):
        closed_limit_up = trade_date == pd.Timestamp("2024-01-08")
        if trade_date == pd.Timestamp("2024-01-09"):
            open_price = 64.68
            high = 65.0
            low = 63.0
            close_price = close
        elif closed_limit_up:
            open_price = 58.0
            high = 61.6
            low = 57.0
            close_price = close
        else:
            open_price = close
            high = close + 0.5
            low = close - 0.5
            close_price = close
        rows.append(
            {
                "symbol": "5001",
                "trade_date": trade_date,
                "market": "TWSE",
                "name": "Stock 5001",
                "open": open_price,
                "high": high,
                "low": low,
                "close": close_price,
                "volume_shares": 10_000 if closed_limit_up else 2_000,
                "turnover_twd": 600_000_000.0 if closed_limit_up else 20_000_000.0,
                "trades": 100,
                "prev_close": prev_close,
                "daily_return": 0.0 if prev_close is None else close_price / prev_close - 1.0,
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


def test_high_low_close_lock_has_poor_fill_quality() -> None:
    audited = add_fill_proxy_features(candidate_trade_rows())
    locked = audited[audited["symbol"] == "4001"].iloc[0]

    assert locked["range_pct"] == 0
    assert locked["fill_quality_score"] < 40
    assert "high==low==close_no_trade_lock_proxy" in locked["base_fill_reason"]

    conservative, reasons = evaluate_fill_assumption(
        audited[audited["symbol"] == "4001"],
        fill_assumption="conservative",
    )
    assert not bool(conservative.iloc[0])
    assert reasons.iloc[0] == "high==low==close_unfillable_under_conservative"


def test_strong_turnover_and_range_scores_high() -> None:
    audited = add_fill_proxy_features(candidate_trade_rows())
    strong = audited[audited["symbol"] == "4000"].iloc[0]

    assert strong["range_pct"] >= 0.03
    assert strong["fill_quality_score"] >= 80

    moderate, _ = evaluate_fill_assumption(audited[audited["symbol"] == "4000"], fill_assumption="moderate")
    conservative, _ = evaluate_fill_assumption(audited[audited["symbol"] == "4000"], fill_assumption="conservative")
    assert bool(moderate.iloc[0])
    assert bool(conservative.iloc[0])


def test_fill_assumptions_have_expected_strictness() -> None:
    audited = add_fill_proxy_features(candidate_trade_rows())

    optimistic, _ = evaluate_fill_assumption(audited, fill_assumption="optimistic")
    moderate, _ = evaluate_fill_assumption(audited, fill_assumption="moderate")
    conservative, _ = evaluate_fill_assumption(audited, fill_assumption="conservative")

    assert int(optimistic.sum()) == len(audited)
    assert int(moderate.sum()) == 2
    assert int(conservative.sum()) == 1
    assert int(conservative.sum()) < int(moderate.sum())


def test_run_closed_limit_up_fill_audit_writes_reports(tmp_path: Path) -> None:
    db_path = tmp_path / "taiwan_trading.duckdb"
    report_dir = tmp_path / "reports"
    init_db(db_path)
    with get_connection(db_path) as conn:
        upsert_dataframe(conn, "daily_prices", synthetic_fill_audit_daily_prices(), ["symbol", "trade_date"])

    audit_events, summary, comparison = run_closed_limit_up_fill_audit(
        db_path=db_path,
        start="2024-01-08",
        end="2024-01-08",
        markets=["TWSE"],
        fixed_notional_twd=100_000,
        min_turnover_twd=100_000_000,
        min_volume_ratio_20d=2.0,
        only_first_limit_up=True,
        output_dir=report_dir,
        rebuild_events=True,
        slippage_bps_per_side=0.0,
        minimum_commission_twd=0.0,
        fill_thresholds=FillAuditThresholds(),
    )

    assert len(audit_events) == 3
    assert not summary.empty
    assert set(comparison["fill_assumption"]) == {"optimistic", "moderate", "conservative"}
    assert (report_dir / "closed_limit_up_fill_audit_events.csv").exists()
    assert (report_dir / "closed_limit_up_fill_audit_summary.csv").exists()
    assert (report_dir / "closed_limit_up_fill_assumption_comparison.csv").exists()
