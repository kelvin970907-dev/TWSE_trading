from __future__ import annotations

import pytest

from src.backtests.costs import (
    TaiwanCostModel,
    brokerage_fee,
    calculate_trade_costs,
    estimate_net_return,
    round_trip_cash_result,
)


def test_calculate_trade_costs_long_day_trade_known_values() -> None:
    result = calculate_trade_costs(
        side="long",
        entry_price=100.0,
        exit_price=110.0,
        shares=1000,
        commission_rate=0.001425,
        commission_discount=0.28,
        sell_tax_rate=0.0015,
        slippage_bps_per_side=5,
        minimum_commission_twd=20,
        is_day_trade=True,
    )

    assert result["buy_notional"] == 100_000.0
    assert result["sell_notional"] == 110_000.0
    assert result["buy_commission"] == pytest.approx(39.9)
    assert result["sell_commission"] == pytest.approx(43.89)
    assert result["sell_tax"] == pytest.approx(165.0)
    assert result["slippage_cost"] == pytest.approx(105.0)
    assert result["total_cost"] == pytest.approx(353.79)
    assert result["gross_pnl"] == pytest.approx(10_000.0)
    assert result["net_pnl"] == pytest.approx(9_646.21)
    assert result["gross_return"] == pytest.approx(0.10)
    assert result["net_return"] == pytest.approx(0.0964621)


def test_calculate_trade_costs_uses_normal_tax_when_not_day_trade() -> None:
    result = calculate_trade_costs(
        side="long",
        entry_price=100.0,
        exit_price=110.0,
        shares=1000,
        commission_discount=0.28,
        sell_tax_rate=0.0015,
        normal_sell_tax_rate=0.003,
        slippage_bps_per_side=0,
        minimum_commission_twd=0,
        is_day_trade=False,
    )

    assert result["sell_tax"] == pytest.approx(330.0)


def test_minimum_commission_applies_per_side() -> None:
    result = calculate_trade_costs(
        side="long",
        entry_price=10.0,
        exit_price=10.5,
        shares=100,
        commission_discount=0.28,
        slippage_bps_per_side=0,
        minimum_commission_twd=20,
    )

    assert result["buy_commission"] == 20.0
    assert result["sell_commission"] == 20.0


def test_price_based_slippage_replaces_bps_slippage() -> None:
    result = calculate_trade_costs(
        side="long",
        entry_price=100.0,
        exit_price=101.0,
        shares=1000,
        slippage_bps_per_side=50,
        price_slippage_per_share=0.10,
        minimum_commission_twd=0,
    )

    assert result["slippage_cost"] == pytest.approx(200.0)


def test_short_trade_uses_sell_entry_and_borrow_fee_placeholder() -> None:
    result = calculate_trade_costs(
        side="short",
        entry_price=100.0,
        exit_price=90.0,
        shares=1000,
        commission_rate=0.001425,
        commission_discount=0.28,
        sell_tax_rate=0.0015,
        slippage_bps_per_side=0,
        minimum_commission_twd=0,
        borrow_fee_rate=0.001,
    )

    assert result["sell_notional"] == 100_000.0
    assert result["buy_notional"] == 90_000.0
    assert result["gross_pnl"] == pytest.approx(10_000.0)
    assert result["sell_tax"] == pytest.approx(150.0)
    assert result["borrow_fee"] == pytest.approx(100.0)
    assert result["net_pnl"] < result["gross_pnl"]


def test_estimate_net_return_delegates_to_discounted_cost_model() -> None:
    model = TaiwanCostModel(
        commission_rate=0.001,
        commission_discount=1.0,
        day_trade_sell_tax_rate=0.003,
        slippage_bps_per_side=5,
        minimum_commission_twd=0,
    )
    gross = 0.02
    net = estimate_net_return(gross, model=model)

    assert net < gross
    expected = calculate_trade_costs(
        side="long",
        entry_price=1.0,
        exit_price=1.02,
        shares=1,
        commission_rate=0.001,
        commission_discount=1.0,
        sell_tax_rate=0.003,
        slippage_bps_per_side=5,
        minimum_commission_twd=0,
    )["net_return"]
    assert net == pytest.approx(expected)


def test_round_trip_cash_result_requires_positive_shares() -> None:
    with pytest.raises(ValueError):
        round_trip_cash_result(entry_price=100, exit_price=101, shares=0)


def test_brokerage_fee_uses_discount_and_minimum() -> None:
    model = TaiwanCostModel(
        commission_rate=0.001425,
        commission_discount=0.28,
        minimum_commission_twd=20,
    )

    assert brokerage_fee(100_000, model) == pytest.approx(39.9)
    assert brokerage_fee(1_000, model) == pytest.approx(20.0)
