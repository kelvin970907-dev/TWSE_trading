"""Institutional flow and margin/short features for event candidates."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
import pandas as pd


FLOW_FEATURE_COLUMNS = [
    "foreign_net_buy_twd",
    "investment_trust_net_buy_twd",
    "dealer_net_buy_twd",
    "total_institutional_net_buy_twd",
    "foreign_net_buy_to_turnover",
    "investment_trust_net_buy_to_turnover",
    "dealer_net_buy_to_turnover",
    "total_institutional_net_buy_to_turnover",
    "margin_buy_balance",
    "margin_sell_balance",
    "short_sale_balance",
    "day_trade_volume",
    "margin_balance_change_1d",
    "short_balance_change_1d",
    "short_squeeze_proxy",
    "margin_crowding_proxy",
]

FLOW_FILTER_COLUMNS = [
    "foreign_net_buy_to_turnover",
    "investment_trust_net_buy_twd",
    "margin_crowding_proxy",
    "short_balance_change_1d",
]


def add_event_flow_features_from_db(conn, events: pd.DataFrame) -> pd.DataFrame:
    """Load raw flow/margin tables and add engineered features to events."""

    if events.empty:
        return ensure_flow_feature_columns(events)

    keys = normalize_event_keys(events)
    symbols = keys["symbol"].dropna().unique().tolist()
    markets = keys["market"].dropna().unique().tolist()
    start = pd.Timestamp(keys["trade_date"].min()) - pd.Timedelta(days=10)
    end = pd.Timestamp(keys["trade_date"].max())
    institutional = load_institutional_flows(conn, symbols=symbols, markets=markets, start=start, end=end)
    margin = load_margin_short(conn, symbols=symbols, markets=markets, start=start, end=end)
    return add_event_flow_features(events, institutional_flows=institutional, margin_short=margin)


def add_event_flow_features(
    events: pd.DataFrame,
    *,
    institutional_flows: pd.DataFrame | None = None,
    margin_short: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Add normalized institutional and margin/short features to event rows."""

    if events.empty:
        return ensure_flow_feature_columns(events)

    frame = ensure_flow_feature_columns(normalize_event_keys(events))
    base_columns = list(events.columns)
    institutional = normalize_institutional_flows(institutional_flows)
    margin = normalize_margin_short(margin_short)

    if not institutional.empty:
        frame = frame.drop(columns=[column for column in institutional_feature_raw_columns() if column in frame.columns])
        frame = frame.merge(institutional, on=["symbol", "market", "trade_date"], how="left")

    if not margin.empty:
        frame = frame.drop(columns=[column for column in margin_feature_raw_columns() if column in frame.columns])
        frame = frame.merge(margin, on=["symbol", "market", "trade_date"], how="left")

    frame = compute_normalized_flow_features(frame)
    for column in FLOW_FEATURE_COLUMNS:
        if column not in base_columns:
            base_columns.append(column)
    return frame[base_columns]


def compute_normalized_flow_features(frame: pd.DataFrame) -> pd.DataFrame:
    output = frame.copy()
    turnover = pd.to_numeric(output.get("day0_turnover_twd"), errors="coerce")
    volume = pd.to_numeric(output.get("day0_volume_shares"), errors="coerce")
    for column in [
        "foreign_net_buy_twd",
        "investment_trust_net_buy_twd",
        "dealer_net_buy_twd",
        "total_institutional_net_buy_twd",
    ]:
        if column not in output.columns:
            output[column] = np.nan
        output[column] = pd.to_numeric(output[column], errors="coerce")

    if "total_institutional_net_buy_twd" in output.columns:
        missing_total = output["total_institutional_net_buy_twd"].isna()
        output.loc[missing_total, "total_institutional_net_buy_twd"] = output.loc[
            missing_total,
            ["foreign_net_buy_twd", "investment_trust_net_buy_twd", "dealer_net_buy_twd"],
        ].sum(axis=1, min_count=1)

    ratio_map = {
        "foreign_net_buy_twd": "foreign_net_buy_to_turnover",
        "investment_trust_net_buy_twd": "investment_trust_net_buy_to_turnover",
        "dealer_net_buy_twd": "dealer_net_buy_to_turnover",
        "total_institutional_net_buy_twd": "total_institutional_net_buy_to_turnover",
    }
    for source_column, target_column in ratio_map.items():
        output[target_column] = np.where(turnover > 0, output[source_column] / turnover, np.nan)

    for column in [
        "margin_buy_balance",
        "margin_sell_balance",
        "short_sale_balance",
        "day_trade_volume",
        "margin_balance_change_1d",
        "short_balance_change_1d",
    ]:
        if column not in output.columns:
            output[column] = np.nan
        output[column] = pd.to_numeric(output[column], errors="coerce")

    output["short_squeeze_proxy"] = np.where(volume > 0, output["short_balance_change_1d"] / volume, np.nan)
    output["margin_crowding_proxy"] = np.where(volume > 0, output["margin_buy_balance"] / volume, np.nan)
    return ensure_flow_feature_columns(output)


def apply_flow_feature_filters(
    events: pd.DataFrame,
    *,
    require_foreign_not_selling_heavily: bool = False,
    require_investment_trust_buying: bool = False,
    avoid_margin_overcrowded: bool = False,
    prefer_short_balance_rising_before_limit_up: bool = False,
    foreign_heavy_sell_threshold: float = -0.05,
    margin_crowding_threshold: float = 5.0,
) -> pd.DataFrame:
    """Filter event rows using engineered flow and margin features."""

    frame = ensure_flow_feature_columns(events)
    mask = pd.Series(True, index=frame.index)
    if require_foreign_not_selling_heavily:
        mask &= pd.to_numeric(frame["foreign_net_buy_to_turnover"], errors="coerce") >= foreign_heavy_sell_threshold
    if require_investment_trust_buying:
        mask &= pd.to_numeric(frame["investment_trust_net_buy_twd"], errors="coerce") > 0
    if avoid_margin_overcrowded:
        mask &= pd.to_numeric(frame["margin_crowding_proxy"], errors="coerce") <= margin_crowding_threshold
    if prefer_short_balance_rising_before_limit_up:
        mask &= pd.to_numeric(frame["short_balance_change_1d"], errors="coerce") > 0
    return frame[mask].reset_index(drop=True)


