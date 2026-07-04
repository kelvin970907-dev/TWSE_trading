from __future__ import annotations

import pandas as pd

from src.features.limit_features import add_limit_features, round_to_tick, tick_size


def test_tick_size_and_rounding() -> None:
    assert tick_size(4.99) == 0.01
    assert tick_size(8.0) == 0.05
    assert tick_size(35.0) == 0.10
    assert tick_size(75.0) == 0.50
    assert round_to_tick(110.2, side="floor") == 110.0


def test_plus_8_to_9_not_limit_flag() -> None:
    daily = pd.DataFrame(
        [
            {
                "trade_date": "2024-01-02",
                "stock_id": "1234",
                "market": "TWSE",
                "open": 100,
                "high": 100,
                "low": 100,
                "close": 100,
                "volume": 1000,
            },
            {
                "trade_date": "2024-01-03",
                "stock_id": "1234",
                "market": "TWSE",
                "open": 103,
                "high": 109,
                "low": 102,
                "close": 108.5,
                "volume": 1000,
            },
            {
                "trade_date": "2024-01-04",
                "stock_id": "1234",
                "market": "TWSE",
                "open": 109,
                "high": 119,
                "low": 108,
                "close": 119,
                "volume": 1000,
            },
        ]
    )

    features = add_limit_features(daily)

    assert bool(features.loc[1, "is_plus_8_to_9_not_limit"]) is True
    assert bool(features.loc[1, "high_touched_limit_up"]) is False
    assert bool(features.loc[2, "high_touched_limit_up"]) is True
