"""Daily paper-trading signal generator for closed-limit-up overnight trades."""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from config.settings import get_settings
from src.backtests.closed_limit_up_fill_audit import (
    FillAuditThresholds,
    add_fill_proxy_features,
    evaluate_fill_assumption,
)
from src.backtests.event_study import build_event_candidates
from src.backtests.strategy_limit_momentum import BOARD_LOT_SIZE
from src.db import get_connection, init_db
from src.live.strategy_profiles import (
    ALL_PROFILE_NAMES,
    ORIGINAL_CHAMPION,
    StrategyProfile,
    resolve_profile_selection,
)


STRATEGY_NAME = "closed_limit_up_overnight_paper_signal"
EXECUTION_WARNING = (
    "Paper trade only. Day0 close limit-up fills may not be executable without "
    "auction/order-book confirmation."
)
PLANNED_EXIT = "Day1 open"
AVOID_SECTORS = ("Healthcare", "Materials")

OUTPUT_COLUMNS = [
    "profile_name",
    "candidate_hash",
    "signal_date",
    "symbol",
    "name",
    "market",
    "sector",
    "industry",
    "close_price",
    "limit_up_price",
    "turnover_twd",
    "volume_ratio_20d",
    "consecutive_limit_up_count",
    "fill_quality_score",
    "same_sector_limitup_count",
    "same_industry_limitup_count",
    "market_limitup_count",
    "theme_breadth_score",
    "planned_entry_price",
    "planned_exit",
    "target_notional_twd",
    "capped_notional_twd",
    "planned_shares",
    "planned_buy_notional_twd",
    "estimated_buy_cost",
    "estimated_cash_required",
    "ranking",
    "notes",
    "execution_warning",
]


@dataclass(frozen=True)
class SignalGeneratorConfig:
    """Production-style parameters for daily paper signal generation."""

    capital_twd: float = 1_000_000.0
    profile_name: str = ORIGINAL_CHAMPION
    candidate_hash: str = ""
    market: str = "TPEX"
    market_regime_filter: str = "none"
    ranking_method: str = "fill_quality_score"
    min_turnover_twd: float = 500_000_000.0
    min_volume_ratio_20d: float = 1.5
    min_fill_quality_score: float = 60.0
    max_consecutive_limit_ups: int = 3
    min_price: float = 10.0
    max_price: float = 100.0
    prior_5d_return_max: float | None = None
    prior_20d_return_max: float | None = None
    max_positions: int = 5
    target_notional_twd: float = 300_000.0
    max_notional_per_symbol_pct: float = 0.20
    max_notional_per_sector_pct: float = 0.70
    max_notional_per_industry_pct: float = 0.35
    board_lot_size: int = BOARD_LOT_SIZE
    avoid_sectors: tuple[str, ...] = AVOID_SECTORS
    allowed_sectors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    commission_rate: float = 0.001425
    commission_discount: float = 0.28
    slippage_bps_per_side: float = 5.0
    minimum_commission_twd: float = 20.0

    @property
    def markets(self) -> list[str]:
        market = self.market.upper().strip()
        if market == "BOTH":
            return ["TWSE", "TPEX"]
        return [market]


def generate_closed_limit_up_signals(
    *,
    db_path: Path | str,
    capital_twd: float = 1_000_000.0,
    signal_date: str | pd.Timestamp = "latest",
    output_dir: Path | str | None = None,
    refresh_events: bool = True,
    profile: str | StrategyProfile = ORIGINAL_CHAMPION,
) -> tuple[pd.DataFrame, pd.DataFrame, Path, Path]:
    """Generate daily paper orders and write CSV/Markdown reports."""

    report_dir = Path(output_dir) if output_dir is not None else get_settings().project_root / "reports" / "live_signals"
    config = make_signal_config(profile=profile, capital_twd=capital_twd, report_dir=report_dir)
    validate_config(config)
    init_db(db_path)

    with get_connection(db_path) as conn:
        resolved_signal_date = resolve_signal_date(conn, signal_date, market=config.market)

    if refresh_events:
        build_event_candidates(
            db_path=db_path,
            start=resolved_signal_date,
            end=resolved_signal_date,
            markets=config.markets,
        )

    with get_connection(db_path, read_only=True) as conn:
        raw_candidates = load_raw_signal_candidates(conn, signal_date=resolved_signal_date, market=config.market)
        history = load_limit_up_history(conn, signal_date=resolved_signal_date, market=config.market)
        price_history = load_price_history(conn, signal_date=resolved_signal_date, market=config.market)
        regime_context = load_signal_date_regime(conn, signal_date=resolved_signal_date)

    enriched = prepare_signal_candidates(raw_candidates, history=history, price_history=price_history, regime_context=regime_context)
    filtered, skipped_reasons = apply_live_filters(enriched, config)
    orders, allocation_skips = build_planned_orders(filtered, config)
    skipped_summary = summarize_skips(skipped_reasons, allocation_skips)

    report_dir.mkdir(parents=True, exist_ok=True)
    date_label = resolved_signal_date.strftime("%Y-%m-%d")
    csv_path = report_dir / f"closed_limit_up_signals_{config.profile_name}_{date_label}.csv"
    md_path = report_dir / f"closed_limit_up_signals_{config.profile_name}_{date_label}.md"
    orders.to_csv(csv_path, index=False)
    md_path.write_text(
        build_markdown_report(
            signal_date=resolved_signal_date,
            config=config,
            raw_candidates=raw_candidates,
            enriched=enriched,
            filtered=filtered,
            orders=orders,
            skipped_summary=skipped_summary,
        ),
        encoding="utf-8",
    )
    return orders, skipped_summary, csv_path, md_path


