"""Strategy profile registry for closed-limit-up paper trading."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

import pandas as pd

from config.settings import get_settings


ORIGINAL_CHAMPION = "original_champion_tpex_500m"
BROAD_CHALLENGER = "broad_challenger_097dd332"
CONSERVATIVE_TPEX = "conservative_tpex_35adc734"
EXPANDED_THEME_BREADTH = "expanded_theme_breadth_3eff3bfd"
ALL_PROFILE_NAMES = (ORIGINAL_CHAMPION, BROAD_CHALLENGER, CONSERVATIVE_TPEX, EXPANDED_THEME_BREADTH)


@dataclass(frozen=True)
class StrategyProfile:
    """Live paper-trading parameters for one strategy profile."""

    profile_name: str
    candidate_hash: str = ""
    market: str = "TPEX"
    event_type: str = "closed_limit_up"
    entry_rule: str = "day0_close"
    exit_rule: str = "day1_open"
    min_turnover_twd: float = 500_000_000.0
    min_volume_ratio_20d: float = 1.5
    fill_assumption: str = "moderate"
    min_fill_quality_score: float = 60.0
    min_price: float = 10.0
    max_price: float = 100.0
    max_consecutive_limit_ups: int = 3
    avoid_sectors: tuple[str, ...] = ("Healthcare", "Materials")
    allowed_sectors: tuple[str, ...] = ()
    weak_sector_handling: str = "avoid_healthcare_materials"
    market_regime_filter: str = "none"
    ranking_method: str = "fill_quality_score"
    momentum_cap: str = "none"
    prior_5d_return_max: float | None = None
    prior_20d_return_max: float | None = None
    max_positions: int = 5
    target_notional_twd: float = 300_000.0
    max_notional_per_symbol_pct: float = 0.20
    max_notional_per_sector_pct: float = 1.00
    max_notional_per_industry_pct: float = 1.00
    board_lot_size: int = 1000
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def markets(self) -> list[str]:
        market = self.market.upper().strip()
        if market == "BOTH":
            return ["TWSE", "TPEX"]
        return [market]


def load_strategy_profiles(*, report_dir: Path | str | None = None) -> dict[str, StrategyProfile]:
    """Return all strategy profiles, hydrating challenger rows from exact results when available."""

    reports = Path(report_dir) if report_dir is not None else get_settings().project_root / "reports"
    profiles = {
        ORIGINAL_CHAMPION: original_champion_profile(),
        BROAD_CHALLENGER: broad_challenger_profile(),
        CONSERVATIVE_TPEX: conservative_tpex_profile(),
        EXPANDED_THEME_BREADTH: expanded_theme_breadth_profile(),
    }
    focused_exact_path = reports / "focused_challenger_exact_results.csv"
    if focused_exact_path.exists():
        try:
            exact = pd.read_csv(focused_exact_path)
        except pd.errors.EmptyDataError:
            exact = pd.DataFrame()
        for name, profile in list(profiles.items()):
            if profile.candidate_hash:
                hydrated = hydrate_profile_from_exact_results(profile, exact)
                profiles[name] = hydrated
    expanded_exact_path = reports / "expanded_clu_tournament_exact.csv"
    if expanded_exact_path.exists():
        try:
            expanded = pd.read_csv(expanded_exact_path)
        except pd.errors.EmptyDataError:
            expanded = pd.DataFrame()
        for name, profile in list(profiles.items()):
            if profile.candidate_hash:
                hydrated = hydrate_profile_from_expanded_results(profile, expanded)
                profiles[name] = hydrated
    return profiles


def resolve_profile_selection(selection: str, *, report_dir: Path | str | None = None) -> list[StrategyProfile]:
    """Resolve CLI profile selection to concrete profile objects."""

    profiles = load_strategy_profiles(report_dir=report_dir)
    value = str(selection).strip()
    if value == "all":
        return [profiles[name] for name in ALL_PROFILE_NAMES]
    if value not in profiles:
        raise ValueError(f"Unknown profile {selection!r}. Expected one of: all, {', '.join(ALL_PROFILE_NAMES)}")
    return [profiles[value]]


def original_champion_profile() -> StrategyProfile:
    return StrategyProfile(
        profile_name=ORIGINAL_CHAMPION,
        market="TPEX",
        min_turnover_twd=500_000_000.0,
        min_volume_ratio_20d=1.5,
        market_regime_filter="none",
        ranking_method="fill_quality_score",
        weak_sector_handling="avoid_healthcare_materials",
        warnings=("Original conservative paper champion; execution is still unverified.",),
    )


def broad_challenger_profile() -> StrategyProfile:
    return StrategyProfile(
        profile_name=BROAD_CHALLENGER,
        candidate_hash="097dd332",
        market="BOTH",
        min_turnover_twd=200_000_000.0,
        min_volume_ratio_20d=1.5,
        market_regime_filter="not_bear",
        ranking_method="fill_quality_score",
        weak_sector_handling="avoid_healthcare_materials",
        warnings=(
            "Broad BOTH-market challenger; TWSE sector coverage may be incomplete.",
            "Paper only until Day0 close fills are verified.",
        ),
    )


def conservative_tpex_profile() -> StrategyProfile:
    return StrategyProfile(
        profile_name=CONSERVATIVE_TPEX,
        candidate_hash="35adc734",
        market="TPEX",
        min_turnover_twd=300_000_000.0,
        min_volume_ratio_20d=1.2,
        market_regime_filter="avoid_weak_day",
        ranking_method="fill_quality_score",
        momentum_cap="30_80",
        prior_5d_return_max=0.30,
        prior_20d_return_max=0.80,
        weak_sector_handling="avoid_healthcare_materials_semiconductor_cap_25",
        max_notional_per_industry_pct=0.25,
        warnings=(
            "Conservative TPEX finalist from focused expansion.",
            "Uses avoid-weak-day and prior momentum cap from exact audit.",
        ),
    )


def expanded_theme_breadth_profile() -> StrategyProfile:
    return StrategyProfile(
        profile_name=EXPANDED_THEME_BREADTH,
        candidate_hash="3eff3bfd",
        market="BOTH",
        min_turnover_twd=200_000_000.0,
        min_volume_ratio_20d=1.5,
        market_regime_filter="not_bear",
        ranking_method="theme_breadth_score",
        max_positions=8,
        max_notional_per_symbol_pct=0.20,
        max_notional_per_sector_pct=0.70,
        max_notional_per_industry_pct=0.35,
        weak_sector_handling="avoid_healthcare_materials",
        warnings=(
            "Expanded tournament replacement-style candidate 3eff3bfd.",
            "Ranks by same-day sector/industry limit-up breadth and allows up to 8 paper positions.",
            "Paper only until Day0 close fills are verified.",
        ),
    )


def hydrate_profile_from_exact_results(profile: StrategyProfile, exact: pd.DataFrame) -> StrategyProfile:
    """Overlay exact focused-expansion parameters for a candidate profile."""

    if exact.empty or "focused_config_hash" not in exact.columns:
        return profile
    matches = exact[exact["focused_config_hash"].astype(str).eq(profile.candidate_hash)]
    if matches.empty:
        return profile
    row = matches.iloc[0]
    momentum_cap = str(row.get("momentum_cap", profile.momentum_cap))
    prior_5d_max, prior_20d_max = momentum_bounds(momentum_cap)
    weak_sector_handling = str(row.get("weak_sector_handling", profile.weak_sector_handling))
    allowed_sectors: tuple[str, ...] = ()
    if weak_sector_handling == "technology_industrials_only":
        allowed_sectors = ("Technology/Electronics", "Industrials/Other")
    return replace(
        profile,
        market=str(row.get("market", profile.market)).upper(),
        min_turnover_twd=float(row.get("min_turnover_twd", profile.min_turnover_twd)),
        min_volume_ratio_20d=float(row.get("min_volume_ratio_20d", profile.min_volume_ratio_20d)),
        market_regime_filter=str(row.get("market_regime_filter", profile.market_regime_filter)),
        ranking_method=str(row.get("ranking_method", profile.ranking_method)),
        momentum_cap=momentum_cap,
        prior_5d_return_max=prior_5d_max,
        prior_20d_return_max=prior_20d_max,
        weak_sector_handling=weak_sector_handling,
        allowed_sectors=allowed_sectors,
        max_notional_per_industry_pct=0.25
        if weak_sector_handling == "avoid_healthcare_materials_semiconductor_cap_25"
        else profile.max_notional_per_industry_pct,
    )


def hydrate_profile_from_expanded_results(profile: StrategyProfile, exact: pd.DataFrame) -> StrategyProfile:
    """Overlay expanded closed-limit-up tournament parameters for a candidate profile."""

    if exact.empty or "expanded_config_hash" not in exact.columns:
        return profile
    matches = exact[exact["expanded_config_hash"].astype(str).eq(profile.candidate_hash)]
    if matches.empty:
        return profile
    row = matches.iloc[0]
    sector_filter = str(row.get("sector_filter", profile.weak_sector_handling))
    cap_semiconductor = truthy(row.get("cap_semiconductor", False))
    allowed_sectors: tuple[str, ...] = ()
    avoid_sectors = profile.avoid_sectors
    if sector_filter == "technology_electronics_only":
        allowed_sectors = ("Technology/Electronics",)
    elif sector_filter == "technology_industrials_only":
        allowed_sectors = ("Technology/Electronics", "Industrials/Other")
    elif sector_filter == "none":
        avoid_sectors = ()
    industry_cap = finite_float(row.get("max_notional_per_industry_pct"), profile.max_notional_per_industry_pct)
    if cap_semiconductor:
        industry_cap = min(industry_cap, 0.25)
    return replace(
        profile,
        market=str(row.get("market", profile.market)).upper(),
        min_turnover_twd=finite_float(row.get("min_turnover_twd"), profile.min_turnover_twd),
        min_volume_ratio_20d=finite_float(row.get("min_volume_ratio_20d"), profile.min_volume_ratio_20d),
        min_fill_quality_score=finite_float(row.get("min_fill_quality_score"), profile.min_fill_quality_score),
        min_price=finite_float(row.get("min_price"), profile.min_price),
        max_price=finite_float(row.get("max_price"), profile.max_price),
        max_consecutive_limit_ups=int(finite_float(row.get("max_consecutive_limit_ups"), profile.max_consecutive_limit_ups)),
        market_regime_filter=str(row.get("market_regime_filter", profile.market_regime_filter)),
        ranking_method=str(row.get("ranking_method", profile.ranking_method)),
        max_positions=int(finite_float(row.get("max_positions_per_day"), profile.max_positions)),
        max_notional_per_symbol_pct=finite_float(
            row.get("max_notional_per_symbol_pct"),
            profile.max_notional_per_symbol_pct,
        ),
        max_notional_per_sector_pct=finite_float(
            row.get("max_notional_per_sector_pct"),
            profile.max_notional_per_sector_pct,
        ),
        max_notional_per_industry_pct=industry_cap,
        weak_sector_handling=sector_filter,
        avoid_sectors=avoid_sectors,
        allowed_sectors=allowed_sectors,
    )


def momentum_bounds(momentum_cap: str) -> tuple[float | None, float | None]:
    mapping = {
        "none": (None, None),
        "30_80": (0.30, 0.80),
        "20_60": (0.20, 0.60),
        "10_40": (0.10, 0.40),
    }
    return mapping.get(str(momentum_cap), (None, None))


def finite_float(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if pd.isna(parsed):
        return default
    return parsed


def truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    if pd.isna(value):
        return False
    return bool(value)
