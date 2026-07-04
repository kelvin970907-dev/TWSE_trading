from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.backtests.strategy_tournament import (
    StrategyConfig,
    StrategyResult,
    build_portfolio_candidates_for_strategy,
    calculate_diversification_score,
    champion_config,
    combine_with_champion,
    daily_return_correlation,
    evaluate_strategy,
    load_tournament_universe,
    run_strategy_tournament,
)
from src.backtests.event_study import build_event_candidates
from src.db import get_connection, init_db, upsert_dataframe


def test_champion_config_runs_on_synthetic_universe(tmp_path: Path) -> None:
    db_path = build_tournament_db(tmp_path)
    universe = load_tournament_universe(db_path=db_path, start="2024-01-01", end="2024-03-31")

    result = evaluate_strategy(champion_config(), universe)

    assert result.metrics["trades"] >= 1
    assert result.metrics["total_net_pnl"] > 0
    assert result.metrics["profit_factor"] > 1


def test_challenger_configs_run_without_errors(tmp_path: Path) -> None:
    db_path = build_tournament_db(tmp_path)
    universe = load_tournament_universe(db_path=db_path, start="2024-01-01", end="2024-03-31")
    configs = [
        StrategyConfig(
            strategy_name="near_9_10_test",
            family="near_limit_9_10_overnight",
            event_type="near_limit_9_10",
            market="TPEX",
            min_turnover_twd=100_000_000,
            min_volume_ratio_20d=1.5,
            min_price=10,
            max_price=100,
            fill_assumption="moderate",
            min_fill_quality_score=40,
        ),
        StrategyConfig(
            strategy_name="day1_close_test",
            family="closed_limit_up_day1_open_to_close",
            event_type="closed_limit_up",
            market="TPEX",
            entry_exit_rule="day1_open_to_day1_close",
            min_turnover_twd=100_000_000,
            min_volume_ratio_20d=1.5,
            min_price=10,
            max_price=100,
            fill_assumption="moderate",
            min_fill_quality_score=40,
        ),
    ]

    results = [evaluate_strategy(config, universe) for config in configs]

    assert all("total_net_pnl" in result.metrics for result in results)
    assert all(result.trades is not None for result in results)


def test_strategy_filter_converts_day1_open_to_close_candidate(tmp_path: Path) -> None:
    db_path = build_tournament_db(tmp_path)
    universe = load_tournament_universe(db_path=db_path, start="2024-01-01", end="2024-03-31")
    config = StrategyConfig(
        strategy_name="day1_open_close_candidate",
        family="closed_limit_up_day1_open_to_close",
        event_type="closed_limit_up",
        market="TPEX",
        entry_exit_rule="day1_open_to_day1_close",
        min_turnover_twd=500_000_000,
        min_volume_ratio_20d=1.5,
        min_price=10,
        max_price=100,
        fill_assumption="moderate",
        min_fill_quality_score=60,
    )

    candidates = build_portfolio_candidates_for_strategy(universe, config)

    assert not candidates.empty
    assert candidates["signal_date"].iloc[0] == candidates["entry_date"].iloc[0]
    assert candidates["entry_price"].iloc[0] == pytest.approx(candidates["day1_open"].iloc[0])
    assert candidates["exit_price"].iloc[0] == pytest.approx(candidates["day1_close"].iloc[0])


def test_correlation_and_diversification_score() -> None:
    left = daily_equity(
        ["2024-01-02", "2024-01-03", "2024-01-04"],
        [0.01, -0.01, 0.02],
    )
    right = daily_equity(
        ["2024-01-02", "2024-01-03", "2024-01-04"],
        [-0.01, 0.01, -0.02],
    )

    corr = daily_return_correlation(left, right)
    score = calculate_diversification_score(
        correlation=corr,
        combined_cagr=0.30,
        champion_cagr=0.20,
        combined_max_drawdown_pct=-0.02,
        champion_max_drawdown_pct=-0.05,
    )

    assert corr < -0.9
    assert score > 1


def test_combined_portfolio_result_works() -> None:
    champion = fake_strategy_result(
        "champion",
        daily_returns=[0.01, -0.01, 0.02],
        net_pnl=1000,
        annualized=0.20,
        maxdd=-0.05,
    )
    challenger = fake_strategy_result(
        "challenger",
        daily_returns=[-0.005, 0.02, 0.0],
        net_pnl=800,
        annualized=0.15,
        maxdd=-0.02,
    )

    combined = combine_with_champion(champion, challenger)

    assert combined["strategy_name"] == "challenger"
    assert combined["combined_final_equity"] > 0
    assert "diversification_score" in combined


def test_run_tournament_writes_report_files(tmp_path: Path) -> None:
    db_path = build_tournament_db(tmp_path)
    output_dir = tmp_path / "reports"

    tournament = run_strategy_tournament(
        db_path=db_path,
        mode="compact",
        output_dir=output_dir,
        start="2024-01-01",
        end="2024-03-31",
        max_configs=3,
        show_progress=False,
    )

    assert not tournament.results.empty
    assert tournament.report_path.exists()
    assert (output_dir / "strategy_tournament_results.csv").exists()
    assert (output_dir / "strategy_tournament_top_challengers.csv").exists()
    assert (output_dir / "strategy_tournament_combined_portfolios.csv").exists()
    assert (output_dir / "strategy_tournament_report.md").exists()