def make_signal_config(
    *,
    profile: str | StrategyProfile,
    capital_twd: float,
    report_dir: Path,
) -> SignalGeneratorConfig:
    """Convert a strategy profile into generator config."""

    if isinstance(profile, StrategyProfile):
        resolved = profile
    else:
        resolved = resolve_profile_selection(profile, report_dir=report_dir.parent if report_dir.name == "live_signals" else report_dir)[0]
    return SignalGeneratorConfig(
        capital_twd=capital_twd,
        profile_name=resolved.profile_name,
        candidate_hash=resolved.candidate_hash,
        market=resolved.market,
        market_regime_filter=resolved.market_regime_filter,
        ranking_method=resolved.ranking_method,
        min_turnover_twd=resolved.min_turnover_twd,
        min_volume_ratio_20d=resolved.min_volume_ratio_20d,
        min_fill_quality_score=resolved.min_fill_quality_score,
        max_consecutive_limit_ups=resolved.max_consecutive_limit_ups,
        min_price=resolved.min_price,
        max_price=resolved.max_price,
        prior_5d_return_max=resolved.prior_5d_return_max,
        prior_20d_return_max=resolved.prior_20d_return_max,
        max_positions=resolved.max_positions,
        target_notional_twd=resolved.target_notional_twd,
        max_notional_per_symbol_pct=resolved.max_notional_per_symbol_pct,
        max_notional_per_sector_pct=resolved.max_notional_per_sector_pct,
        max_notional_per_industry_pct=resolved.max_notional_per_industry_pct,
        board_lot_size=resolved.board_lot_size,
        avoid_sectors=resolved.avoid_sectors,
        allowed_sectors=resolved.allowed_sectors,
        warnings=resolved.warnings,
    )


def generate_closed_limit_up_signals_for_profiles(
    *,
    db_path: Path | str,
    capital_twd: float = 1_000_000.0,
    signal_date: str | pd.Timestamp = "latest",
    output_dir: Path | str | None = None,
    profile: str = "all",
    profiles_override: list[StrategyProfile] | None = None,
    force_combined: bool = False,
    refresh_events: bool = True,
) -> tuple[dict[str, tuple[pd.DataFrame, pd.DataFrame, Path, Path]], Path | None, Path | None]:
    """Generate signals for one or all profiles and optional all-profile comparison files."""

    report_dir = Path(output_dir) if output_dir is not None else get_settings().project_root / "reports" / "live_signals"
    profiles = (
        profiles_override
        if profiles_override is not None
        else resolve_profile_selection(profile, report_dir=report_dir.parent if report_dir.name == "live_signals" else report_dir)
    )
    results: dict[str, tuple[pd.DataFrame, pd.DataFrame, Path, Path]] = {}
    for item in profiles:
        results[item.profile_name] = generate_closed_limit_up_signals(
            db_path=db_path,
            capital_twd=capital_twd,
            signal_date=signal_date,
            output_dir=report_dir,
            refresh_events=refresh_events,
            profile=item,
        )
    if len(results) <= 1 and not force_combined:
        return results, None, None
    combined = pd.concat([value[0] for value in results.values()], ignore_index=True) if results else empty_orders()
    signal_dates = pd.to_datetime(combined["signal_date"], errors="coerce").dropna() if not combined.empty else pd.Series(dtype="datetime64[ns]")
    if signal_dates.empty:
        fallback_date_label = fallback_signal_date_label(signal_date)
        if results:
            first_orders = next(iter(results.values()))[0]
            if first_orders.empty:
                date_label = fallback_date_label
            else:
                date_label = str(first_orders["signal_date"].iloc[0])
        else:
            date_label = fallback_date_label
    else:
        date_label = signal_dates.iloc[0].strftime("%Y-%m-%d")
    csv_path = report_dir / f"closed_limit_up_signals_all_profiles_{date_label}.csv"
    md_path = report_dir / f"closed_limit_up_signals_all_profiles_{date_label}.md"
    combined.to_csv(csv_path, index=False)
    md_path.write_text(build_all_profiles_markdown(results=results, combined=combined, date_label=date_label), encoding="utf-8")
    return results, csv_path, md_path


def fallback_signal_date_label(signal_date: str | pd.Timestamp) -> str:
    """Best-effort label for all-profile files when no profile generated orders."""

    if isinstance(signal_date, str) and signal_date.strip().lower() == "latest":
        return "unknown"
    try:
        return pd.Timestamp(signal_date).strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return "unknown"


def resolve_signal_date(conn, signal_date: str | pd.Timestamp, *, market: str) -> pd.Timestamp:
    """Resolve `latest` to the newest available market date in daily_prices."""

    if isinstance(signal_date, str) and signal_date.strip().lower() == "latest":
        markets = ["TWSE", "TPEX"] if market.upper() == "BOTH" else [market.upper()]
        placeholders = ",".join(["?"] * len(markets))
        row = conn.execute(
            f"""
            SELECT MAX(trade_date)
            FROM daily_prices
            WHERE UPPER(market) IN ({placeholders})
            """,
            markets,
        ).fetchone()
        if not row or row[0] is None:
            raise ValueError(f"No daily_prices rows found for market={market}.")
        return pd.Timestamp(row[0]).normalize()
    resolved = pd.Timestamp(signal_date).normalize()
    if pd.isna(resolved):
        raise ValueError(f"Invalid signal_date: {signal_date!r}")
    return resolved


