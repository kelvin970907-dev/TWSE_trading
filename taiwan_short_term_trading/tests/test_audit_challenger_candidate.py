from __future__ import annotations

import pandas as pd

from src.backtests.focused_challenger_expansion import FocusedGridConfig, focused_config_hash
from src.backtests.strategy_tournament import StrategyConfig, StrategyResult
from src.reports.audit_challenger_candidate import (
    build_comparison,
    build_sector_audit,
    candidate_decision,
    load_candidate_configs,
    prepare_audit_trades,
    summarize_market_split,
)


def test_load_candidate_configs_from_synthetic_exact_results() -> None:
    config = FocusedGridConfig(
        market="BOTH",
        min_turnover_twd=200_000_000.0,
        min_volume_ratio_20d=1.5,
        market_regime_filter="not_bear",
        ranking_method="fill_quality_score",
        momentum_cap="none",
        weak_sector_handling="avoid_healthcare_materials",
    )
    config_hash = focused_config_hash(config)
    exact = pd.DataFrame([{**config.payload(), "focused_config_hash": config_hash}])

    configs = load_candidate_configs(exact, [config_hash])

    assert configs[config_hash] == config


def test_prepare_trades_and_market_split_work_on_synthetic_trades() -> None:
    trades = synthetic_trades()

    prepared = prepare_audit_trades(trades, candidate_hash="abc123")
    split = summarize_market_split(prepared)

    assert "_diagnostic_row_id" in prepared.columns
    assert "trade_id" in prepared.columns
    assert set(split["market"]) == {"TPEX", "TWSE"}
    assert split["trades"].sum() == 4


def test_sector_audit_contains_stress_sections() -> None:
    sector = build_sector_audit(synthetic_trades())

    assert {"sector_summary", "industry_summary", "concentration", "stress_tests"}.issubset(set(sector["section"]))
    stress = sector[sector["section"].eq("stress_tests")]
    assert "remove_top_1_sector_by_pnl" in set(stress["scenario"])


def test_candidate_decision_promotes_strong_synthetic_candidate() -> None:
    summary = pd.DataFrame(
        [
            {"section": "basic_performance", "metric": "trades", "value": 600},
            {"section": "basic_performance", "metric": "final_equity", "value": 5_000_000},
            {"section": "basic_performance", "metric": "profit_factor", "value": 5.0},
            {"section": "basic_performance", "metric": "max_drawdown_pct", "value": -0.02},
        ]
    )
    fill = pd.DataFrame(
        [
            {
                "section": "stress_tests",
                "scenario": "remove_hard_and_possible_fill",
                "total_net_pnl": 1_000_000,
            }
        ]
    )
    sector = pd.DataFrame(
        [
            {
                "section": "stress_tests",
                "scenario": "remove_top_1_sector_by_pnl",
                "net_pnl": 500_000,
            }
        ]
    )
    symbol = pd.DataFrame()
    regime = pd.DataFrame([{"section": "summary"}])

    decision, risk = candidate_decision(summary=summary, fill=fill, sector=sector, symbol=symbol, regime=regime)

    assert decision == "promote to paper champion candidate"
    assert "execution" in risk.lower()


def test_comparison_uses_synthetic_strategy_results() -> None:
    champion = fake_result("champion", final_equity=2_000_000, max_drawdown_pct=-0.03)
    challenger = fake_result("challenger", final_equity=3_000_000, max_drawdown_pct=-0.02)
    audit = type(
        "Audit",
        (),
        {
            "candidate_hash": "abc123",
            "config": FocusedGridConfig(
                market="TPEX",
                min_turnover_twd=300_000_000.0,
                min_volume_ratio_20d=1.5,
                market_regime_filter="avoid_weak_day",
                ranking_method="fill_quality_score",
                momentum_cap="30_80",
                weak_sector_handling="avoid_healthcare_materials_semiconductor_cap_25",
            ),
            "result": challenger,
            "decision": "keep as research challenger",
            "biggest_unresolved_risk": "execution",
        },
    )()

    comparison = build_comparison([audit], champion=champion)

    assert comparison.iloc[0]["candidate_hash"] == "abc123"
    assert comparison.iloc[0]["final_equity_vs_champion"] == 1_000_000
    assert "combined_final_equity" in comparison.columns


def synthetic_trades() -> pd.DataFrame:
    return pd.DataFrame(
        [
            row("7001", "TPEX", "Technology/Electronics", "Semiconductor", "2024-01-02", 1000.0, 0.01),
            row("7002", "TPEX", "Technology/Electronics", "Components", "2024-01-03", 2000.0, 0.02),
            row("1301", "TWSE", "Industrials/Other", "Other", "2024-01-04", -500.0, -0.005),
            row("1302", "TWSE", "Healthcare", "Biotech", "2024-01-05", 100.0, 0.001),
        ]
    )


def row(
    symbol: str,
    market: str,
    sector: str,
    industry: str,
    date: str,
    net_pnl: float,
    net_return: float,
) -> dict[str, object]:
    return {
        "portfolio_trade_id": f"{symbol}-{date}",
        "event_id": f"{market}:{symbol}:{date}:closed_limit_up",
        "symbol": symbol,
        "name": symbol,
        "market": market,
        "sector": sector,
        "industry": industry,
        "signal_date": pd.Timestamp(date),
        "entry_date": pd.Timestamp(date),
        "exit_date": pd.Timestamp(date) + pd.Timedelta(days=1),
        "net_pnl": net_pnl,
        "net_return": net_return,
        "buy_notional": 100_000.0,
        "fill_quality_score": 70.0,
    }


def fake_result(name: str, *, final_equity: float, max_drawdown_pct: float) -> StrategyResult:
    daily = pd.DataFrame(
        [
            {"date": pd.Timestamp("2024-01-02"), "daily_return": 0.01, "start_equity": 1_000_000, "end_equity": 1_010_000},
            {"date": pd.Timestamp("2024-01-03"), "daily_return": -0.005, "start_equity": 1_010_000, "end_equity": final_equity},
        ]
    )
    return StrategyResult(
        config=StrategyConfig(strategy_name=name, family="closed_limit_up_overnight", event_type="closed_limit_up"),
        trades=pd.DataFrame(),
        daily_equity=daily,
        metrics={
            "final_equity": final_equity,
            "total_net_pnl": final_equity - 1_000_000,
            "annualized_return": 0.5,
            "profit_factor": 3.0,
            "max_drawdown_pct": max_drawdown_pct,
            "median_net_return_per_trade": 0.01,
            "trades": 10,
        },
    )
