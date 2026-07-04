"""Daily-data fill-realism audit for closed-limit-up overnight trades.

This module does not claim to know actual closing-auction fills. It applies
conservative daily OHLCV proxies to the existing Day0-close to Day1-open
strategy so the research can separate the paper edge from likely execution
quality.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from config.settings import get_settings
from src.backtests.closed_limit_up_overnight import (
    build_overnight_trades,
    empty_trade_report_frame,
    max_drawdown_from_pnl,
    parse_bool,
)
from src.backtests.closed_limit_up_overnight import apply_strategy_filters as apply_overnight_strategy_filters
from src.backtests.event_study import build_event_candidates, normalize_markets
from src.backtests.limit_up_gap_capture import (
    build_limit_up_gap_frame,
    bucket_turnover,
    bucket_volume_shock,
    load_closed_limit_up_events,
    load_daily_prices_for_gap_capture,
)
from src.db import get_connection, init_db


FILL_ASSUMPTIONS = ("optimistic", "moderate", "conservative")
DEFAULT_FILL_QUALITY_THRESHOLDS = (40.0, 50.0, 60.0, 70.0, 80.0)

AUDIT_EVENT_COLUMNS = [
    "trade_id",
    "event_id",
    "symbol",
    "market",
    "signal_date",
    "entry_date",
    "exit_date",
    "entry_price",
    "exit_price",
    "shares",
    "gross_return",
    "net_return",
    "gross_pnl",
    "net_pnl",
    "day0_open",
    "day0_high",
    "day0_low",
    "day0_close",
    "day0_price",
    "day0_turnover_twd",
    "turnover_twd",
    "volume_ratio_20d",
    "range_pct",
    "close_location",
    "consecutive_limit_up_count",
    "prior_5d_return",
    "prior_20d_return",
    "first_limit_up_in_sequence",
    "day0_volume_shock_bucket",
    "turnover_bucket",
    "range_pct_bucket",
    "fill_quality_score",
    "fill_quality_score_bucket",
    "fill_assumption",
    "fillable_boolean",
    "fill_reason",
]

SUMMARY_COLUMNS = [
    "scenario_id",
    "fill_assumption",
    "min_fill_quality_score",
    "summary_level",
    "market",
    "year",
    "turnover_bucket",
    "day0_volume_shock_bucket",
    "range_pct_bucket",
    "fill_quality_score_bucket",
    "candidate_trades",
    "fillable_trades",
    "skipped_trades",
    "fillable_ratio",
    "raw_win_rate",
    "win_rate",
    "average_gross_return",
    "median_gross_return",
    "average_net_return",
    "median_net_return",
    "profit_factor",
    "total_net_pnl",
    "max_drawdown",
    "average_turnover_twd",
    "average_volume_ratio_20d",
    "average_range_pct",
    "average_fill_quality_score",
]


@dataclass(frozen=True)
class FillAuditThresholds:
    """Daily-data proxy thresholds for fillability assumptions."""

    moderate_min_turnover_twd: float = 100_000_000.0
    moderate_min_volume_ratio_20d: float = 2.0
    moderate_min_range_pct: float = 0.01
    moderate_min_close_location: float = 0.80
    conservative_min_turnover_twd: float = 200_000_000.0
    conservative_min_volume_ratio_20d: float = 3.0
    conservative_min_range_pct: float = 0.03
    conservative_min_close_location: float = 0.85
    narrow_lock_range_pct: float = 0.005
    extremely_high_turnover_twd: float = 1_000_000_000.0


def run_closed_limit_up_fill_audit(
    *,
    db_path: Path | str,
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
    markets: Sequence[str] | None = None,
    fixed_notional_twd: float = 100_000.0,
    min_turnover_twd: float | None = None,
    min_volume_ratio_20d: float | None = None,
    min_close_location: float | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    max_consecutive_limit_ups: int | None = None,
    only_first_limit_up: bool = False,
    exclude_if_prior_5d_return_above: float | None = None,
    exclude_if_prior_20d_return_above: float | None = None,
    commission_rate: float = 0.001425,
    commission_discount: float = 0.28,
    sell_tax_rate: float = 0.003,
    slippage_bps_per_side: float = 5.0,
    minimum_commission_twd: float = 20.0,
    fill_thresholds: FillAuditThresholds | None = None,
    fill_quality_thresholds: Sequence[float] = DEFAULT_FILL_QUALITY_THRESHOLDS,
    output_dir: Path | str | None = None,
    rebuild_events: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Run fill-realism scenarios for the closed-limit-up overnight strategy."""

    thresholds = fill_thresholds or FillAuditThresholds()
    validate_fill_audit_inputs(
        start=start,
        end=end,
        fixed_notional_twd=fixed_notional_twd,
        min_turnover_twd=min_turnover_twd,
        min_volume_ratio_20d=min_volume_ratio_20d,
        min_close_location=min_close_location,
        min_price=min_price,
        max_price=max_price,
        max_consecutive_limit_ups=max_consecutive_limit_ups,
        commission_rate=commission_rate,
        commission_discount=commission_discount,
        sell_tax_rate=sell_tax_rate,
        slippage_bps_per_side=slippage_bps_per_side,
        minimum_commission_twd=minimum_commission_twd,
        thresholds=thresholds,
        fill_quality_thresholds=fill_quality_thresholds,
    )

    init_db(db_path)
    market_values = normalize_markets(markets)
    if rebuild_events:
        build_event_candidates(
            db_path=db_path,
            start=start,
            end=end,
            markets=market_values,
        )

    with get_connection(db_path) as conn:
        events = load_closed_limit_up_events(
            conn,
            start=start,
            end=end,
            markets=market_values,
        )
        daily_prices = load_daily_prices_for_gap_capture(conn, markets=market_values)

    study = build_limit_up_gap_frame(
        events,
        daily_prices,
        normal_sell_tax_rate=sell_tax_rate,
        slippage_bps_per_side=slippage_bps_per_side,
        minimum_commission_twd=minimum_commission_twd,
    )
    filtered = apply_overnight_strategy_filters(
        study,
        min_turnover_twd=min_turnover_twd,
        min_volume_ratio_20d=min_volume_ratio_20d,
        min_close_location=min_close_location,
        min_price=min_price,
        max_price=max_price,
        max_consecutive_limit_ups=max_consecutive_limit_ups,
        only_first_limit_up=only_first_limit_up,
        exclude_if_prior_5d_return_above=exclude_if_prior_5d_return_above,
        exclude_if_prior_20d_return_above=exclude_if_prior_20d_return_above,
    )
    trades = build_overnight_trades(
        filtered,
        fixed_notional_twd=fixed_notional_twd,
        commission_rate=commission_rate,
        commission_discount=commission_discount,
        sell_tax_rate=sell_tax_rate,
        slippage_bps_per_side=slippage_bps_per_side,
        minimum_commission_twd=minimum_commission_twd,
        min_turnover_twd=min_turnover_twd,
        min_volume_ratio_20d=min_volume_ratio_20d,
        min_close_location=min_close_location,
        min_price=min_price,
        max_price=max_price,
        max_consecutive_limit_ups=max_consecutive_limit_ups,
        only_first_limit_up=only_first_limit_up,
        exclude_if_prior_5d_return_above=exclude_if_prior_5d_return_above,
        exclude_if_prior_20d_return_above=exclude_if_prior_20d_return_above,
    )

    candidates = add_fill_proxy_features(trades)
    audit_events = build_fill_assumption_events(candidates, thresholds=thresholds)
    summary = summarize_fill_audit(
        candidates,
        thresholds=thresholds,
        fill_quality_thresholds=fill_quality_thresholds,
    )
    comparison = build_assumption_comparison(summary)

    report_dir = Path(output_dir) if output_dir is not None else default_report_dir()
    report_dir.mkdir(parents=True, exist_ok=True)
    audit_events.to_csv(report_dir / "closed_limit_up_fill_audit_events.csv", index=False)
    summary.to_csv(report_dir / "closed_limit_up_fill_audit_summary.csv", index=False)
    comparison.to_csv(report_dir / "closed_limit_up_fill_assumption_comparison.csv", index=False)
    return audit_events, summary, comparison


