from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.backtests.event_study import build_event_candidates
from src.backtests.focused_challenger_expansion import (
    calculate_robust_score,
    focused_config_hash,
    iter_focused_grid,
    market_split_metrics,
    run_focused_challenger_expansion,
    select_finalist_configs,
)
from src.backtests.strategy_tournament import StrategyConfig, StrategyResult, combine_with_champion
from src.db import get_connection, init_db, upsert_dataframe


def test_focused_grid_generates_expected_configs() -> None:
    grid = iter_focused_grid()

    assert len(grid) == 14_400
    assert grid[0].market == "TPEX"
    assert grid[0].min_turnover_twd == 100_000_000.0
    assert grid[0].min_volume_ratio_20d == 1.2


def test_robust_score_penalizes_weak_configs() -> None:
    good = {
        "annualized_return": 1.0,
        "profit_factor": 3.0,
        "median_net_return_per_trade": 0.015,
        "max_drawdown_pct": -0.02,
        "positive_quarters": 10,
        "total_quarters": 10,
        "trades": 500,
        "top10_symbol_trade_share": 0.15,
        "sector_concentration": 0.60,
    }
    weak = {
        **good,
        "profit_factor": 1.2,
        "max_drawdown_pct": -0.15,
        "positive_quarters": 5,
        "trades": 20,
        "top10_symbol_trade_share": 0.70,
        "sector_concentration": 0.95,
    }

    assert calculate_robust_score(good) > calculate_robust_score(weak)


def test_combined_champion_calculation_works() -> None:
    champion = fake_strategy_result("champion", [0.01, -0.01, 0.02])
    challenger = fake_strategy_result("challenger", [-0.005, 0.02, 0.005])

    combined = combine_with_champion(champion, challenger)

    assert combined["strategy_name"] == "challenger"
    assert combined["combined_final_equity"] > 0
    assert "diversification_score" in combined


def test_twse_tpex_split_metrics_are_computed() -> None:
    trades = pd.DataFrame(
        {
            "market": ["TWSE", "TPEX", "TPEX"],
            "net_pnl": [100.0, 200.0, -50.0],
            "net_return": [0.01, 0.02, -0.005],
        }
    )

    metrics = market_split_metrics(trades)

    assert metrics["twse_trades"] == 1
    assert metrics["tpex_trades"] == 2
    assert metrics["twse_net_pnl"] == 100.0
    assert metrics["tpex_net_pnl"] == 150.0


def test_finalist_selection_deduplicates_configs() -> None:
    configs = iter_focused_grid(max_configs=4)
    duplicate_hash = focused_config_hash(configs[0])
    fast_results = pd.DataFrame(
        [
            {
                "focused_config_hash": duplicate_hash,
                "robust_score": 10.0,
                "total_net_pnl": 100.0,
                "median_net_return_per_trade": 0.01,
                "diversification_score": 1.0,
            },
            {
                "focused_config_hash": duplicate_hash,
                "robust_score": 9.0,
                "total_net_pnl": 90.0,
                "median_net_return_per_trade": 0.01,
                "diversification_score": 1.0,
            },
            {
                "focused_config_hash": focused_config_hash(configs[1]),
                "robust_score": 8.0,
                "total_net_pnl": 80.0,
                "median_net_return_per_trade": 0.01,
                "diversification_score": 1.0,
            },
        ]
    )

    finalists = select_finalist_configs(fast_results, configs=configs, finalists=2)

    assert len(finalists) == 2
    assert len({focused_config_hash(config) for config in finalists}) == 2


def test_known_synthetic_winner_survives_fast_prescreen() -> None:
    configs = iter_focused_grid(max_configs=3)
    winner = configs[2]
    fast_results = pd.DataFrame(
        [
            {
                "focused_config_hash": focused_config_hash(config),
                "robust_score": 1.0 if config is not winner else 10.0,
                "total_net_pnl": 1.0,
                "median_net_return_per_trade": 0.01,
                "diversification_score": 1.0,
            }
            for config in configs
        ]
    )

    finalists = select_finalist_configs(fast_results, configs=configs, finalists=1)

    assert finalists == [winner]


def test_fast_stage_returns_sensible_metrics(tmp_path: Path) -> None:
    db_path = build_focused_db(tmp_path)
    output_dir = tmp_path / "reports"

    result = run_focused_challenger_expansion(
        db_path=db_path,
        output_dir=output_dir,
        start="2024-01-01",
        end="2024-03-31",
        stage="fast",
        max_configs=2,
        show_progress=False,
    )

    assert len(result.fast_results) == 2
    assert result.exact_results.empty
    assert result.fast_results["is_approximate"].all()
    assert result.fast_results["trades"].max() >= 1
    assert (output_dir / "focused_challenger_fast_results.csv").exists()
    assert (output_dir / "focused_challenger_fast_top.csv").exists()


