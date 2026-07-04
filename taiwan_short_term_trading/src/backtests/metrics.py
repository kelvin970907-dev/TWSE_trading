"""Backtest performance metrics."""

from __future__ import annotations

import numpy as np
import pandas as pd


def cumulative_return(returns: pd.Series | np.ndarray | list[float]) -> float:
    values = pd.Series(returns, dtype="float64").dropna()
    if values.empty:
        return 0.0
    return float((1.0 + values).prod() - 1.0)


def annualized_return(returns: pd.Series | np.ndarray | list[float], periods_per_year: int = 252) -> float:
    values = pd.Series(returns, dtype="float64").dropna()
    if values.empty:
        return 0.0
    total = cumulative_return(values)
    years = len(values) / periods_per_year
    return float((1.0 + total) ** (1.0 / years) - 1.0)


def sharpe_ratio(
    returns: pd.Series | np.ndarray | list[float],
    periods_per_year: int = 252,
    risk_free_rate: float = 0.0,
) -> float:
    values = pd.Series(returns, dtype="float64").dropna()
    if values.empty:
        return 0.0
    excess = values - risk_free_rate / periods_per_year
    std = excess.std(ddof=1)
    if std == 0 or np.isnan(std):
        return 0.0
    return float(np.sqrt(periods_per_year) * excess.mean() / std)


def max_drawdown(returns: pd.Series | np.ndarray | list[float]) -> float:
    values = pd.Series(returns, dtype="float64").dropna()
    if values.empty:
        return 0.0
    equity = (1.0 + values).cumprod()
    drawdown = equity / equity.cummax() - 1.0
    return float(drawdown.min())


def hit_rate(returns: pd.Series | np.ndarray | list[float]) -> float:
    values = pd.Series(returns, dtype="float64").dropna()
    if values.empty:
        return 0.0
    return float((values > 0).mean())


def profit_factor(returns: pd.Series | np.ndarray | list[float]) -> float:
    values = pd.Series(returns, dtype="float64").dropna()
    gains = values[values > 0].sum()
    losses = abs(values[values < 0].sum())
    if losses == 0:
        return float("inf") if gains > 0 else 0.0
    return float(gains / losses)


def summarize_returns(returns: pd.Series | np.ndarray | list[float]) -> dict[str, float]:
    values = pd.Series(returns, dtype="float64").dropna()
    return {
        "count": float(len(values)),
        "mean_return": float(values.mean()) if not values.empty else 0.0,
        "median_return": float(values.median()) if not values.empty else 0.0,
        "cumulative_return": cumulative_return(values),
        "annualized_return": annualized_return(values) if len(values) > 1 else 0.0,
        "sharpe_ratio": sharpe_ratio(values),
        "max_drawdown": max_drawdown(values),
        "hit_rate": hit_rate(values),
        "profit_factor": profit_factor(values),
    }
