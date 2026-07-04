"""Generate a simple event-study report from DuckDB daily prices."""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from src.backtests.metrics import summarize_returns
from src.backtests.strategy_limit_momentum import backtest_next_day_continuation
from src.db import read_sql


def load_daily_prices(db_path: Path | str | None = None) -> pd.DataFrame:
    return read_sql(
        """
        SELECT
            trade_date,
            symbol AS stock_id,
            market,
            name AS stock_name,
            open,
            high,
            low,
            close,
            volume_shares AS volume,
            turnover_twd AS turnover
        FROM daily_prices
        ORDER BY market, symbol, trade_date
        """,
        db_path=db_path,
    )


def generate_report(
    event_column: str,
    min_dollar_volume: float | None = None,
    db_path: Path | str | None = None,
) -> tuple[pd.DataFrame, dict[str, float]]:
    daily = load_daily_prices(db_path=db_path)
    trades = backtest_next_day_continuation(
        daily,
        event_column=event_column,
        min_dollar_volume=min_dollar_volume,
    )
    summary = summarize_returns(trades["net_return"]) if not trades.empty else summarize_returns([])
    return trades, summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a next-day continuation event report")
    parser.add_argument("--db", type=Path, help="Optional DuckDB database path")
    parser.add_argument("--event-column", default="is_plus_8_to_9_not_limit")
    parser.add_argument("--min-dollar-volume", type=float)
    parser.add_argument("--output", type=Path, default=Path("data/processed/event_report.csv"))
    args = parser.parse_args()

    trades, summary = generate_report(args.event_column, args.min_dollar_volume, db_path=args.db)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    trades.to_csv(args.output, index=False)

    print(f"Wrote {len(trades)} trades to {args.output}")
    for key, value in summary.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
