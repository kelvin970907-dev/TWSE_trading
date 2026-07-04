"""Taiwan trading-cost model."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field


class TaiwanCostModel(BaseModel):
    """Configurable Taiwan stock trading cost assumptions.

    Commission rates vary by broker and account. The default uses Taiwan's
    standard listed-stock commission rate with a configurable discount, not a
    claim about any one broker's actual pricing.
    """

    commission_rate: float = Field(default=0.001425, ge=0)
    commission_discount: float = Field(default=0.28, ge=0)
    normal_sell_tax_rate: float = Field(default=0.003, ge=0)
    day_trade_sell_tax_rate: float = Field(default=0.0015, ge=0)
    slippage_bps_per_side: float = Field(default=5.0, ge=0)
    minimum_commission_twd: float = Field(default=20.0, ge=0)
    borrow_fee_rate: float = Field(default=0.0, ge=0)

    @property
    def effective_commission_rate(self) -> float:
        return self.commission_rate * self.commission_discount

    @property
    def transaction_tax_rate(self) -> float:
        return self.day_trade_sell_tax_rate

    @property
    def brokerage_rate(self) -> float:
        return self.commission_rate

    @property
    def min_brokerage_fee(self) -> float:
        return self.minimum_commission_twd

    @property
    def slippage_rate(self) -> float:
        return self.slippage_bps_per_side / 10_000.0


@dataclass(frozen=True)
class CashTradeResult:
    entry_notional: float
    exit_notional: float
    entry_cost: float
    exit_cost: float
    gross_pnl: float
    net_pnl: float
    gross_return: float
    net_return: float


def calculate_trade_costs(
    side: str,
    entry_price: float,
    exit_price: float,
    shares: int,
    commission_rate: float = 0.001425,
    commission_discount: float = 0.28,
    sell_tax_rate: float = 0.0015,
    slippage_bps_per_side: float = 5,
    minimum_commission_twd: float = 20,
    is_day_trade: bool = True,
    price_slippage_per_share: float | None = None,
    normal_sell_tax_rate: float = 0.003,
    borrow_fee_rate: float = 0.0,
) -> dict[str, float]:
    """Calculate Taiwan stock round-trip trading costs.

    Args:
        side: `long`/`buy` for buy then sell, or `short`/`sell` for short sell
            then buy to cover.
        entry_price: Executed entry price before modeling slippage.
        exit_price: Executed exit price before modeling slippage.
        shares: Number of shares.
        commission_rate: Standard commission rate, before broker discount.
        commission_discount: Multiplier applied to the standard commission.
        sell_tax_rate: Sale tax rate used for qualified stock day trades by
            default. Taiwan's normal stock sale tax default is available via
            `normal_sell_tax_rate`.
        slippage_bps_per_side: Percent slippage per side in basis points.
        minimum_commission_twd: Minimum commission per order.
        is_day_trade: If False, use `normal_sell_tax_rate` instead of
            `sell_tax_rate`.
        price_slippage_per_share: Optional absolute TWD slippage per share per
            side. When supplied, it replaces bps-based slippage.
        normal_sell_tax_rate: Normal Taiwan stock sale tax rate.
        borrow_fee_rate: Placeholder percent fee applied to short entry notional.

    Returns:
        Cost, PnL, and return fields as plain floats.
    """

    normalized_side = _normalize_side(side)
    _validate_positive_trade_inputs(
        entry_price=entry_price,
        exit_price=exit_price,
        shares=shares,
        commission_rate=commission_rate,
        commission_discount=commission_discount,
        sell_tax_rate=sell_tax_rate,
        slippage_bps_per_side=slippage_bps_per_side,
        minimum_commission_twd=minimum_commission_twd,
        normal_sell_tax_rate=normal_sell_tax_rate,
        borrow_fee_rate=borrow_fee_rate,
        price_slippage_per_share=price_slippage_per_share,
    )

    if normalized_side == "long":
        buy_price = entry_price
        sell_price = exit_price
        entry_notional = buy_price * shares
        exit_notional = sell_price * shares
        gross_pnl = exit_notional - entry_notional
        return_base = entry_notional
        borrow_fee = 0.0
    else:
        sell_price = entry_price
        buy_price = exit_price
        entry_notional = sell_price * shares
        exit_notional = buy_price * shares
        gross_pnl = entry_notional - exit_notional
        return_base = entry_notional
        borrow_fee = entry_notional * borrow_fee_rate

    buy_notional = buy_price * shares
    sell_notional = sell_price * shares
    effective_commission_rate = commission_rate * commission_discount
    buy_commission = max(buy_notional * effective_commission_rate, minimum_commission_twd)
    sell_commission = max(sell_notional * effective_commission_rate, minimum_commission_twd)
    effective_sell_tax_rate = sell_tax_rate if is_day_trade else normal_sell_tax_rate
    sell_tax = sell_notional * effective_sell_tax_rate
    slippage_cost = _slippage_cost(
        entry_price=entry_price,
        exit_price=exit_price,
        shares=shares,
        slippage_bps_per_side=slippage_bps_per_side,
        price_slippage_per_share=price_slippage_per_share,
    )
    total_cost = buy_commission + sell_commission + sell_tax + slippage_cost + borrow_fee
    net_pnl = gross_pnl - total_cost
    gross_return = gross_pnl / return_base
    net_return = net_pnl / return_base

    return {
        "buy_notional": float(buy_notional),
        "sell_notional": float(sell_notional),
        "buy_commission": float(buy_commission),
        "sell_commission": float(sell_commission),
        "sell_tax": float(sell_tax),
        "slippage_cost": float(slippage_cost),
        "borrow_fee": float(borrow_fee),
        "total_cost": float(total_cost),
        "gross_pnl": float(gross_pnl),
        "net_pnl": float(net_pnl),
        "gross_return": float(gross_return),
        "net_return": float(net_return),
    }


def estimate_net_return(gross_return: float, model: TaiwanCostModel | None = None) -> float:
    """Approximate net return for a one-unit long round trip."""

    cost_model = model or TaiwanCostModel()
    result = calculate_trade_costs(
        side="long",
        entry_price=1.0,
        exit_price=1.0 + gross_return,
        shares=1,
        commission_rate=cost_model.commission_rate,
        commission_discount=cost_model.commission_discount,
        sell_tax_rate=cost_model.day_trade_sell_tax_rate,
        slippage_bps_per_side=cost_model.slippage_bps_per_side,
        minimum_commission_twd=0.0,
        is_day_trade=True,
        borrow_fee_rate=cost_model.borrow_fee_rate,
    )
    return result["net_return"]


def brokerage_fee(notional: float, model: TaiwanCostModel | None = None) -> float:
    """Commission fee with discount and per-order minimum."""

    if notional < 0:
        raise ValueError("notional must be non-negative")
    cost_model = model or TaiwanCostModel()
    return max(
        notional * cost_model.effective_commission_rate,
        cost_model.minimum_commission_twd,
    )


def round_trip_cash_result(
    entry_price: float,
    exit_price: float,
    shares: int,
    model: TaiwanCostModel | None = None,
    side: str = "long",
    is_day_trade: bool = True,
) -> CashTradeResult:
    """Return cash PnL after Taiwan commissions, tax, slippage, and borrow fee."""

    cost_model = model or TaiwanCostModel()
    result = calculate_trade_costs(
        side=side,
        entry_price=entry_price,
        exit_price=exit_price,
        shares=shares,
        commission_rate=cost_model.commission_rate,
        commission_discount=cost_model.commission_discount,
        sell_tax_rate=cost_model.day_trade_sell_tax_rate,
        slippage_bps_per_side=cost_model.slippage_bps_per_side,
        minimum_commission_twd=cost_model.minimum_commission_twd,
        is_day_trade=is_day_trade,
        normal_sell_tax_rate=cost_model.normal_sell_tax_rate,
        borrow_fee_rate=cost_model.borrow_fee_rate,
    )
    entry_notional = entry_price * shares
    exit_notional = exit_price * shares
    if _normalize_side(side) == "long":
        entry_cost = result["buy_commission"] + result["slippage_cost"] / 2.0
        exit_cost = result["sell_commission"] + result["sell_tax"] + result["slippage_cost"] / 2.0
    else:
        entry_cost = (
            result["sell_commission"]
            + result["sell_tax"]
            + result["slippage_cost"] / 2.0
            + result["borrow_fee"]
        )
        exit_cost = result["buy_commission"] + result["slippage_cost"] / 2.0

    return CashTradeResult(
        entry_notional=entry_notional,
        exit_notional=exit_notional,
        entry_cost=entry_cost,
        exit_cost=exit_cost,
        gross_pnl=result["gross_pnl"],
        net_pnl=result["net_pnl"],
        gross_return=result["gross_return"],
        net_return=result["net_return"],
    )


def _normalize_side(side: str) -> str:
    value = str(side).lower().strip()
    if value in {"long", "buy"}:
        return "long"
    if value in {"short", "sell"}:
        return "short"
    raise ValueError("side must be one of: long, buy, short, sell")


def _slippage_cost(
    *,
    entry_price: float,
    exit_price: float,
    shares: int,
    slippage_bps_per_side: float,
    price_slippage_per_share: float | None,
) -> float:
    if price_slippage_per_share is not None:
        return price_slippage_per_share * shares * 2.0
    slippage_rate = slippage_bps_per_side / 10_000.0
    return (entry_price * shares + exit_price * shares) * slippage_rate


def _validate_positive_trade_inputs(
    *,
    entry_price: float,
    exit_price: float,
    shares: int,
    commission_rate: float,
    commission_discount: float,
    sell_tax_rate: float,
    slippage_bps_per_side: float,
    minimum_commission_twd: float,
    normal_sell_tax_rate: float,
    borrow_fee_rate: float,
    price_slippage_per_share: float | None,
) -> None:
    if entry_price <= 0 or exit_price <= 0:
        raise ValueError("entry_price and exit_price must be positive")
    if shares <= 0:
        raise ValueError("shares must be positive")

    non_negative_values = {
        "commission_rate": commission_rate,
        "commission_discount": commission_discount,
        "sell_tax_rate": sell_tax_rate,
        "slippage_bps_per_side": slippage_bps_per_side,
        "minimum_commission_twd": minimum_commission_twd,
        "normal_sell_tax_rate": normal_sell_tax_rate,
        "borrow_fee_rate": borrow_fee_rate,
    }
    for name, value in non_negative_values.items():
        if value < 0:
            raise ValueError(f"{name} must be non-negative")
    if price_slippage_per_share is not None and price_slippage_per_share < 0:
        raise ValueError("price_slippage_per_share must be non-negative")