def test_focused_expansion_writes_report_files(tmp_path: Path) -> None:
    db_path = build_focused_db(tmp_path)
    output_dir = tmp_path / "reports"

    result = run_focused_challenger_expansion(
        db_path=db_path,
        output_dir=output_dir,
        start="2024-01-01",
        end="2024-03-31",
        max_configs=2,
        finalists=2,
        show_progress=False,
    )

    assert len(result.results) == 2
    assert len(result.fast_results) == 2
    assert len(result.exact_results) == 2
    assert result.report_path.exists()
    assert (output_dir / "focused_challenger_fast_results.csv").exists()
    assert (output_dir / "focused_challenger_fast_top.csv").exists()
    assert (output_dir / "focused_challenger_exact_results.csv").exists()
    assert (output_dir / "focused_challenger_exact_top.csv").exists()
    assert (output_dir / "focused_challenger_exact_combined.csv").exists()
    assert (output_dir / "focused_challenger_two_stage_report.md").exists()


def fake_strategy_result(name: str, returns: list[float]) -> StrategyResult:
    config = StrategyConfig(strategy_name=name, family="closed_limit_up_overnight", event_type="closed_limit_up")
    equity = 1_000_000.0
    rows = []
    for date, daily_return in zip(pd.bdate_range("2024-01-02", periods=len(returns)), returns):
        start = equity
        equity *= 1 + daily_return
        rows.append({"date": date, "start_equity": start, "end_equity": equity, "daily_return": daily_return})
    return StrategyResult(
        config=config,
        trades=pd.DataFrame(),
        daily_equity=pd.DataFrame(rows),
        metrics={
            "strategy_name": name,
            "annualized_return": 0.2,
            "max_drawdown_pct": -0.02,
            "total_net_pnl": equity - 1_000_000,
            "profit_factor": 2.0,
            "median_net_return_per_trade": 0.01,
        },
    )


def build_focused_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "taiwan_trading.duckdb"
    init_db(db_path)
    daily_prices = pd.DataFrame(
        [
            *symbol_rows("7001", "TPEX", "Alpha Tech", pd.Timestamp("2024-02-15")),
            *symbol_rows("1301", "TWSE", "Gamma Tech", pd.Timestamp("2024-02-16")),
        ]
    )
    sectors = pd.DataFrame(
        [
            {
                "symbol": "7001",
                "market": "TPEX",
                "name": "Alpha Tech",
                "sector": "Technology/Electronics",
                "industry": "Semiconductor",
                "source": "test",
            },
            {
                "symbol": "1301",
                "market": "TWSE",
                "name": "Gamma Tech",
                "sector": "Technology/Electronics",
                "industry": "Components",
                "source": "test",
            },
        ]
    )
    with get_connection(db_path) as conn:
        upsert_dataframe(conn, "daily_prices", daily_prices, ["symbol", "trade_date"])
        upsert_dataframe(conn, "stock_sector_map", sectors, ["symbol", "market"])
    build_event_candidates(db_path=db_path, start="2024-01-01", end="2024-03-31", markets=["TWSE", "TPEX"])
    return db_path


def symbol_rows(symbol: str, market: str, name: str, event_date: pd.Timestamp) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    previous_close = 50.0
    for date in pd.bdate_range(end=event_date - pd.Timedelta(days=1), periods=24):
        rows.append(price_row(symbol, market, name, date, close=previous_close, prev_close=previous_close, volume=100_000))
    event_close = previous_close * 1.10
    rows.append(
        price_row(
            symbol,
            market,
            name,
            event_date,
            open_price=previous_close * 1.04,
            high=event_close,
            low=previous_close * 1.02,
            close=event_close,
            prev_close=previous_close,
            volume=2_000_000,
            turnover=700_000_000,
            touched_limit_up=True,
            closed_limit_up=True,
            limit_up_price=event_close,
        )
    )
    day1 = pd.bdate_range(start=event_date + pd.Timedelta(days=1), periods=1)[0]
    rows.append(
        price_row(
            symbol,
            market,
            name,
            day1,
            open_price=event_close * 1.03,
            high=event_close * 1.04,
            low=event_close,
            close=event_close * 1.035,
            prev_close=event_close,
            volume=500_000,
        )
    )
    return rows


def price_row(
    symbol: str,
    market: str,
    name: str,
    trade_date: pd.Timestamp,
    *,
    close: float,
    prev_close: float,
    volume: int,
    turnover: float | None = None,
    open_price: float | None = None,
    high: float | None = None,
    low: float | None = None,
    touched_limit_up: bool = False,
    closed_limit_up: bool = False,
    limit_up_price: float | None = None,
) -> dict[str, object]:
    open_value = close if open_price is None else open_price
    high_value = close * 1.002 if high is None else high
    low_value = close * 0.998 if low is None else low
    return {
        "symbol": symbol,
        "trade_date": trade_date,
        "market": market,
        "name": name,
        "open": open_value,
        "high": high_value,
        "low": low_value,
        "close": close,
        "volume_shares": volume,
        "turnover_twd": turnover if turnover is not None else volume * close,
        "trades": 100,
        "prev_close": prev_close,
        "daily_return": close / prev_close - 1.0 if prev_close else 0.0,
        "limit_up_price": limit_up_price if limit_up_price is not None else prev_close * 1.10,
        "limit_down_price": prev_close * 0.90,
        "touched_limit_up": touched_limit_up,
        "touched_limit_down": False,
        "closed_limit_up": closed_limit_up,
        "closed_limit_down": False,
        "source": "test",
        "created_at": pd.Timestamp("2024-01-01"),
    }