def load_raw_signal_candidates(conn, *, signal_date: pd.Timestamp, market: str) -> pd.DataFrame:
    """Load same-day TPEX closed-limit-up event candidates with sector metadata."""

    markets = ["TWSE", "TPEX"] if market.upper() == "BOTH" else [market.upper()]
    placeholders = ",".join(["?"] * len(markets))
    return conn.execute(
        f"""
        SELECT
            ec.event_id,
            ec.symbol,
            ec.trade_date AS signal_date,
            ec.market,
            ec.day0_open,
            ec.day0_high,
            ec.day0_low,
            ec.day0_close,
            ec.day0_volume_shares,
            ec.day0_turnover_twd,
            ec.close_location,
            ec.volume_ratio_20d,
            ec.closed_limit_up,
            dp.name AS daily_price_name,
            dp.limit_up_price,
            m.name AS mapped_name,
            m.sector,
            m.industry,
            m.source AS sector_source
        FROM event_candidates ec
        JOIN daily_prices dp
          ON dp.symbol = ec.symbol
         AND dp.market = ec.market
         AND dp.trade_date = ec.trade_date
        LEFT JOIN stock_sector_map m
          ON m.symbol = ec.symbol
         AND m.market = ec.market
        WHERE ec.trade_date = ?
          AND UPPER(ec.market) IN ({placeholders})
          AND ec.event_type = 'closed_limit_up'
          AND ec.closed_limit_up = TRUE
        ORDER BY ec.day0_turnover_twd DESC, ec.symbol
        """,
        [signal_date.date(), *markets],
    ).fetch_df()


def load_limit_up_history(conn, *, signal_date: pd.Timestamp, market: str) -> pd.DataFrame:
    """Load historical closed-limit-up flags needed for sequence counts."""

    markets = ["TWSE", "TPEX"] if market.upper() == "BOTH" else [market.upper()]
    placeholders = ",".join(["?"] * len(markets))
    return conn.execute(
        f"""
        SELECT
            symbol,
            market,
            trade_date,
            closed_limit_up
        FROM daily_prices
        WHERE UPPER(market) IN ({placeholders})
          AND trade_date <= ?
        ORDER BY symbol, trade_date
        """,
        [*markets, signal_date.date()],
    ).fetch_df()


def load_price_history(conn, *, signal_date: pd.Timestamp, market: str) -> pd.DataFrame:
    """Load closes needed for prior 5D/20D return filters."""

    markets = ["TWSE", "TPEX"] if market.upper() == "BOTH" else [market.upper()]
    placeholders = ",".join(["?"] * len(markets))
    return conn.execute(
        f"""
        SELECT symbol, market, trade_date, close
        FROM daily_prices
        WHERE UPPER(market) IN ({placeholders})
          AND trade_date <= ?
        ORDER BY market, symbol, trade_date
        """,
        [*markets, signal_date.date()],
    ).fetch_df()


def load_signal_date_regime(conn, *, signal_date: pd.Timestamp) -> dict[str, Any]:
    """Load TAIEX regime context for the signal date."""

    row = conn.execute(
        """
        SELECT
            trade_date,
            daily_return,
            close_above_ma20,
            close_above_ma60,
            drawdown_from_60d_high
        FROM index_daily_prices
        WHERE UPPER(index_symbol) = 'TAIEX'
          AND trade_date = ?
        """,
        [signal_date.date()],
    ).fetchone()
    if not row:
        return {
            "index_data_available": False,
            "bull_regime": False,
            "bear_regime": False,
            "weak_market_day": False,
            "strong_market_day": False,
            "correction_regime": False,
        }
    daily_return = safe_float(row[1])
    above20 = bool(row[2])
    above60 = bool(row[3])
    drawdown = safe_float(row[4])
    return {
        "index_data_available": True,
        "taiex_day0_return": daily_return,
        "taiex_drawdown_from_60d_high": drawdown,
        "bull_regime": above20 and above60,
        "bear_regime": (not above20) and (not above60),
        "weak_market_day": daily_return <= -0.005,
        "strong_market_day": daily_return >= 0.005,
        "correction_regime": drawdown <= -0.05,
    }


