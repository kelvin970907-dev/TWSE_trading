from __future__ import annotations

import pandas as pd
import pytest

from src.backtests.portfolio_sim_closed_limit_up import (
    PortfolioConfig,
    add_candidate_scores,
    calculate_board_lot_shares_from_notional,
    simulate_portfolio,
)


def test_capital_limit_selects_only_allowed_positions() -> None:
    candidates = make_candidates(
        [
            candidate("7001", "2024-01-02", fill_quality_score=80),
            candidate("7002", "2024-01-02", fill_quality_score=70),
            candidate("7003", "2024-01-02", fill_quality_score=60),
        ]
    )
    config = PortfolioConfig(max_positions_per_day=2, fixed_notional_twd=100_000)

    trades, _daily, metrics = simulate_portfolio(candidates, config)

    assert trades["symbol"].tolist() == ["7001", "7002"]
    assert metrics["trades"] == 2
    assert metrics["skipped_due_to_max_positions"] == 1


def test_board_lot_rounding_and_skip() -> None:
    assert calculate_board_lot_shares_from_notional(100_000, 30) == 3000
    assert calculate_board_lot_shares_from_notional(100_000, 101) == 0
    candidates = make_candidates([candidate("7001", "2024-01-02", entry_price=150.0, exit_price=153.0)])

    trades, _daily, metrics = simulate_portfolio(
        candidates,
        PortfolioConfig(max_positions_per_day=1, fixed_notional_twd=100_000),
    )

    assert trades.empty
    assert metrics["skipped_due_to_board_lot"] == 1


def test_sector_cap_limits_same_day_sector_exposure() -> None:
    candidates = make_candidates(
        [
            candidate("7001", "2024-01-02", sector="Technology/Electronics", industry="Semiconductor"),
            candidate("7002", "2024-01-02", sector="Technology/Electronics", industry="Components"),
        ]
    )
    config = PortfolioConfig(
        initial_capital_twd=1_000_000,
        max_positions_per_day=2,
        fixed_notional_twd=300_000,
        max_notional_per_symbol_pct=1.0,
        max_notional_per_sector_pct=0.30,
        max_notional_per_industry_pct=1.0,
    )

    trades, _daily, metrics = simulate_portfolio(candidates, config)

    assert trades["symbol"].tolist() == ["7001"]
    assert metrics["skipped_due_to_risk_caps"] == 1
    assert trades["buy_notional"].iloc[0] == pytest.approx(300_000)


def test_monthly_symbol_cap_blocks_repeated_symbol() -> None:
    candidates = make_candidates(
        [
            candidate("7001", "2024-01-02", fill_quality_score=90),
            candidate("7001", "2024-01-10", fill_quality_score=90),
            candidate("7001", "2024-02-01", fill_quality_score=90),
        ]
    )
    config = PortfolioConfig(
        max_positions_per_day=1,
        fixed_notional_twd=100_000,
        max_trades_per_symbol_per_month=1,
    )

    trades, _daily, metrics = simulate_portfolio(candidates, config)

    assert trades["signal_date"].dt.strftime("%Y-%m-%d").tolist() == ["2024-01-02", "2024-02-01"]
    assert metrics["skipped_due_to_monthly_symbol_cap"] == 1


def test_equity_updates_after_overnight_exit() -> None:
    candidates = make_candidates([candidate("7001", "2024-01-02", entry_price=50.0, exit_price=55.0)])
    config = PortfolioConfig(
        initial_capital_twd=1_000_000,
        max_positions_per_day=1,
        fixed_notional_twd=100_000,
        slippage_bps_per_side=0.0,
        minimum_commission_twd=0.0,
    )

    trades, daily, metrics = simulate_portfolio(candidates, config)

    assert len(trades) == 1
    assert trades["shares"].iloc[0] == 2000
    assert daily["end_equity"].iloc[-1] == pytest.approx(1_000_000 + trades["net_pnl"].iloc[0])
    assert metrics["final_equity"] == pytest.approx(1_000_000 + trades["net_pnl"].iloc[0])


def make_candidates(rows: list[dict[str, object]]) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    frame["_diagnostic_row_id"] = range(len(frame))
    frame["market"] = "TPEX"
    frame["name"] = frame["symbol"].map(lambda symbol: f"Stock {symbol}")
    frame["day0_turnover_twd"] = 700_000_000.0
    frame["volume_ratio_20d"] = 2.0
    frame["consecutive_limit_up_count"] = 1
    frame["index_data_available"] = True
    frame["bear_regime"] = False
    frame["bull_regime"] = True
    frame["weak_market_day"] = False
    frame["signal_date"] = pd.to_datetime(frame["signal_date"])
    frame["entry_date"] = frame["signal_date"]
    frame["exit_date"] = frame["signal_date"] + pd.Timedelta(days=1)
    frame["month"] = frame["signal_date"].dt.to_period("M").astype(str)
    frame["trade_id"] = frame["symbol"] + "_" + frame["signal_date"].dt.strftime("%Y%m%d")
    frame["event_id"] = "event_" + frame["trade_id"]
    return add_candidate_scores(frame)


def candidate(
    symbol: str,
    signal_date: str,
    *,
    entry_price: float = 50.0,
    exit_price: float = 52.0,
    fill_quality_score: float = 70.0,
    sector: str = "Technology/Electronics",
    industry: str = "Semiconductor",
) -> dict[str, object]:
    return {
        "symbol": symbol,
        "signal_date": signal_date,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "day0_close": entry_price,
        "fill_quality_score": fill_quality_score,
        "sector": sector,
        "industry": industry,
    }
