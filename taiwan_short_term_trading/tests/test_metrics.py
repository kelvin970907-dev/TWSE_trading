from __future__ import annotations

import pytest

from src.backtests.metrics import cumulative_return, hit_rate, max_drawdown, profit_factor


def test_core_metrics() -> None:
    returns = [0.10, -0.05, 0.02]

    assert cumulative_return(returns) == pytest.approx((1.10 * 0.95 * 1.02) - 1)
    assert hit_rate(returns) == pytest.approx(2 / 3)
    assert max_drawdown(returns) < 0
    assert profit_factor(returns) == pytest.approx(0.12 / 0.05)