def add_fill_proxy_features(trades: pd.DataFrame) -> pd.DataFrame:
    """Add daily-data fill-quality proxy features to candidate trades."""

    if trades.empty:
        return empty_candidate_frame()

    required = {
        "day0_high",
        "day0_low",
        "day0_close",
        "day0_turnover_twd",
        "volume_ratio_20d",
        "close_location",
        "consecutive_limit_up_count",
        "first_limit_up_in_sequence",
    }
    missing = sorted(required - set(trades.columns))
    if missing:
        raise ValueError(f"trades is missing required columns for fill audit: {missing}")

    frame = trades.copy()
    numeric_columns = [
        "day0_high",
        "day0_low",
        "day0_close",
        "day0_turnover_twd",
        "volume_ratio_20d",
        "close_location",
        "consecutive_limit_up_count",
        "prior_5d_return",
        "prior_20d_return",
        "gross_return",
        "net_return",
        "gross_pnl",
        "net_pnl",
    ]
    for column in numeric_columns:
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")

    frame["range_pct"] = np.where(
        frame["day0_low"] > 0,
        frame["day0_high"] / frame["day0_low"] - 1.0,
        np.nan,
    )
    frame["day0_price"] = frame["day0_close"]
    frame["turnover_twd"] = frame["day0_turnover_twd"]
    frame["first_limit_up_in_sequence"] = frame["first_limit_up_in_sequence"].fillna(False).astype(bool)
    frame["is_no_trade_lock"] = (
        np.isclose(frame["day0_high"], frame["day0_low"], equal_nan=False)
        & np.isclose(frame["day0_low"], frame["day0_close"], equal_nan=False)
    )
    frame["high_close_equal"] = np.isclose(frame["day0_high"], frame["day0_close"], equal_nan=False)
    frame["turnover_bucket"] = bucket_turnover(frame["day0_turnover_twd"])
    frame["day0_volume_shock_bucket"] = bucket_volume_shock(frame["volume_ratio_20d"])
    frame["range_pct_bucket"] = bucket_range_pct(frame["range_pct"])

    scored = frame.apply(score_fill_quality_row, axis=1, result_type="expand")
    frame["fill_quality_score"] = scored["fill_quality_score"].astype(float)
    frame["base_fill_reason"] = scored["fill_reason"].astype("string")
    frame["fill_quality_score_bucket"] = bucket_fill_quality_score(frame["fill_quality_score"])
    return frame.reset_index(drop=True)