def build_tournament_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "taiwan_trading.duckdb"
    init_db(db_path)
    daily_prices = synthetic_daily_prices()
    sector_map = pd.DataFrame(
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
                "symbol": "7002",
                "market": "TPEX",
                "name": "Beta Components",
                "sector": "Technology/Electronics",
                "industry": "Components",
                "source": "test",
            },
        ]
    )
    index_prices = synthetic_index_prices()
    with get_connection(db_path) as conn:
        upsert_dataframe(conn, "daily_prices", daily_prices, ["symbol", "trade_date"])
        upsert_dataframe(conn, "stock_sector_map", sector_map, ["symbol", "market"])
        upsert_dataframe(conn, "index_daily_prices", index_prices, ["index_symbol", "trade_date"])
    build_event_candidates(db_path=db_path, start="2024-01-01", end="2024-03-31", markets=["TPEX"])
    return db_path


def synthetic_daily_prices() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    rows.extend(symbol_history("7001", "Alpha Tech", pd.Timestamp("2024-02-15"), "closed_limit_up"))
    rows.extend(symbol_history("7002", "Beta Components", pd.Timestamp("2024-02-20"), "near_limit_9_10"))
    return pd.DataFrame(rows)


def symbol_history(symbol: str, name: str, event_date: pd.Timestamp, event_type: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    previous_close = 50.0
    history_dates = pd.bdate_range(end=event_date - pd.Timedelta(days=1), periods=24)
    for date in history_dates:
        rows.append(price_row(symbol, name, date, close=previous_close, prev_close=previous_close, volume=100_000))

    if event_type == "closed_limit_up":
        close = previous_close * 1.10
        rows.append(
            price_row(
                symbol,
                name,
                event_date,
                open_price=previous_close * 1.04,
                high=close,
                low=previous_close * 1.02,
                close=close,
                prev_close=previous_close,
                volume=2_000_000,
                turnover=700_000_000,
                touched_limit_up=True,
                closed_limit_up=True,
                limit_up_price=close,
            )
        )
        day1_open = close * 1.03
    else:
        close = previous_close * 1.095
        rows.append(
            price_row(
                symbol,
                name,
                event_date,
                open_price=previous_close * 1.02,
                high=close,
                low=previous_close * 1.01,
                close=close,
                prev_close=previous_close,
                volume=2_000_000,
                turnover=300_000_000,
                touched_limit_up=False,
                closed_limit_up=False,
                limit_up_price=previous_close * 1.10,
            )
        )
        day1_open = close * 1.01

    day1_date = pd.bdate_range(start=event_date + pd.Timedelta(days=1), periods=1)[0]
    rows.append(
        price_row(
            symbol,
            name,
            day1_date,
            open_price=day1_open,
            high=day1_open * 1.02,
            low=day1_open * 0.99,
            close=day1_open * 1.005,
            prev_close=close,
            volume=500_000,
            turnover=80_000_000,
        )
    )
    return rows


def price_row(
    symbol: str,
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
        "market": "TPEX",
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


def synthetic_index_prices() -> pd.DataFrame:
    rows = []
    dates = pd.bdate_range(start="2024-01-01", end="2024-03-31")
    for i, date in enumerate(dates):
        close = 17_000 + i * 10
        rows.append(
            {
                "index_symbol": "TAIEX",
                "trade_date": date,
                "open": close - 20,
                "high": close + 50,
                "low": close - 50,
                "close": close,
                "volume": 1_000_000,
                "turnover_twd": 100_000_000_000,
                "daily_return": 0.001,
                "ma5": close - 10,
                "ma20": close - 20,
                "ma60": close - 30,
                "close_above_ma20": True,
                "close_above_ma60": True,
                "drawdown_from_60d_high": -0.01,
                "source": "test",
            }
        )
    return pd.DataFrame(rows)


def daily_equity(dates: list[str], returns: list[float]) -> pd.DataFrame:
    equity = 1_000_000.0
    rows = []
    for date, daily_return in zip(dates, returns):
        start = equity
        equity *= 1 + daily_return
        rows.append(
            {
                "date": pd.Timestamp(date),
                "start_equity": start,
                "end_equity": equity,
                "daily_return": daily_return,
                "positions_entered": 1,
                "gross_exposure_end": 100_000.0,
                "gross_entry_notional": 100_000.0,
                "realized_pnl": equity - start,
            }
        )
    return pd.DataFrame(rows)


def fake_strategy_result(
    name: str,
    *,
    daily_returns: list[float],
    net_pnl: float,
    annualized: float,
    maxdd: float,
) -> StrategyResult:
    config = StrategyConfig(
        strategy_name=name,
        family="closed_limit_up_overnight",
        event_type="closed_limit_up",
    )
    daily = daily_equity(["2024-01-02", "2024-01-03", "2024-01-04"], daily_returns)
    trades = pd.DataFrame(
        {
            "symbol": ["7001"],
            "sector": ["Technology/Electronics"],
            "exit_date": [pd.Timestamp("2024-01-03")],
            "net_pnl": [net_pnl],
            "net_return": [0.01],
        }
    )
    metrics = {
        "strategy_name": name,
        "total_net_pnl": net_pnl,
        "annualized_return": annualized,
        "max_drawdown_pct": maxdd,
        "profit_factor": 2.0,
        "median_net_return_per_trade": 0.01,
    }
    return StrategyResult(config=config, trades=trades, daily_equity=daily, metrics=metrics)
