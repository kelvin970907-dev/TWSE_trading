"""Daily price feature engineering."""

from __future__ import annotations

import pandas as pd


REQUIRED_COLUMNS = {"trade_date", "stock_id", "market", "open", "high", "low", "close"}


def add_daily_return_features(daily_prices: pd.DataFrame) -> pd.DataFrame:
    """Add return and dollar-volume features to daily OHLCV data."""

    missing = sorted(REQUIRED_COLUMNS - set(daily_prices.columns))
    if missing:
        raise ValueError(f"daily_prices is missing required columns: {missing}")

    frame = daily_prices.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"])
    frame = frame.sort_values(["market", "stock_id", "trade_date"]).reset_index(drop=True)

    group_keys = ["market", "stock_id"]
    frame["prev_close"] = frame.groupby(group_keys)["close"].shift(1)
    frame["close_to_close_return"] = frame["close"] / frame["prev_close"] - 1.0
    frame["open_gap_return"] = frame["open"] / frame["prev_close"] - 1.0
    frame["intraday_return"] = frame["close"] / frame["open"] - 1.0
    frame["high_return_from_prev_close"] = frame["high"] / frame["prev_close"] - 1.0
    frame["low_return_from_prev_close"] = frame["low"] / frame["prev_close"] - 1.0

    if "turnover" in frame.columns:
        frame["dollar_volume"] = frame["turnover"]
    elif "volume" in frame.columns:
        frame["dollar_volume"] = frame["volume"] * frame["close"]
    else:
        frame["dollar_volume"] = pd.NA

    return frame
