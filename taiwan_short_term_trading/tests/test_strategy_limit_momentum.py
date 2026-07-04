from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.backtests.costs import calculate_trade_costs
from src.backtests.event_study import build_event_candidates
from src.backtests.strategy_limit_momentum import (
    calculate_board_lot_shares,
    run_strategy_limit_momentum,
    simulate_daily_ohlc_exit,
    summarize_strategy_trades,
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


def test_calculate_board_lot_shares_rounds_down_to_1000() -> None:
    assert calculate_board_lot_shares(fixed_notional_twd=100_000, entry_price=30) == 3000
    assert calculate_board_lot_shares(fixed_notional_twd=100_000, entry_price=101) == 0


def test_simulate_daily_ohlc_exit_respects_path_assumptions() -> None:
    base = {
        "entry_price": 100.0,
        "day_high": 103.0,
        "day_low": 98.0,
        "day_close": 100.5,
        "take_profit_pct": 0.02,
        "stop_loss_pct": 0.01,
    }

    optimistic = simulate_daily_ohlc_exit(**base, path_assumption="optimistic")
    pessimistic = simulate_daily_ohlc_exit(**base, path_assumption="pessimistic")
    close_only = simulate_daily_ohlc_exit(**base, path_assumption="close_only")

    assert optimistic["exit_reason"] == "take_profit"
    assert optimistic["exit_price"] == pytest.approx(102.0)
    assert pessimistic["exit_reason"] == "stop_loss"
    assert pessimistic["exit_price"] == pytest.approx(99.0)
    assert close_only["exit_reason"] == "close_exit"
    assert close_only["exit_price"] == pytest.approx(100.5)


def test_run_strategy_limit_momentum_builds_trades_and_reports(tmp_path: Path) -> None:
    db_path = tmp_path / "taiwan_trading.duckdb"
    report_dir = tmp_path / "reports"
    init_db(db_path)
    daily = synthetic_daily_prices()

    with get_connection(db_path) as conn:
        upsert_dataframe(conn, "daily_prices", daily, ["symbol", "trade_date"])

    trades, summary = run_strategy_limit_momentum(
        db_path=db_path,
        start="2024-01-31",
        end="2024-01-31",
        event_types=["near_limit_8_9", "near_limit_9_10", "closed_limit_up"],
        fixed_notional_twd=200_000,
        min_turnover_twd=200_000,
        min_volume_ratio_20d=2.0,
        markets=["TWSE"],
        commission_rate=0.001425,
        commission_discount=0.28,
        sell_tax_rate=0.0015,
        slippage_bps_per_side=5,
        minimum_commission_twd=20,
        take_profit_pct=0.01,
        stop_loss_pct=0.01,
        path_assumption="optimistic",
        output_dir=report_dir,
    )

    assert len(trades) == 3
    assert set(trades["event_type"]) == {"near_limit_8_9", "near_limit_9_10", "closed_limit_up"}
    assert set(trades["shares"]) == {2000}

    near_8_9 = trades[trades["event_type"] == "near_limit_8_9"].iloc[0]
    expected_costs = calculate_trade_costs(
        side="long",
        entry_price=100.0,
        exit_price=101.0,
        shares=2000,
        commission_rate=0.001425,
        commission_discount=0.28,
        sell_tax_rate=0.0015,
        slippage_bps_per_side=5,
        minimum_commission_twd=20,
    )
    assert near_8_9["gross_pnl"] == pytest.approx(expected_costs["gross_pnl"])
    assert near_8_9["net_pnl"] == pytest.approx(expected_costs["net_pnl"])
    assert near_8_9["net_return"] == pytest.approx(expected_costs["net_return"])
    assert near_8_9["exit_reason"] == "take_profit"
    assert near_8_9["path_assumption"] == "optimistic"
    assert near_8_9["take_profit_price"] == pytest.approx(101.0)
    assert near_8_9["stop_loss_price"] == pytest.approx(99.0)

    with get_connection(db_path, read_only=True) as conn:
        stored_count = conn.execute("SELECT COUNT(*) FROM backtest_trades").fetchone()[0]

    assert stored_count == 3
    assert (report_dir / "strategy_limit_momentum_trades.csv").exists()
    assert (report_dir / "strategy_limit_momentum_summary.csv").exists()

    overall_rows = summary[summary["summary_level"] == "overall"]
    assert set(overall_rows["path_assumption"]) == {"optimistic", "pessimistic", "close_only"}
    overall = overall_rows[overall_rows["path_assumption"] == "optimistic"].iloc[0]
    assert overall["number_of_trades"] == 3
    assert overall["total_net_pnl"] == pytest.approx(trades["net_pnl"].sum())
    assert "by_event_type" in set(summary["summary_level"])
    assert "by_year" in set(summary["summary_level"])
    assert "by_market" in set(summary["summary_level"])


def test_strategy_filters_close_location_and_skips_small_board_lots(tmp_path: Path) -> None:
    db_path = tmp_path / "taiwan_trading.duckdb"
    init_db(db_path)
    daily = synthetic_daily_prices()

    with get_connection(db_path) as conn:
        upsert_dataframe(conn, "daily_prices", daily, ["symbol", "trade_date"])
        build_event_candidates(
            db_path=db_path,
            start="2024-01-31",
            end="2024-01-31",
            markets=["TWSE"],
        )

    trades, _ = run_strategy_limit_momentum(
        db_path=db_path,
        start="2024-01-31",
        end="2024-01-31",
        event_types=["near_limit_8_9", "near_limit_9_10", "closed_limit_up"],
        fixed_notional_twd=50_000,
        min_close_location=0.85,
        markets=["TWSE"],
        output_dir=tmp_path / "reports",
        rebuild_events=False,
    )

    assert trades.empty


def test_strategy_applies_market_regime_filter(tmp_path: Path) -> None:
    db_path = tmp_path / "taiwan_trading.duckdb"
    init_db(db_path)
    daily = synthetic_daily_prices()

    with get_connection(db_path) as conn:
        upsert_dataframe(conn, "daily_prices", daily, ["symbol", "trade_date"])
        upsert_dataframe(
            conn,
            "index_daily_prices",
            pd.DataFrame(
                [
                    {
                        "index_symbol": "TAIEX",
                        "trade_date": pd.Timestamp("2024-01-31"),
                        "open": 100.0,
                        "high": 101.0,
                        "low": 99.0,
                        "close": 101.0,
                        "daily_return": 0.01,
                        "ma20": 100.0,
                        "ma60": 105.0,
                        "close_above_ma20": True,
                        "close_above_ma60": False,
                    }
                ]
            ),
            ["index_symbol", "trade_date"],
        )

    passing, _ = run_strategy_limit_momentum(
        db_path=db_path,
        start="2024-01-31",
        end="2024-01-31",
        event_types=["near_limit_8_9"],
        fixed_notional_twd=200_000,
        markets=["TWSE"],
        market_regime="taiex_above_20ma",
        output_dir=tmp_path / "reports_pass",
    )
    failing, _ = run_strategy_limit_momentum(
        db_path=db_path,
        start="2024-01-31",
        end="2024-01-31",
        event_types=["near_limit_8_9"],
        fixed_notional_twd=200_000,
        markets=["TWSE"],
        market_regime="taiex_above_60ma",
        output_dir=tmp_path / "reports_fail",
    )

    assert len(passing) == 1
    assert failing.empty


def test_strategy_applies_institutional_and_margin_filters(tmp_path: Path) -> None:
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
                        "foreign_net_buy_twd": -10_000.0,
                        "investment_trust_net_buy_twd": 20_000.0,
                        "dealer_net_buy_twd": 0.0,
                        "total_institutional_net_buy_twd": 10_000.0,
                        "source": "test",
                    },
                    {
                        "symbol": "1002",
                        "trade_date": pd.Timestamp("2024-01-31"),
                        "market": "TWSE",
                        "foreign_net_buy_twd": -100_000.0,
                        "investment_trust_net_buy_twd": -1_000.0,
                        "dealer_net_buy_twd": 0.0,
                        "total_institutional_net_buy_twd": -101_000.0,
                        "source": "test",
                    },
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
                        "margin_buy_balance": 6_000,
                        "margin_sell_balance": 0,
                        "short_sale_balance": 300,
                        "day_trade_volume": 1_000,
                        "source": "test",
                    },
                    {
                        "symbol": "1001",
                        "trade_date": pd.Timestamp("2024-01-31"),
                        "market": "TWSE",
                        "margin_buy_balance": 8_000,
                        "margin_sell_balance": 0,
                        "short_sale_balance": 500,
                        "day_trade_volume": 1_000,
                        "source": "test",
                    },
                    {
                        "symbol": "1002",
                        "trade_date": pd.Timestamp("2024-01-30"),
                        "market": "TWSE",
                        "margin_buy_balance": 30_000,
                        "margin_sell_balance": 0,
                        "short_sale_balance": 500,
                        "day_trade_volume": 1_000,
                        "source": "test",
                    },
                    {
                        "symbol": "1002",
                        "trade_date": pd.Timestamp("2024-01-31"),
                        "market": "TWSE",
                        "margin_buy_balance": 30_000,
                        "margin_sell_balance": 0,
                        "short_sale_balance": 400,
                        "day_trade_volume": 1_000,
                        "source": "test",
                    },
                ]
            ),
            ["symbol", "trade_date"],
        )

    trades, _ = run_strategy_limit_momentum(
        db_path=db_path,
        start="2024-01-31",
        end="2024-01-31",
        event_types=["near_limit_8_9", "near_limit_9_10"],
        fixed_notional_twd=200_000,
        markets=["TWSE"],
        require_foreign_not_selling_heavily=True,
        require_investment_trust_buying=True,
        avoid_margin_overcrowded=True,
        prefer_short_balance_rising_before_limit_up=True,
        foreign_heavy_sell_threshold=-0.05,
        margin_crowding_threshold=5.0,
        output_dir=tmp_path / "reports",
    )

    assert len(trades) == 1
    assert trades.iloc[0]["symbol"] == "1001"


def test_summarize_strategy_trades_profit_factor_and_drawdown() -> None:
    trades = pd.DataFrame(
        [
            {
                "entry_date": "2024-01-02",
                "event_type": "near_limit_8_9",
                "market": "TWSE",
                "gross_return": 0.02,
                "net_return": 0.01,
                "gross_pnl": 2_000,
                "net_pnl": 1_000,
                "day0_turnover_twd": 100_000_000,
            },
            {
                "entry_date": "2024-01-03",
                "event_type": "near_limit_8_9",
                "market": "TWSE",
                "gross_return": -0.01,
                "net_return": -0.02,
                "gross_pnl": -1_000,
                "net_pnl": -2_000,
                "day0_turnover_twd": 200_000_000,
            },
        ]
    )

    summary = summarize_strategy_trades(trades)
    overall = summary[summary["summary_level"] == "overall"].iloc[0]

    assert overall["number_of_trades"] == 2
    assert overall["path_assumption"] == "unspecified"
    assert overall["win_rate"] == 0.5
    assert overall["profit_factor"] == pytest.approx(0.5)
    assert overall["max_drawdown"] == pytest.approx(-2_000)
    assert overall["average_turnover"] == pytest.approx(150_000_000)