def score_fill_quality_row(row: pd.Series) -> dict[str, Any]:
    """Score one candidate from 0 to 100 using daily-data fill proxies."""

    turnover = safe_float(row.get("day0_turnover_twd"))
    volume_ratio = safe_float(row.get("volume_ratio_20d"))
    range_pct = safe_float(row.get("range_pct"))
    close_location = safe_float(row.get("close_location"))
    consecutive_count = safe_float(row.get("consecutive_limit_up_count"))
    day0_price = safe_float(row.get("day0_close"))
    first_limit = bool(row.get("first_limit_up_in_sequence", False))
    no_trade_lock = bool(row.get("is_no_trade_lock", False))

    score = 50.0
    reasons: list[str] = []

    if turnover >= 500_000_000:
        score += 20
        reasons.append("turnover>=500m")
    if turnover >= 200_000_000:
        score += 10
        reasons.append("turnover>=200m")
    if 2 <= volume_ratio <= 10:
        score += 10
        reasons.append("volume_ratio_2_to_10")
    if range_pct >= 0.03:
        score += 10
        reasons.append("range>=3pct")
    if 0.80 <= close_location <= 0.99:
        score += 10
        reasons.append("close_location_0.8_to_0.99")
    if first_limit:
        score += 10
        reasons.append("first_limit_up")
    if no_trade_lock:
        score -= 30
        reasons.append("high==low==close_no_trade_lock_proxy")
    if range_pct < 0.01:
        score -= 20
        reasons.append("range<1pct")
    if volume_ratio >= 10:
        score -= 15
        reasons.append("volume_ratio>=10")
    if consecutive_count >= 2:
        score -= 10
        reasons.append("consecutive_limit_ups>=2")
    if day0_price < 10:
        score -= 10
        reasons.append("price<10")
    if turnover < 100_000_000:
        score -= 10
        reasons.append("turnover<100m")

    bounded = float(min(100.0, max(0.0, score)))
    return {
        "fill_quality_score": bounded,
        "fill_reason": "; ".join(reasons) if reasons else "neutral_daily_proxy",
    }


def build_fill_assumption_events(
    candidates: pd.DataFrame,
    *,
    thresholds: FillAuditThresholds,
) -> pd.DataFrame:
    """Return one event row per candidate and fill assumption."""

    if candidates.empty:
        return pd.DataFrame(columns=AUDIT_EVENT_COLUMNS)

    rows: list[pd.DataFrame] = []
    for assumption in FILL_ASSUMPTIONS:
        fillable, reasons = evaluate_fill_assumption(
            candidates,
            fill_assumption=assumption,
            thresholds=thresholds,
        )
        part = candidates.copy()
        part["fill_assumption"] = assumption
        part["fillable_boolean"] = fillable.astype(bool).to_numpy()
        part["fill_reason"] = (
            part["base_fill_reason"].astype("string")
            + "; "
            + pd.Series(reasons, index=part.index).astype("string")
        )
        rows.append(part)

    output = pd.concat(rows, ignore_index=True)
    for column in AUDIT_EVENT_COLUMNS:
        if column not in output.columns:
            output[column] = np.nan
    return output[AUDIT_EVENT_COLUMNS]