def load_institutional_flows(
    conn,
    *,
    symbols: Sequence[Any],
    markets: Sequence[Any],
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> pd.DataFrame:
    if not symbols or not markets:
        return empty_institutional_flows()
    symbol_placeholders = ", ".join(["?"] * len(symbols))
    market_placeholders = ", ".join(["?"] * len(markets))
    return conn.execute(
        f"""
        SELECT
            symbol,
            trade_date,
            market,
            foreign_net_buy_twd,
            investment_trust_net_buy_twd,
            dealer_net_buy_twd,
            total_institutional_net_buy_twd
        FROM institutional_flows
        WHERE symbol IN ({symbol_placeholders})
          AND market IN ({market_placeholders})
          AND trade_date >= ?
          AND trade_date <= ?
        ORDER BY market, symbol, trade_date
        """,
        [*symbols, *markets, start.date(), end.date()],
    ).fetch_df()


def load_margin_short(
    conn,
    *,
    symbols: Sequence[Any],
    markets: Sequence[Any],
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> pd.DataFrame:
    if not symbols or not markets:
        return empty_margin_short()
    symbol_placeholders = ", ".join(["?"] * len(symbols))
    market_placeholders = ", ".join(["?"] * len(markets))
    return conn.execute(
        f"""
        SELECT
            symbol,
            trade_date,
            market,
            margin_buy_balance,
            margin_sell_balance,
            short_sale_balance,
            day_trade_volume
        FROM margin_short
        WHERE symbol IN ({symbol_placeholders})
          AND market IN ({market_placeholders})
          AND trade_date >= ?
          AND trade_date <= ?
        ORDER BY market, symbol, trade_date
        """,
        [*symbols, *markets, start.date(), end.date()],
    ).fetch_df()


def normalize_event_keys(events: pd.DataFrame) -> pd.DataFrame:
    frame = events.copy()
    frame["symbol"] = frame["symbol"].astype("string").str.strip()
    frame["market"] = frame["market"].astype("string").str.upper().str.strip()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.normalize()
    return frame


def normalize_institutional_flows(frame: pd.DataFrame | None) -> pd.DataFrame:
    if frame is None or frame.empty:
        return empty_institutional_flows()
    output = frame.copy()
    output["symbol"] = output["symbol"].astype("string").str.strip()
    output["market"] = output["market"].astype("string").str.upper().str.strip()
    output["trade_date"] = pd.to_datetime(output["trade_date"]).dt.normalize()
    for column in [
        "foreign_net_buy_twd",
        "investment_trust_net_buy_twd",
        "dealer_net_buy_twd",
        "total_institutional_net_buy_twd",
    ]:
        if column not in output.columns:
            output[column] = np.nan
        output[column] = pd.to_numeric(output[column], errors="coerce")
    output["total_institutional_net_buy_twd"] = output["total_institutional_net_buy_twd"].fillna(
        output[["foreign_net_buy_twd", "investment_trust_net_buy_twd", "dealer_net_buy_twd"]].sum(axis=1, min_count=1)
    )
    return output[["symbol", "market", "trade_date", *institutional_feature_raw_columns()]]


def normalize_margin_short(frame: pd.DataFrame | None) -> pd.DataFrame:
    if frame is None or frame.empty:
        return empty_margin_short()
    output = frame.copy()
    output["symbol"] = output["symbol"].astype("string").str.strip()
    output["market"] = output["market"].astype("string").str.upper().str.strip()
    output["trade_date"] = pd.to_datetime(output["trade_date"]).dt.normalize()
    for column in ["margin_buy_balance", "margin_sell_balance", "short_sale_balance", "day_trade_volume"]:
        if column not in output.columns:
            output[column] = np.nan
        output[column] = pd.to_numeric(output[column], errors="coerce")
    output = output.sort_values(["market", "symbol", "trade_date"]).reset_index(drop=True)
    output["margin_balance_change_1d"] = output.groupby(["market", "symbol"])["margin_buy_balance"].diff()
    output["short_balance_change_1d"] = output.groupby(["market", "symbol"])["short_sale_balance"].diff()
    return output[["symbol", "market", "trade_date", *margin_feature_raw_columns()]]


def ensure_flow_feature_columns(frame: pd.DataFrame) -> pd.DataFrame:
    output = frame.copy()
    for column in FLOW_FEATURE_COLUMNS:
        if column not in output.columns:
            output[column] = np.nan
    return output


def institutional_feature_raw_columns() -> list[str]:
    return [
        "foreign_net_buy_twd",
        "investment_trust_net_buy_twd",
        "dealer_net_buy_twd",
        "total_institutional_net_buy_twd",
    ]


def margin_feature_raw_columns() -> list[str]:
    return [
        "margin_buy_balance",
        "margin_sell_balance",
        "short_sale_balance",
        "day_trade_volume",
        "margin_balance_change_1d",
        "short_balance_change_1d",
    ]


def empty_institutional_flows() -> pd.DataFrame:
    return pd.DataFrame(columns=["symbol", "market", "trade_date", *institutional_feature_raw_columns()])


def empty_margin_short() -> pd.DataFrame:
    return pd.DataFrame(columns=["symbol", "market", "trade_date", *margin_feature_raw_columns()])
