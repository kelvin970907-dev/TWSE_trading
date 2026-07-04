"""Market regime and sector filters for event-driven strategies."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
import pandas as pd


MARKET_REGIME_FILTERS = [
    "none",
    "taiex_above_20ma",
    "taiex_above_60ma",
    "taiex_positive_day0",
]

SECTOR_FILTERS = [
    "none",
    "sector_return_positive_day0",
    "sector_momentum_5d_positive",
    "sector_top_quartile_day0",
]


def apply_event_context_filters(
    conn,
    events: pd.DataFrame,
    *,
    market_regime: str = "none",
    sector_filter: str = "none",
    index_symbol: str = "TAIEX",
) -> pd.DataFrame:
    """Apply optional market-regime and sector filters to event candidates."""

    market_regime_value = normalize_market_regime_filter(market_regime)
    sector_filter_value = normalize_sector_filter(sector_filter)
    if events.empty or (market_regime_value == "none" and sector_filter_value == "none"):
        return events.copy()

    frame = normalize_event_frame(events)
    if market_regime_value != "none":
        index_features = load_index_features(
            conn,
            index_symbol=index_symbol,
            dates=frame["trade_date"].dropna().unique().tolist(),
        )
        frame = apply_market_regime_filter(frame, index_features, market_regime=market_regime_value)
    if sector_filter_value != "none":
        sector_features = load_sector_features(
            conn,
            symbols=frame["symbol"].dropna().unique().tolist(),
            markets=frame["market"].dropna().unique().tolist(),
            dates=frame["trade_date"].dropna().unique().tolist(),
        )
        frame = apply_sector_filter(frame, sector_features, sector_filter=sector_filter_value)
    return frame[events.columns].reset_index(drop=True)


def apply_market_regime_filter(
    events: pd.DataFrame,
    index_features: pd.DataFrame,
    *,
    market_regime: str,
) -> pd.DataFrame:
    if index_features.empty:
        return events.iloc[0:0].copy()

    frame = events.merge(index_features, on="trade_date", how="left")
    if market_regime == "taiex_above_20ma":
        mask = frame["close_above_ma20"].fillna(False).astype(bool)
    elif market_regime == "taiex_above_60ma":
        mask = frame["close_above_ma60"].fillna(False).astype(bool)
    elif market_regime == "taiex_positive_day0":
        mask = pd.to_numeric(frame["index_daily_return"], errors="coerce") > 0
    else:
        raise ValueError(f"Unsupported market_regime: {market_regime}")
    return frame[mask].drop(columns=[column for column in index_features.columns if column != "trade_date"])


def apply_sector_filter(events: pd.DataFrame, sector_features: pd.DataFrame, *, sector_filter: str) -> pd.DataFrame:
    if sector_features.empty:
        return events.iloc[0:0].copy()

    frame = events.merge(sector_features, on=["symbol", "market", "trade_date"], how="left")
    if sector_filter == "sector_return_positive_day0":
        mask = pd.to_numeric(frame["equal_weight_return"], errors="coerce") > 0
    elif sector_filter == "sector_momentum_5d_positive":
        mask = pd.to_numeric(frame["sector_momentum_5d"], errors="coerce") > 0
    elif sector_filter == "sector_top_quartile_day0":
        returns = pd.to_numeric(frame["equal_weight_return"], errors="coerce")
        threshold = pd.to_numeric(frame["sector_return_q75"], errors="coerce")
        mask = returns >= threshold
    else:
        raise ValueError(f"Unsupported sector_filter: {sector_filter}")

    drop_columns = [
        column
        for column in sector_features.columns
        if column not in {"symbol", "market", "trade_date"}
    ]
    return frame[mask].drop(columns=drop_columns)


def load_index_features(conn, *, index_symbol: str, dates: Sequence[Any]) -> pd.DataFrame:
    clean_dates = sorted({pd.Timestamp(date).date() for date in dates if pd.notna(date)})
    if not clean_dates:
        return pd.DataFrame(columns=["trade_date", "index_daily_return", "close_above_ma20", "close_above_ma60"])
    placeholders = ", ".join(["?"] * len(clean_dates))
    frame = conn.execute(
        f"""
        SELECT
            trade_date,
            daily_return AS index_daily_return,
            close_above_ma20,
            close_above_ma60
        FROM index_daily_prices
        WHERE UPPER(index_symbol) = ?
          AND trade_date IN ({placeholders})
        """,
        [index_symbol.upper(), *clean_dates],
    ).fetch_df()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.normalize()
    return frame


def load_sector_features(
    conn,
    *,
    symbols: Sequence[Any],
    markets: Sequence[Any],
    dates: Sequence[Any],
) -> pd.DataFrame:
    clean_symbols = sorted({str(symbol).strip() for symbol in symbols if pd.notna(symbol)})
    clean_markets = sorted({str(market).upper().strip() for market in markets if pd.notna(market)})
    clean_dates = sorted({pd.Timestamp(date).date() for date in dates if pd.notna(date)})
    if not clean_symbols or not clean_markets or not clean_dates:
        return empty_sector_feature_frame()

    symbol_placeholders = ", ".join(["?"] * len(clean_symbols))
    market_placeholders = ", ".join(["?"] * len(clean_markets))
    date_placeholders = ", ".join(["?"] * len(clean_dates))
    frame = conn.execute(
        f"""
        WITH thresholds AS (
            SELECT
                trade_date,
                QUANTILE_CONT(equal_weight_return, 0.75) AS sector_return_q75
            FROM sector_daily_features
            WHERE trade_date IN ({date_placeholders})
            GROUP BY trade_date
        )
        SELECT
            m.symbol,
            m.market,
            f.trade_date,
            f.sector,
            f.equal_weight_return,
            f.sector_momentum_5d,
            f.sector_momentum_20d,
            t.sector_return_q75
        FROM stock_sector_map AS m
        JOIN sector_daily_features AS f
          ON m.sector = f.sector
        JOIN thresholds AS t
          ON f.trade_date = t.trade_date
        WHERE m.symbol IN ({symbol_placeholders})
          AND m.market IN ({market_placeholders})
          AND f.trade_date IN ({date_placeholders})
        """,
        [*clean_dates, *clean_symbols, *clean_markets, *clean_dates],
    ).fetch_df()
    if frame.empty:
        return empty_sector_feature_frame()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.normalize()
    frame["market"] = frame["market"].astype("string").str.upper().str.strip()
    frame["symbol"] = frame["symbol"].astype("string").str.strip()
    return frame


def normalize_event_frame(events: pd.DataFrame) -> pd.DataFrame:
    frame = events.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.normalize()
    frame["symbol"] = frame["symbol"].astype("string").str.strip()
    frame["market"] = frame["market"].astype("string").str.upper().str.strip()
    return frame


def normalize_market_regime_filter(value: str) -> str:
    normalized = value.lower().strip()
    if normalized not in MARKET_REGIME_FILTERS:
        raise ValueError(f"Unsupported market_regime: {value}. Valid values are {MARKET_REGIME_FILTERS}")
    return normalized


def normalize_sector_filter(value: str) -> str:
    normalized = value.lower().strip()
    if normalized not in SECTOR_FILTERS:
        raise ValueError(f"Unsupported sector_filter: {value}. Valid values are {SECTOR_FILTERS}")
    return normalized


def empty_sector_feature_frame() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "symbol",
            "market",
            "trade_date",
            "sector",
            "equal_weight_return",
            "sector_momentum_5d",
            "sector_momentum_20d",
            "sector_return_q75",
        ]
    )