def evaluate_fill_assumption(
    candidates: pd.DataFrame,
    *,
    fill_assumption: str,
    thresholds: FillAuditThresholds | None = None,
) -> tuple[pd.Series, pd.Series]:
    """Evaluate fillability under one daily-data proxy assumption."""

    selected_thresholds = thresholds or FillAuditThresholds()
    assumption = normalize_fill_assumption(fill_assumption)
    if candidates.empty:
        empty = pd.Series(dtype=bool)
        return empty, pd.Series(dtype="string")

    if assumption == "optimistic":
        return (
            pd.Series(True, index=candidates.index),
            pd.Series("optimistic_assumes_close_fill", index=candidates.index, dtype="string"),
        )

    turnover = pd.to_numeric(candidates["day0_turnover_twd"], errors="coerce")
    volume_ratio = pd.to_numeric(candidates["volume_ratio_20d"], errors="coerce")
    range_pct = pd.to_numeric(candidates["range_pct"], errors="coerce")
    close_location = pd.to_numeric(candidates["close_location"], errors="coerce")
    high = pd.to_numeric(candidates["day0_high"], errors="coerce")
    low = pd.to_numeric(candidates["day0_low"], errors="coerce")
    close = pd.to_numeric(candidates["day0_close"], errors="coerce")
    no_trade_lock = candidates["is_no_trade_lock"].fillna(False).astype(bool)
    high_close_equal = candidates["high_close_equal"].fillna(False).astype(bool)
    has_intraday_range = high > low

    if assumption == "moderate":
        fillable = (
            (turnover >= selected_thresholds.moderate_min_turnover_twd)
            & (volume_ratio >= selected_thresholds.moderate_min_volume_ratio_20d)
            & has_intraday_range
            & (range_pct >= selected_thresholds.moderate_min_range_pct)
            & (close_location >= selected_thresholds.moderate_min_close_location)
        )
        reasons = build_assumption_reasons(
            candidates,
            fillable=fillable,
            pass_reason="moderate_daily_activity_evidence",
            fail_reason="failed_moderate_turnover_volume_range_or_close_location",
        )
        return fillable.fillna(False), reasons

    narrow_locked = (
        high_close_equal
        & (range_pct < selected_thresholds.narrow_lock_range_pct)
        & (turnover < selected_thresholds.extremely_high_turnover_twd)
    )
    range_ok = (range_pct >= selected_thresholds.conservative_min_range_pct) | (
        (turnover >= selected_thresholds.extremely_high_turnover_twd)
        & (range_pct > 0)
        & ~no_trade_lock
    )
    fillable = (
        (turnover >= selected_thresholds.conservative_min_turnover_twd)
        & (volume_ratio >= selected_thresholds.conservative_min_volume_ratio_20d)
        & (close_location >= selected_thresholds.conservative_min_close_location)
        & has_intraday_range
        & range_ok
        & ~no_trade_lock
        & ~narrow_locked
    )
    reasons = build_assumption_reasons(
        candidates,
        fillable=fillable,
        pass_reason="conservative_strong_activity_evidence",
        fail_reason="likely_unfillable_or_insufficient_daily_activity_evidence",
    )
    reasons = reasons.mask(no_trade_lock, "high==low==close_unfillable_under_conservative")
    reasons = reasons.mask(narrow_locked & ~no_trade_lock, "narrow_limit_close_likely_unfillable")
    return fillable.fillna(False), reasons


def build_assumption_reasons(
    candidates: pd.DataFrame,
    *,
    fillable: pd.Series,
    pass_reason: str,
    fail_reason: str,
) -> pd.Series:
    reasons = pd.Series(fail_reason, index=candidates.index, dtype="string")
    reasons = reasons.mask(fillable.fillna(False), pass_reason)
    return reasons