def prepare_signal_candidates(
    raw_candidates: pd.DataFrame,
    *,
    history: pd.DataFrame,
    price_history: pd.DataFrame | None = None,
    regime_context: dict[str, Any] | None = None,
) -> pd.DataFrame:
    """Add names, sector labels, limit-up sequence counts, and fill scores."""

    if raw_candidates.empty:
        return empty_candidate_frame()

    frame = raw_candidates.copy()
    frame["signal_date"] = pd.to_datetime(frame["signal_date"], errors="coerce").dt.normalize()
    frame["symbol"] = frame["symbol"].astype("string").str.strip()
    frame["market"] = frame["market"].astype("string").str.upper().str.strip()
    frame["name"] = first_nonblank(frame, ["mapped_name", "daily_price_name", "symbol"])
    frame["sector"] = clean_group_values(frame.get("sector"), "MISSING_SECTOR")
    frame["industry"] = clean_group_values(frame.get("industry"), "MISSING_INDUSTRY")
    frame = add_theme_breadth_features(frame)

    for column in [
        "day0_open",
        "day0_high",
        "day0_low",
        "day0_close",
        "day0_volume_shares",
        "day0_turnover_twd",
        "close_location",
        "volume_ratio_20d",
        "limit_up_price",
    ]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")

    sequence = calculate_limit_up_sequences(history)
    frame = frame.merge(
        sequence,
        left_on=["symbol", "market", "signal_date"],
        right_on=["symbol", "market", "trade_date"],
        how="left",
    ).drop(columns=["trade_date"], errors="ignore")
    frame["consecutive_limit_up_count"] = pd.to_numeric(
        frame["consecutive_limit_up_count"],
        errors="coerce",
    ).fillna(0)
    frame["first_limit_up_in_sequence"] = frame["consecutive_limit_up_count"].eq(1)
    frame = add_prior_return_features(frame, price_history if price_history is not None else pd.DataFrame())
    regime = regime_context or {}
    for column, default in [
        ("index_data_available", False),
        ("bull_regime", False),
        ("bear_regime", False),
        ("weak_market_day", False),
        ("strong_market_day", False),
        ("correction_regime", False),
        ("taiex_day0_return", np.nan),
        ("taiex_drawdown_from_60d_high", np.nan),
    ]:
        frame[column] = regime.get(column, default)

    fill_input = frame.rename(
        columns={
            "day0_turnover_twd": "turnover_twd",
        }
    ).copy()
    fill_input["day0_turnover_twd"] = frame["day0_turnover_twd"]
    fill_input = add_fill_proxy_features(fill_input)
    fillable_moderate, fill_reasons = evaluate_fill_assumption(
        fill_input,
        fill_assumption="moderate",
        thresholds=FillAuditThresholds(moderate_min_volume_ratio_20d=1.5),
    )
    frame["fill_quality_score"] = fill_input["fill_quality_score"].to_numpy()
    frame["fill_reason"] = fill_input["base_fill_reason"].astype("string").to_numpy()
    frame["fillable_moderate"] = fillable_moderate.astype(bool).to_numpy()
    frame["moderate_fill_reason"] = fill_reasons.astype("string").to_numpy()
    return frame.sort_values(["fill_quality_score", "day0_turnover_twd", "volume_ratio_20d"], ascending=False).reset_index(
        drop=True
    )


def add_prior_return_features(candidates: pd.DataFrame, price_history: pd.DataFrame) -> pd.DataFrame:
    """Add prior 5D/20D returns using only closes before Day0."""

    output = candidates.copy()
    output["prior_5d_return"] = np.nan
    output["prior_20d_return"] = np.nan
    if output.empty or price_history.empty:
        return output
    history = price_history.copy()
    history["trade_date"] = pd.to_datetime(history["trade_date"], errors="coerce").dt.normalize()
    history["symbol"] = history["symbol"].astype("string").str.strip()
    history["market"] = history["market"].astype("string").str.upper().str.strip()
    history["close"] = pd.to_numeric(history["close"], errors="coerce")
    rows = []
    for (symbol, market), group in history.groupby(["symbol", "market"], sort=False):
        group = group.sort_values("trade_date").copy()
        close = group["close"]
        group["prior_5d_return"] = close.shift(1) / close.shift(6) - 1.0
        group["prior_20d_return"] = close.shift(1) / close.shift(21) - 1.0
        rows.append(group[["symbol", "market", "trade_date", "prior_5d_return", "prior_20d_return"]])
    prior = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    output = output.merge(
        prior,
        left_on=["symbol", "market", "signal_date"],
        right_on=["symbol", "market", "trade_date"],
        how="left",
        suffixes=("", "_calc"),
    ).drop(columns=["trade_date"], errors="ignore")
    for column in ["prior_5d_return", "prior_20d_return"]:
        calc = f"{column}_calc"
        if calc in output.columns:
            output[column] = output[column].where(output[column].notna(), output[calc])
            output = output.drop(columns=[calc])
    return output


def add_theme_breadth_features(candidates: pd.DataFrame) -> pd.DataFrame:
    """Add same-day closed-limit-up breadth by sector, industry, and market."""

    frame = candidates.copy()
    if frame.empty:
        for column in [
            "same_sector_limitup_count",
            "same_industry_limitup_count",
            "market_limitup_count",
            "theme_breadth_score",
        ]:
            frame[column] = []
        return frame
    closed = frame[frame["closed_limit_up"].fillna(False).astype(bool)].copy()
    if closed.empty:
        frame["same_sector_limitup_count"] = 0
        frame["same_industry_limitup_count"] = 0
        frame["market_limitup_count"] = 0
        frame["theme_breadth_score"] = 0.0
        return frame

    sector_counts = (
        closed.groupby(["signal_date", "sector"], dropna=False).size().rename("same_sector_limitup_count").reset_index()
    )
    industry_counts = (
        closed.groupby(["signal_date", "industry"], dropna=False).size().rename("same_industry_limitup_count").reset_index()
    )
    market_counts = closed.groupby("signal_date", dropna=False).size().rename("market_limitup_count").reset_index()
    frame = frame.merge(sector_counts, on=["signal_date", "sector"], how="left")
    frame = frame.merge(industry_counts, on=["signal_date", "industry"], how="left")
    frame = frame.merge(market_counts, on="signal_date", how="left")
    for column in ["same_sector_limitup_count", "same_industry_limitup_count", "market_limitup_count"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0).astype(int)
    frame["theme_breadth_score"] = (
        2.0 * frame["same_sector_limitup_count"]
        + 3.0 * frame["same_industry_limitup_count"]
        + 0.25 * frame["market_limitup_count"]
    )
    return frame


