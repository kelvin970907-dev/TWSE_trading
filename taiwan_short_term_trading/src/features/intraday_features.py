"""Intraday feature engineering."""

from __future__ import annotations

import pandas as pd


REQUIRED_COLUMNS = {"ts", "stock_id", "market", "price", "volume"}


def add_intraday_features(intraday_prices: pd.DataFrame) -> pd.DataFrame:
    """Add VWAP and first/last trade features to intraday data."""

    missing = sorted(REQUIRED_COLUMNS - set(intraday_prices.columns))
    if missing:
        raise ValueError(f"intraday_prices is missing required columns: {missing}")

    frame = intraday_prices.copy()
    frame["ts"] = pd.to_datetime(frame["ts"])
    if "trade_date" not in frame.columns:
        frame["trade_date"] = frame["ts"].dt.normalize()
    else:
        frame["trade_date"] = pd.to_datetime(frame["trade_date"])

    frame = frame.sort_values(["market", "stock_id", "trade_date", "ts"]).reset_index(drop=True)
    keys = ["market", "stock_id", "trade_date"]
    frame["notional"] = frame["price"] * frame["volume"]
    frame["cum_volume"] = frame.groupby(keys)["volume"].cumsum()
    frame["cum_notional"] = frame.groupby(keys)["notional"].cumsum()
    frame["vwap"] = frame["cum_notional"] / frame["cum_volume"]
    frame["first_price"] = frame.groupby(keys)["price"].transform("first")
    frame["last_price"] = frame.groupby(keys)["price"].transform("last")
    frame["return_from_open_trade"] = frame["price"] / frame["first_price"] - 1.0
    return frame


def summarize_intraday_session(intraday_prices: pd.DataFrame) -> pd.DataFrame:
    """Collapse intraday records into one row per stock/session."""

    features = add_intraday_features(intraday_prices)
    keys = ["market", "stock_id", "trade_date"]
    summary = features.groupby(keys, as_index=False).agg(
        first_price=("first_price", "first"),
        last_price=("last_price", "last"),
        high_price=("price", "max"),
        low_price=("price", "min"),
        volume=("volume", "sum"),
        vwap=("vwap", "last"),
    )
    summary["open_to_last_return"] = summary["last_price"] / summary["first_price"] - 1.0
    summary["open_to_high_return"] = summary["high_price"] / summary["first_price"] - 1.0
    summary["open_to_low_return"] = summary["low_price"] / summary["first_price"] - 1.0
    return summary