def summarize_fill_audit(
    candidates: pd.DataFrame,
    *,
    thresholds: FillAuditThresholds | None = None,
    fill_quality_thresholds: Sequence[float] = DEFAULT_FILL_QUALITY_THRESHOLDS,
) -> pd.DataFrame:
    """Summarize returns after applying fill assumptions and score thresholds."""

    if candidates.empty:
        return pd.DataFrame(columns=SUMMARY_COLUMNS)

    selected_thresholds = thresholds or FillAuditThresholds()
    scenario_rows: list[pd.DataFrame] = []
    score_threshold_values: list[float | None] = [None, *[float(value) for value in fill_quality_thresholds]]
    for assumption in FILL_ASSUMPTIONS:
        fillable, _ = evaluate_fill_assumption(
            candidates,
            fill_assumption=assumption,
            thresholds=selected_thresholds,
        )
        for score_threshold in score_threshold_values:
            mask = fillable.copy()
            if score_threshold is not None:
                mask &= pd.to_numeric(candidates["fill_quality_score"], errors="coerce") >= score_threshold
            scenario_rows.append(
                summarize_fill_scenario(
                    candidates,
                    fillable_mask=mask.fillna(False),
                    fill_assumption=assumption,
                    min_fill_quality_score=score_threshold,
                )
            )

    if not scenario_rows:
        return pd.DataFrame(columns=SUMMARY_COLUMNS)
    return pd.concat(scenario_rows, ignore_index=True)[SUMMARY_COLUMNS]


def summarize_fill_scenario(
    candidates: pd.DataFrame,
    *,
    fillable_mask: pd.Series,
    fill_assumption: str,
    min_fill_quality_score: float | None,
) -> pd.DataFrame:
    scenario_id = make_scenario_id(fill_assumption, min_fill_quality_score)
    frame = candidates.copy()
    frame["entry_date"] = pd.to_datetime(frame["entry_date"])
    frame["year"] = frame["entry_date"].dt.year
    frame["_fillable"] = fillable_mask.reindex(frame.index).fillna(False).astype(bool)

    group_specs = [
        ("overall", []),
        ("by_market", ["market"]),
        ("by_year", ["year"]),
        ("by_turnover_bucket", ["turnover_bucket"]),
        ("by_volume_shock_bucket", ["day0_volume_shock_bucket"]),
        ("by_range_pct_bucket", ["range_pct_bucket"]),
        ("by_fill_quality_score_bucket", ["fill_quality_score_bucket"]),
    ]
    parts = [
        summarize_fill_group(
            frame,
            scenario_id=scenario_id,
            fill_assumption=fill_assumption,
            min_fill_quality_score=min_fill_quality_score,
            summary_level=level,
            group_columns=columns,
        )
        for level, columns in group_specs
    ]
    return pd.concat(parts, ignore_index=True)


def summarize_fill_group(
    frame: pd.DataFrame,
    *,
    scenario_id: str,
    fill_assumption: str,
    min_fill_quality_score: float | None,
    summary_level: str,
    group_columns: list[str],
) -> pd.DataFrame:
    grouped = frame.groupby(group_columns, dropna=False, observed=False) if group_columns else [((), frame)]
    rows: list[dict[str, Any]] = []
    for group_key, group in grouped:
        key_values = group_key if isinstance(group_key, tuple) else (group_key,)
        selected = group[group["_fillable"]].sort_values("entry_date")
        row: dict[str, Any] = {
            "scenario_id": scenario_id,
            "fill_assumption": fill_assumption,
            "min_fill_quality_score": min_fill_quality_score,
            "summary_level": summary_level,
            "market": None,
            "year": None,
            "turnover_bucket": None,
            "day0_volume_shock_bucket": None,
            "range_pct_bucket": None,
            "fill_quality_score_bucket": None,
            **calculate_fill_metrics(group, selected),
        }
        for column, value in zip(group_columns, key_values):
            row[column] = value
        rows.append(row)
    return pd.DataFrame(rows, columns=SUMMARY_COLUMNS)