def calculate_limit_up_sequences(history: pd.DataFrame) -> pd.DataFrame:
    """Count consecutive closed-limit-up days through each date."""

    if history.empty:
        return pd.DataFrame(columns=["symbol", "market", "trade_date", "consecutive_limit_up_count"])
    frame = history.copy()
    frame["symbol"] = frame["symbol"].astype("string").str.strip()
    frame["market"] = frame["market"].astype("string").str.upper().str.strip()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce").dt.normalize()
    frame["closed_limit_up"] = frame["closed_limit_up"].fillna(False).astype(bool)
    frame = frame.sort_values(["market", "symbol", "trade_date"]).reset_index(drop=True)
    frame["consecutive_limit_up_count"] = (
        frame.groupby(["market", "symbol"], group_keys=False)["closed_limit_up"].apply(count_true_streak)
    )
    return frame[["symbol", "market", "trade_date", "consecutive_limit_up_count"]]


def count_true_streak(values: pd.Series) -> pd.Series:
    count = 0
    streaks = []
    for value in values.astype(bool):
        count = count + 1 if value else 0
        streaks.append(count)
    return pd.Series(streaks, index=values.index, dtype="int64")


def apply_live_filters(
    candidates: pd.DataFrame,
    config: SignalGeneratorConfig,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Apply strategy filters and return candidates plus first-failure reasons."""

    if candidates.empty:
        return candidates.copy(), pd.DataFrame(columns=["reason", "count"])
    frame = candidates.copy()
    reasons: list[str] = []
    for row in frame.to_dict("records"):
        reasons.append(first_skip_reason(row, config))
    frame["skip_reason"] = reasons
    filtered = frame[frame["skip_reason"].eq("")].copy()
    return filtered.reset_index(drop=True), count_reasons(reasons)


def first_skip_reason(row: dict[str, Any], config: SignalGeneratorConfig) -> str:
    if config.market.upper() != "BOTH" and str(row.get("market", "")).upper() != config.market.upper():
        return "wrong_market"
    if not bool(row.get("closed_limit_up", False)):
        return "not_closed_limit_up"
    if safe_float(row.get("day0_turnover_twd")) < config.min_turnover_twd:
        return "turnover_below_minimum"
    if safe_float(row.get("volume_ratio_20d")) < config.min_volume_ratio_20d:
        return "volume_ratio_below_minimum"
    if safe_float(row.get("consecutive_limit_up_count")) > config.max_consecutive_limit_ups:
        return "too_many_consecutive_limit_ups"
    close_price = safe_float(row.get("day0_close"))
    if close_price < config.min_price or close_price > config.max_price:
        return "price_outside_configured_range"
    if safe_float(row.get("fill_quality_score")) < config.min_fill_quality_score:
        return "fill_quality_below_minimum"
    if not bool(row.get("fillable_moderate", False)):
        return "failed_moderate_fill_proxy"
    if str(row.get("sector", "")).strip() in config.avoid_sectors:
        return "avoided_weak_sector"
    if config.allowed_sectors and str(row.get("sector", "")).strip() not in config.allowed_sectors:
        return "outside_sector_allowlist"
    if config.market_regime_filter != "none":
        if not bool(row.get("index_data_available", False)):
            return "missing_market_regime_data"
        if config.market_regime_filter == "not_bear" and bool(row.get("bear_regime", False)):
            return "bear_regime"
        if config.market_regime_filter == "avoid_weak_day" and bool(row.get("weak_market_day", False)):
            return "weak_market_day"
        if config.market_regime_filter == "bull_only" and not bool(row.get("bull_regime", False)):
            return "not_bull_regime"
    if config.prior_5d_return_max is not None and safe_float(row.get("prior_5d_return")) > config.prior_5d_return_max:
        return "prior_5d_return_above_cap"
    if config.prior_20d_return_max is not None and safe_float(row.get("prior_20d_return")) > config.prior_20d_return_max:
        return "prior_20d_return_above_cap"
    return ""


def build_planned_orders(
    candidates: pd.DataFrame,
    config: SignalGeneratorConfig,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Rank candidates, apply portfolio constraints, and build paper orders."""

    if candidates.empty:
        return empty_orders(), pd.DataFrame(columns=["reason", "count"])

    ranked = rank_candidates(candidates, config).reset_index(drop=True)
    rows: list[dict[str, Any]] = []
    skipped: Counter[str] = Counter()
    gross_exposure = 0.0
    sector_exposure: Counter[str] = Counter()
    industry_exposure: Counter[str] = Counter()
    for rank, row in enumerate(ranked.to_dict("records"), start=1):
        if len(rows) >= config.max_positions:
            skipped["max_positions"] += 1
            continue

        close_price = safe_float(row.get("day0_close"))
        symbol_cap = config.capital_twd * config.max_notional_per_symbol_pct
        remaining_capital = max(config.capital_twd - gross_exposure, 0.0)
        capped_notional = min(config.target_notional_twd, symbol_cap, remaining_capital)
        sector = str(row.get("sector", ""))
        industry = str(row.get("industry", ""))
        sector_room = max(config.capital_twd * config.max_notional_per_sector_pct - float(sector_exposure[sector]), 0.0)
        industry_room = max(
            config.capital_twd * config.max_notional_per_industry_pct - float(industry_exposure[industry]),
            0.0,
        )
        capped_notional = min(capped_notional, sector_room, industry_room)
        planned_shares = board_lot_shares(capped_notional, close_price, board_lot_size=config.board_lot_size)
        if planned_shares < config.board_lot_size:
            skipped["board_lot_or_capital"] += 1
            continue

        buy_notional = planned_shares * close_price
        estimated_buy_cost = estimate_buy_cost(
            buy_notional=buy_notional,
            config=config,
        )
        gross_exposure += buy_notional
        sector_exposure[sector] += buy_notional
        industry_exposure[industry] += buy_notional
        rows.append(
            {
                "profile_name": config.profile_name,
                "candidate_hash": config.candidate_hash,
                "signal_date": pd.Timestamp(row["signal_date"]).date().isoformat(),
                "symbol": str(row["symbol"]),
                "name": str(row.get("name", "")),
                "market": str(row.get("market", "")),
                "sector": str(row.get("sector", "")),
                "industry": str(row.get("industry", "")),
                "close_price": close_price,
                "limit_up_price": safe_float(row.get("limit_up_price")),
                "turnover_twd": safe_float(row.get("day0_turnover_twd")),
                "volume_ratio_20d": safe_float(row.get("volume_ratio_20d")),
                "consecutive_limit_up_count": int(safe_float(row.get("consecutive_limit_up_count"), 0)),
                "fill_quality_score": safe_float(row.get("fill_quality_score")),
                "same_sector_limitup_count": int(safe_float(row.get("same_sector_limitup_count"), 0)),
                "same_industry_limitup_count": int(safe_float(row.get("same_industry_limitup_count"), 0)),
                "market_limitup_count": int(safe_float(row.get("market_limitup_count"), 0)),
                "theme_breadth_score": safe_float(row.get("theme_breadth_score"), 0.0),
                "planned_entry_price": close_price,
                "planned_exit": PLANNED_EXIT,
                "target_notional_twd": config.target_notional_twd,
                "capped_notional_twd": capped_notional,
                "planned_shares": int(planned_shares),
                "planned_buy_notional_twd": buy_notional,
                "estimated_buy_cost": estimated_buy_cost,
                "estimated_cash_required": buy_notional + estimated_buy_cost,
                "ranking": rank,
                "notes": (
                    f"Ranked by {config.ranking_method}. "
                    f"Moderate fill proxy reason: {row.get('moderate_fill_reason', '')}"
                ),
                "execution_warning": EXECUTION_WARNING,
            }
        )
    orders = pd.DataFrame(rows)
    if orders.empty:
        orders = empty_orders()
    else:
        orders = orders[OUTPUT_COLUMNS]
    return orders, counter_to_frame(skipped)


def rank_candidates(candidates: pd.DataFrame, config: SignalGeneratorConfig) -> pd.DataFrame:
    """Sort candidates according to the profile ranking method."""

    frame = candidates.copy()
    if config.ranking_method == "day0_turnover_twd":
        sort_columns = ["day0_turnover_twd", "fill_quality_score", "volume_ratio_20d", "symbol"]
    elif config.ranking_method == "volume_ratio_20d":
        sort_columns = ["volume_ratio_20d", "fill_quality_score", "day0_turnover_twd", "symbol"]
    elif config.ranking_method == "composite_score":
        frame["turnover_rank_score"] = pd.to_numeric(frame["day0_turnover_twd"], errors="coerce").rank(pct=True)
        frame["volume_rank_score"] = pd.to_numeric(frame["volume_ratio_20d"], errors="coerce").rank(pct=True)
        frame["composite_score"] = (
            pd.to_numeric(frame["fill_quality_score"], errors="coerce").fillna(0.0)
            + 25.0 * frame["turnover_rank_score"].fillna(0.0)
            + 25.0 * frame["volume_rank_score"].fillna(0.0)
        )
        sort_columns = ["composite_score", "fill_quality_score", "day0_turnover_twd", "symbol"]
    elif config.ranking_method == "theme_breadth_score":
        sort_columns = [
            "theme_breadth_score",
            "same_industry_limitup_count",
            "same_sector_limitup_count",
            "fill_quality_score",
            "day0_turnover_twd",
            "symbol",
        ]
    else:
        sort_columns = ["fill_quality_score", "day0_turnover_twd", "volume_ratio_20d", "symbol"]
    ascending = [False] * (len(sort_columns) - 1) + [True]
    return frame.sort_values(sort_columns, ascending=ascending)


def summarize_skips(*skip_frames: pd.DataFrame) -> pd.DataFrame:
    counts: Counter[str] = Counter()
    for frame in skip_frames:
        if frame.empty:
            continue
        for row in frame.to_dict("records"):
            reason = str(row.get("reason", "")).strip()
            if reason:
                counts[reason] += int(row.get("count", 0))
    return counter_to_frame(counts)


def build_markdown_report(
    *,
    signal_date: pd.Timestamp,
    config: SignalGeneratorConfig,
    raw_candidates: pd.DataFrame,
    enriched: pd.DataFrame,
    filtered: pd.DataFrame,
    orders: pd.DataFrame,
    skipped_summary: pd.DataFrame,
) -> str:
    """Build a Markdown paper-order report."""

    lines = [
        f"# Closed-Limit-Up Paper Signals - {signal_date.date().isoformat()}",
        "",
        "## Execution Warning",
        "",
        EXECUTION_WARNING,
        "",
        "## Strategy Parameters",
        "",
        markdown_table(pd.DataFrame([asdict(config)])),
        "",
        "## Market Date",
        "",
        f"Signal date: `{signal_date.date().isoformat()}`",
        f"Strategy universe: `{config.market}`",
        f"Profile: `{config.profile_name}`",
        f"Candidate hash: `{config.candidate_hash}`",
        "",
        "## Candidate Counts",
        "",
        f"Raw closed-limit-up candidates: {len(raw_candidates):,}",
        f"Candidates after strategy filters: {len(filtered):,}",
        f"Planned paper orders: {len(orders):,}",
        "",
        "## Planned Paper Orders",
        "",
        markdown_table(orders),
        "",
        "## Skipped Reasons",
        "",
        markdown_table(skipped_summary),
        "",
        "## Candidate Diagnostics",
        "",
        markdown_table(
            enriched[
                [
                    "symbol",
                    "name",
                    "sector",
                    "industry",
                    "day0_close",
                    "day0_turnover_twd",
                    "volume_ratio_20d",
                    "consecutive_limit_up_count",
                    "fill_quality_score",
                    "same_sector_limitup_count",
                    "same_industry_limitup_count",
                    "market_limitup_count",
                    "theme_breadth_score",
                    "fillable_moderate",
                    "moderate_fill_reason",
                ]
            ].head(30)
            if not enriched.empty
            else enriched
        ),
        "",
        "## Execution Caveats",
        "",
        "- These are paper orders, not live trade instructions.",
        "- The strategy assumes a Day0 close entry, but closed limit-up stocks may have no executable sell liquidity.",
        "- Do not mark a paper order as filled unless broker/order-book or auction records support the fill.",
        "- The planned exit is the next trading day's open; no intraday stop or discretionary exit is modeled here.",
        *[f"- Profile warning: {warning}" for warning in config.warnings],
        "",
        "## Next-Day Evaluation Template",
        "",
        "When Day1 data becomes available, compare each paper order against:",
        "",
        "- actual Day1 open",
        "- theoretical exit price",
        "- gross return",
        "- net return after normal overnight Taiwan stock costs",
        "- whether paper fill was assumed",
        "- whether actual broker would have filled if known",
        "",
    ]
    return "\n".join(lines)


def build_all_profiles_markdown(
    *,
    results: dict[str, tuple[pd.DataFrame, pd.DataFrame, Path, Path]],
    combined: pd.DataFrame,
    date_label: str,
) -> str:
    """Build all-profile signal comparison Markdown."""

    rows = []
    symbols_by_profile: dict[str, set[str]] = {}
    for profile_name, (orders, skipped, csv_path, _md_path) in results.items():
        symbols = set(orders["symbol"].astype(str)) if not orders.empty else set()
        symbols_by_profile[profile_name] = symbols
        rows.append(
            {
                "profile_name": profile_name,
                "orders": len(orders),
                "symbols": ", ".join(sorted(symbols)) if symbols else "",
                "skipped_reasons": "; ".join(f"{r.reason}:{r.count}" for r in skipped.itertuples()) if not skipped.empty else "",
                "csv_path": str(csv_path),
            }
        )
    overlap_rows = []
    names = list(symbols_by_profile)
    for i, left in enumerate(names):
        for right in names[i + 1 :]:
            overlap = sorted(symbols_by_profile[left] & symbols_by_profile[right])
            overlap_rows.append({"left_profile": left, "right_profile": right, "overlap_count": len(overlap), "symbols": ", ".join(overlap)})
    return "\n".join(
        [
            f"# Closed-Limit-Up Signals - All Profiles - {date_label}",
            "",
            "## Profile Summary",
            "",
            markdown_table(pd.DataFrame(rows)),
            "",
            "## Overlap",
            "",
            markdown_table(pd.DataFrame(overlap_rows)),
            "",
            "## Combined Orders",
            "",
            markdown_table(combined),
            "",
            EXECUTION_WARNING,
            "",
        ]
    )


def count_reasons(reasons: list[str]) -> pd.DataFrame:
    counter = Counter(reason for reason in reasons if reason)
    return counter_to_frame(counter)


def counter_to_frame(counter: Counter[str]) -> pd.DataFrame:
    rows = [{"reason": reason, "count": count} for reason, count in counter.items()]
    if not rows:
        return pd.DataFrame(columns=["reason", "count"])
    return pd.DataFrame(rows).sort_values(["count", "reason"], ascending=[False, True]).reset_index(drop=True)


def estimate_buy_cost(*, buy_notional: float, config: SignalGeneratorConfig) -> float:
    commission = max(
        buy_notional * config.commission_rate * config.commission_discount,
        config.minimum_commission_twd,
    )
    entry_slippage = buy_notional * (config.slippage_bps_per_side / 10_000.0)
    return float(commission + entry_slippage)


def board_lot_shares(notional_twd: float, price: float, *, board_lot_size: int = BOARD_LOT_SIZE) -> int:
    if notional_twd <= 0 or price <= 0:
        return 0
    raw_shares = int(notional_twd // price)
    return int((raw_shares // board_lot_size) * board_lot_size)


def validate_config(config: SignalGeneratorConfig) -> None:
    if config.capital_twd <= 0:
        raise ValueError("capital_twd must be positive")
    if config.max_positions <= 0:
        raise ValueError("max_positions must be positive")
    if not 0 < config.max_notional_per_symbol_pct <= 1:
        raise ValueError("max_notional_per_symbol_pct must be in (0, 1]")


def safe_float(value: Any, default: float = np.nan) -> float:
    try:
        if pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def first_nonblank(frame: pd.DataFrame, columns: list[str]) -> pd.Series:
    result = pd.Series(pd.NA, index=frame.index, dtype="object")
    for column in columns:
        if column not in frame.columns:
            continue
        values = frame[column]
        mask = result.isna() | result.astype("string").fillna("").str.strip().eq("")
        result.loc[mask] = values.loc[mask]
    return result.astype("string").fillna("").str.strip()


def clean_group_values(values: pd.Series | None, missing_label: str) -> pd.Series:
    if values is None:
        return pd.Series(dtype="string")
    cleaned = values.astype("string").fillna("").str.strip()
    return cleaned.mask(cleaned == "", missing_label)


def empty_candidate_frame() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "event_id",
            "symbol",
            "signal_date",
            "market",
            "day0_close",
            "day0_turnover_twd",
            "volume_ratio_20d",
            "consecutive_limit_up_count",
            "fill_quality_score",
            "same_sector_limitup_count",
            "same_industry_limitup_count",
            "market_limitup_count",
            "theme_breadth_score",
            "sector",
            "industry",
            "fillable_moderate",
        ]
    )


def empty_orders() -> pd.DataFrame:
    return pd.DataFrame(columns=OUTPUT_COLUMNS)


def markdown_table(frame: pd.DataFrame, *, max_rows: int = 40) -> str:
    if frame.empty:
        return "_No rows._"
    view = frame.head(max_rows).copy().fillna("")
    for column in view.columns:
        if pd.api.types.is_float_dtype(view[column]):
            view[column] = view[column].map(format_float)
    headers = [str(column) for column in view.columns]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for _, row in view.iterrows():
        lines.append("| " + " | ".join(escape_markdown_cell(row[column]) for column in view.columns) + " |")
    return "\n".join(lines)


def format_float(value: Any) -> str:
    if pd.isna(value):
        return ""
    number = float(value)
    if abs(number) >= 1000:
        return f"{number:,.2f}"
    return f"{number:.4f}"


def escape_markdown_cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate daily closed-limit-up paper-trading signals")
    parser.add_argument("--db", type=Path, default=get_settings().db_path)
    parser.add_argument("--capital-twd", type=float, default=1_000_000.0)
    parser.add_argument("--signal-date", default="latest")
    parser.add_argument("--output-dir", type=Path, default=get_settings().project_root / "reports" / "live_signals")
    parser.add_argument(
        "--profile",
        default=ORIGINAL_CHAMPION,
        choices=[*ALL_PROFILE_NAMES, "all"],
    )
    parser.add_argument("--skip-refresh-events", action="store_true")
    args = parser.parse_args()

    if args.profile == "all":
        results, combined_csv, combined_md = generate_closed_limit_up_signals_for_profiles(
            db_path=args.db,
            capital_twd=args.capital_twd,
            signal_date=args.signal_date,
            output_dir=args.output_dir,
            profile=args.profile,
            refresh_events=not args.skip_refresh_events,
        )
        for profile_name, (orders, _skipped, csv_path, md_path) in results.items():
            print(f"Wrote {len(orders)} planned paper orders for {profile_name} to {csv_path}")
            print(f"Wrote Markdown report to {md_path}")
        if combined_csv is not None:
            print(f"Wrote all-profile comparison CSV to {combined_csv}")
        if combined_md is not None:
            print(f"Wrote all-profile comparison Markdown to {combined_md}")
        print(f"\n{EXECUTION_WARNING}")
        return

    orders, skipped, csv_path, md_path = generate_closed_limit_up_signals(
        db_path=args.db,
        capital_twd=args.capital_twd,
        signal_date=args.signal_date,
        output_dir=args.output_dir,
        profile=args.profile,
        refresh_events=not args.skip_refresh_events,
    )
    print(f"Wrote {len(orders)} planned paper orders to {csv_path}")
    print(f"Wrote Markdown report to {md_path}")
    print("\nPlanned paper orders")
    display_columns = [
        "signal_date",
        "symbol",
        "name",
        "sector",
        "close_price",
        "fill_quality_score",
        "planned_shares",
        "planned_buy_notional_twd",
    ]
    print(orders[display_columns].to_string(index=False) if not orders.empty else "No planned paper orders.")
    print("\nSkipped reasons")
    print(skipped.to_string(index=False) if not skipped.empty else "No skips.")
    print(f"\n{EXECUTION_WARNING}")


if __name__ == "__main__":
    main()
