"""Taiwan price-limit feature engineering."""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.features.daily_features import add_daily_return_features


def tick_size(price: float | int | None) -> float:
    """Approximate Taiwan equity tick size for a given price."""

    if price is None or pd.isna(price):
        return np.nan
    value = float(price)
    if value < 5:
        return 0.01
    if value < 10:
        return 0.05
    if value < 50:
        return 0.10
    if value < 100:
        return 0.50
    if value < 500:
        return 1.00
    if value < 1000:
        return 5.00
    return 10.00


def round_to_tick(price: float | int | None, side: str = "nearest") -> float:
    """Round a price to the approximate Taiwan tick grid."""

    if price is None or pd.isna(price):
        return np.nan
    size = tick_size(float(price))
    value = float(price)
    if side == "floor":
        return round(np.floor((value + 1e-12) / size) * size, 4)
    if side == "ceil":
        return round(np.ceil((value - 1e-12) / size) * size, 4)
    if side == "nearest":
        return round(round(value / size) * size, 4)
    raise ValueError("side must be one of: floor, ceil, nearest")


def add_limit_features(
    daily_prices: pd.DataFrame,
    limit_pct: float = 0.10,
    near_lower: float = 0.08,
    near_upper: float = 0.09,
    tolerance: float = 1e-9,
) -> pd.DataFrame:
    """Add approximate Taiwan limit-up and near-limit event flags.

    The core research flag is `is_plus_8_to_9_not_limit`: close-to-close return
    between +8% and +9%, while the high did not touch the estimated limit-up
    price.
    """

    if "prev_close" not in daily_prices.columns:
        frame = add_daily_return_features(daily_prices)
    else:
        frame = daily_prices.copy()

    raw_limit_up = frame["prev_close"] * (1.0 + limit_pct)
    frame["limit_up_price"] = raw_limit_up.map(lambda value: round_to_tick(value, side="floor"))

    frame["high_touched_limit_up"] = (
        frame["limit_up_price"].notna()
        & frame["high"].notna()
        & (frame["high"] >= frame["limit_up_price"] - tolerance)
    )
    frame["close_at_limit_up"] = (
        frame["limit_up_price"].notna()
        & frame["close"].notna()
        & (frame["close"] >= frame["limit_up_price"] - tolerance)
    )
    frame["is_plus_8_to_9_not_limit"] = (
        frame["close_to_close_return"].notna()
        & (frame["close_to_close_return"] >= near_lower)
        & (frame["close_to_close_return"] <= near_upper)
        & ~frame["high_touched_limit_up"]
    )

    return frame