def calculate_fill_metrics(candidates: pd.DataFrame, selected: pd.DataFrame) -> dict[str, float]:
    candidate_count = int(len(candidates))
    fillable_count = int(len(selected))
    skipped = candidate_count - fillable_count
    if selected.empty:
        return {
            "candidate_trades": candidate_count,
            "fillable_trades": 0,
            "skipped_trades": skipped,
            "fillable_ratio": 0.0 if candidate_count else np.nan,
            "raw_win_rate": np.nan,
            "win_rate": np.nan,
            "average_gross_return": np.nan,
            "median_gross_return": np.nan,
            "average_net_return": np.nan,
            "median_net_return": np.nan,
            "profit_factor": np.nan,
            "total_net_pnl": 0.0,
            "max_drawdown": 0.0,
            "average_turnover_twd": np.nan,
            "average_volume_ratio_20d": np.nan,
            "average_range_pct": np.nan,
            "average_fill_quality_score": np.nan,
        }

    gross_return = pd.to_numeric(selected["gross_return"], errors="coerce")
    net_return = pd.to_numeric(selected["net_return"], errors="coerce")
    net_pnl = pd.to_numeric(selected["net_pnl"], errors="coerce").fillna(0.0)
    losses = abs(net_pnl[net_pnl < 0].sum())
    gains = net_pnl[net_pnl > 0].sum()
    if losses == 0 and gains > 0:
        profit_factor = float("inf")
    elif losses == 0:
        profit_factor = np.nan
    else:
        profit_factor = float(gains / losses)
    return {
        "candidate_trades": candidate_count,
        "fillable_trades": fillable_count,
        "skipped_trades": skipped,
        "fillable_ratio": fillable_count / candidate_count if candidate_count else np.nan,
        "raw_win_rate": float((gross_return > 0).mean()),
        "win_rate": float((net_pnl > 0).mean()),
        "average_gross_return": float(gross_return.mean()),
        "median_gross_return": float(gross_return.median()),
        "average_net_return": float(net_return.mean()),
        "median_net_return": float(net_return.median()),
        "profit_factor": profit_factor,
        "total_net_pnl": float(net_pnl.sum()),
        "max_drawdown": max_drawdown_by_exit_date(selected),
        "average_turnover_twd": float(pd.to_numeric(selected["day0_turnover_twd"], errors="coerce").mean()),
        "average_volume_ratio_20d": float(pd.to_numeric(selected["volume_ratio_20d"], errors="coerce").mean()),
        "average_range_pct": float(pd.to_numeric(selected["range_pct"], errors="coerce").mean()),
        "average_fill_quality_score": float(pd.to_numeric(selected["fill_quality_score"], errors="coerce").mean()),
    }


def build_assumption_comparison(summary: pd.DataFrame) -> pd.DataFrame:
    """Extract overall rows for quick assumption comparison."""

    if summary.empty:
        return pd.DataFrame(columns=SUMMARY_COLUMNS)
    return summary[summary["summary_level"].eq("overall")].reset_index(drop=True)


def max_drawdown_by_exit_date(trades: pd.DataFrame) -> float:
    """Calculate drawdown from daily realized PnL, avoiding same-day row-order noise."""

    if trades.empty:
        return 0.0
    date_column = "exit_date" if "exit_date" in trades.columns else "entry_date"
    dated = trades.copy()
    dated["_pnl_date"] = pd.to_datetime(dated[date_column]).dt.normalize()
    daily_pnl = (
        dated.groupby("_pnl_date", dropna=False)["net_pnl"]
        .sum()
        .sort_index()
        .reset_index(drop=True)
    )
    return max_drawdown_from_pnl(daily_pnl)


def bucket_range_pct(values: pd.Series) -> pd.Series:
    buckets = pd.cut(
        pd.to_numeric(values, errors="coerce"),
        bins=[-np.inf, 0.0, 0.01, 0.02, 0.03, 0.05, np.inf],
        labels=["0", "0_1pct", "1_2pct", "2_3pct", "3_5pct", ">=5pct"],
    )
    return buckets.astype("string").fillna("unknown")


def bucket_fill_quality_score(values: pd.Series) -> pd.Series:
    buckets = pd.cut(
        pd.to_numeric(values, errors="coerce"),
        bins=[-np.inf, 40.0, 50.0, 60.0, 70.0, 80.0, np.inf],
        labels=["<40", "40_50", "50_60", "60_70", "70_80", ">=80"],
    )
    return buckets.astype("string").fillna("unknown")


def normalize_fill_assumption(fill_assumption: str) -> str:
    value = fill_assumption.lower().strip()
    if value not in FILL_ASSUMPTIONS:
        raise ValueError(f"fill_assumption must be one of {FILL_ASSUMPTIONS}, got {fill_assumption!r}")
    return value


def make_scenario_id(fill_assumption: str, min_fill_quality_score: float | None) -> str:
    if min_fill_quality_score is None:
        return f"{fill_assumption}_all_scores"
    return f"{fill_assumption}_score_ge_{min_fill_quality_score:g}"


def safe_float(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return np.nan
    return result


def empty_candidate_frame() -> pd.DataFrame:
    base = empty_trade_report_frame()
    for column in [
        "day0_price",
        "turnover_twd",
        "range_pct",
        "is_no_trade_lock",
        "high_close_equal",
        "range_pct_bucket",
        "fill_quality_score",
        "base_fill_reason",
        "fill_quality_score_bucket",
    ]:
        base[column] = pd.Series(dtype="float64" if column not in {"base_fill_reason"} else "string")
    return base


def validate_fill_audit_inputs(
    *,
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
    fixed_notional_twd: float,
    min_turnover_twd: float | None,
    min_volume_ratio_20d: float | None,
    min_close_location: float | None,
    min_price: float | None,
    max_price: float | None,
    max_consecutive_limit_ups: int | None,
    commission_rate: float,
    commission_discount: float,
    sell_tax_rate: float,
    slippage_bps_per_side: float,
    minimum_commission_twd: float,
    thresholds: FillAuditThresholds,
    fill_quality_thresholds: Sequence[float],
) -> None:
    if pd.Timestamp(end) < pd.Timestamp(start):
        raise ValueError("end must be on or after start")
    if fixed_notional_twd <= 0:
        raise ValueError("fixed_notional_twd must be positive")
    for name, value in {
        "min_turnover_twd": min_turnover_twd,
        "min_volume_ratio_20d": min_volume_ratio_20d,
        "min_close_location": min_close_location,
        "min_price": min_price,
        "max_price": max_price,
        "max_consecutive_limit_ups": max_consecutive_limit_ups,
        "commission_rate": commission_rate,
        "commission_discount": commission_discount,
        "sell_tax_rate": sell_tax_rate,
        "slippage_bps_per_side": slippage_bps_per_side,
        "minimum_commission_twd": minimum_commission_twd,
        **thresholds.__dict__,
    }.items():
        if value is not None and value < 0:
            raise ValueError(f"{name} must be non-negative")
    if min_price is not None and max_price is not None and max_price < min_price:
        raise ValueError("max_price must be greater than or equal to min_price")
    for threshold in fill_quality_thresholds:
        if threshold < 0 or threshold > 100:
            raise ValueError("fill_quality_thresholds must be between 0 and 100")


def default_report_dir() -> Path:
    return get_settings().project_root / "reports"


def print_fill_audit_summary(comparison: pd.DataFrame) -> None:
    if comparison.empty:
        print("\nclosed-limit-up fill audit: no candidate trades after strategy filters")
        return

    display_columns = [
        "scenario_id",
        "candidate_trades",
        "fillable_trades",
        "fillable_ratio",
        "average_net_return",
        "median_net_return",
        "profit_factor",
        "total_net_pnl",
        "max_drawdown",
    ]
    print("\nclosed-limit-up fill realism audit")
    print(comparison[display_columns].to_string(index=False))

    core = comparison[
        comparison["scenario_id"].isin(
            ["optimistic_all_scores", "moderate_all_scores", "conservative_all_scores"]
        )
    ]
    if not core.empty:
        print("\ncore assumption answer")
        print(
            core[
                [
                    "fill_assumption",
                    "fillable_trades",
                    "fillable_ratio",
                    "average_net_return",
                    "median_net_return",
                    "profit_factor",
                    "max_drawdown",
                ]
            ].to_string(index=False)
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Closed-limit-up overnight fill-realism audit")
    parser.add_argument("--db", type=Path, default=get_settings().db_path, help="DuckDB path")
    parser.add_argument("--start", required=True, help="Start date, YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="End date, YYYY-MM-DD")
    parser.add_argument("--market", choices=["TWSE", "TPEX", "BOTH"], default="BOTH")
    parser.add_argument("--fixed-notional-twd", type=float, default=100_000.0)
    parser.add_argument("--min-turnover-twd", type=float)
    parser.add_argument("--min-volume-ratio-20d", type=float)
    parser.add_argument("--min-close-location", type=float)
    parser.add_argument("--min-price", type=float)
    parser.add_argument("--max-price", type=float)
    parser.add_argument("--max-consecutive-limit-ups", type=int)
    parser.add_argument("--only-first-limit-up", nargs="?", const=True, default=False, type=parse_bool)
    parser.add_argument("--exclude-if-prior-5d-return-above", type=float)
    parser.add_argument("--exclude-if-prior-20d-return-above", type=float)
    parser.add_argument("--commission-rate", type=float, default=0.001425)
    parser.add_argument("--commission-discount", type=float, default=0.28)
    parser.add_argument(
        "--sell-tax-rate",
        type=float,
        default=0.003,
        help="Normal overnight stock sale tax. Defaults to 0.30%.",
    )
    parser.add_argument("--slippage-bps-per-side", type=float, default=5.0)
    parser.add_argument("--minimum-commission-twd", type=float, default=20.0)
    parser.add_argument("--moderate-fill-min-turnover-twd", type=float, default=100_000_000.0)
    parser.add_argument("--moderate-fill-min-volume-ratio-20d", type=float, default=2.0)
    parser.add_argument("--moderate-fill-min-range-pct", type=float, default=0.01)
    parser.add_argument("--moderate-fill-min-close-location", type=float, default=0.80)
    parser.add_argument("--conservative-fill-min-turnover-twd", type=float, default=200_000_000.0)
    parser.add_argument("--conservative-fill-min-volume-ratio-20d", type=float, default=3.0)
    parser.add_argument("--conservative-fill-min-range-pct", type=float, default=0.03)
    parser.add_argument("--conservative-fill-min-close-location", type=float, default=0.85)
    parser.add_argument("--narrow-lock-range-pct", type=float, default=0.005)
    parser.add_argument("--extremely-high-turnover-twd", type=float, default=1_000_000_000.0)
    parser.add_argument(
        "--fill-quality-thresholds",
        nargs="*",
        type=float,
        default=list(DEFAULT_FILL_QUALITY_THRESHOLDS),
    )
    parser.add_argument("--output-dir", type=Path, default=default_report_dir())
    parser.add_argument(
        "--skip-build-events",
        action="store_true",
        help="Use existing event_candidates instead of rebuilding for the window",
    )
    args = parser.parse_args()

    thresholds = FillAuditThresholds(
        moderate_min_turnover_twd=args.moderate_fill_min_turnover_twd,
        moderate_min_volume_ratio_20d=args.moderate_fill_min_volume_ratio_20d,
        moderate_min_range_pct=args.moderate_fill_min_range_pct,
        moderate_min_close_location=args.moderate_fill_min_close_location,
        conservative_min_turnover_twd=args.conservative_fill_min_turnover_twd,
        conservative_min_volume_ratio_20d=args.conservative_fill_min_volume_ratio_20d,
        conservative_min_range_pct=args.conservative_fill_min_range_pct,
        conservative_min_close_location=args.conservative_fill_min_close_location,
        narrow_lock_range_pct=args.narrow_lock_range_pct,
        extremely_high_turnover_twd=args.extremely_high_turnover_twd,
    )
    audit_events, summary, comparison = run_closed_limit_up_fill_audit(
        db_path=args.db,
        start=args.start,
        end=args.end,
        markets=normalize_markets([args.market]),
        fixed_notional_twd=args.fixed_notional_twd,
        min_turnover_twd=args.min_turnover_twd,
        min_volume_ratio_20d=args.min_volume_ratio_20d,
        min_close_location=args.min_close_location,
        min_price=args.min_price,
        max_price=args.max_price,
        max_consecutive_limit_ups=args.max_consecutive_limit_ups,
        only_first_limit_up=args.only_first_limit_up,
        exclude_if_prior_5d_return_above=args.exclude_if_prior_5d_return_above,
        exclude_if_prior_20d_return_above=args.exclude_if_prior_20d_return_above,
        commission_rate=args.commission_rate,
        commission_discount=args.commission_discount,
        sell_tax_rate=args.sell_tax_rate,
        slippage_bps_per_side=args.slippage_bps_per_side,
        minimum_commission_twd=args.minimum_commission_twd,
        fill_thresholds=thresholds,
        fill_quality_thresholds=args.fill_quality_thresholds,
        output_dir=args.output_dir,
        rebuild_events=not args.skip_build_events,
    )

    print(f"Wrote {len(audit_events)} event-assumption rows to {args.output_dir / 'closed_limit_up_fill_audit_events.csv'}")
    print(f"Wrote {len(summary)} summary rows to {args.output_dir / 'closed_limit_up_fill_audit_summary.csv'}")
    print(
        "Wrote "
        f"{len(comparison)} comparison rows to "
        f"{args.output_dir / 'closed_limit_up_fill_assumption_comparison.csv'}"
    )
    print_fill_audit_summary(comparison)


if __name__ == "__main__":
    main()
