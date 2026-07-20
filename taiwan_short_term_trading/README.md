# Taiwan Short-Term Trading Research

Research and backtest short-term Taiwan equity strategies focused on strong one-day moves, limit-up behavior, and next-trading-day continuation after the open.

## Research Questions

- Do stocks that rise +8% to +9% without touching the +10% daily price limit continue upward the next trading day?
- Do stocks that touch or close at limit-up continue after the next open?
- Are any continuation effects large enough after Taiwan brokerage fees, transaction tax, slippage, liquidity filters, and realistic execution assumptions?

## Project Layout

```text
taiwan_short_term_trading/
  config/                  Runtime settings and paths
  data/                    Local data only; DuckDB/raw/processed files are gitignored
  notebooks/               Exploratory notebooks
  src/                     Core Python modules
    data_collectors/       TWSE/TPEX and CSV ingestion entry points
    features/              Daily, limit, and intraday feature builders
    backtests/             Event studies, strategy backtests, costs, and metrics
    reports/               Report generation scripts
  tests/                   Pytest coverage for core calculations
```

## Setup

Use Python 3.11 or newer.

```bash
cd taiwan_short_term_trading
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Initialize the local DuckDB database:

```bash
python -m src.db --init
```

The default database path is `data/taiwan_trading.duckdb`. Database files and large data extracts are ignored by git.

## Basic Workflow

1. Collect or import daily price data into `daily_prices`.
2. Build daily and limit-related features.
3. Select event cohorts such as:
   - `is_plus_8_to_9_not_limit`
   - `high_touched_limit_up`
   - `close_at_limit_up`
4. Backtest next-day open-to-close or open-to-intraday exits.
5. Apply Taiwan costs and liquidity filters.
6. Compare net returns, hit rate, drawdown, turnover, and event stability by period.

## Example Commands

Collect TWSE daily market data for a date range:

```bash
python -m src.data_collectors.collect_daily_prices --start 2023-01-01 --end 2026-06-22 --market TWSE --db data/taiwan_trading.duckdb
```

Collect TPEx/OTC daily market data:

```bash
python -m src.data_collectors.collect_daily_prices --start 2023-01-01 --end 2026-06-22 --market TPEX --db data/taiwan_trading.duckdb
```

Collect both markets:

```bash
python -m src.data_collectors.collect_daily_prices --start 2023-01-01 --end 2026-06-22 --market BOTH --db data/taiwan_trading.duckdb
```

Raw responses are cached under `data/raw/twse_daily/` and `data/raw/tpex_daily/`, so interrupted runs can be resumed without refetching dates that already have cache files. After each collection run, the CLI prints row counts by market, date range by market, and the top 10 symbols by number of rows.

The TPEx public daily quote payload includes non-stock instruments in the same table. The TPEx parser currently stores standard four-digit common stock symbols by default.

Import vendor/exported 1-minute intraday bars from one CSV:

```bash
python -m src.data_collectors.collect_intraday_prices import-csv \
  --db data/taiwan_trading.duckdb \
  --csv path/to/intraday.csv
```

Import a folder of intraday CSV files:

```bash
python -m src.data_collectors.collect_intraday_prices import-folder \
  --db data/taiwan_trading.duckdb \
  --folder data/raw/intraday_vendor/
```

Intraday CSV imports expect columns `symbol`, `market`, `bar_time`, `open`, `high`, `low`, `close`, `volume_shares`, and `turnover_twd`. The importer derives `trade_date` from `bar_time`, validates duplicate `symbol/bar_time` keys, rejects non-positive prices and negative volume/turnover, checks default Taiwan regular-session times of `09:00` to `13:30`, stores rows in `intraday_bars`, and prints coverage by symbol. The `public-download` mode is present as an adapter point, but no broad official historical 1-minute TWSE/TPEx source is configured yet.

Import market regime and sector context:

```bash
python -m src.data_collectors.collect_market_context collect-taiex-public \
  --db data/taiwan_trading.duckdb \
  --start 2023-01-01 \
  --end 2026-06-24

python -m src.data_collectors.collect_market_context import-index-csv \
  --db data/taiwan_trading.duckdb \
  --csv path/to/taiex_daily.csv \
  --index-symbol TAIEX

python -m src.data_collectors.collect_market_context import-sector-map-csv \
  --db data/taiwan_trading.duckdb \
  --csv path/to/stock_sector_map.csv

python -m src.data_collectors.collect_market_context import-sector-map \
  --db data/taiwan_trading.duckdb \
  --csv path/to/sector_map.csv

python -m src.data_collectors.collect_market_context collect-sector-map-public \
  --db data/taiwan_trading.duckdb \
  --market TWSE

python -m src.data_collectors.collect_market_context build-sector-features \
  --db data/taiwan_trading.duckdb
```

The public TAIEX collector uses TWSE's monthly `MI_5MINS_HIST` endpoint and caches raw JSON under `data/raw/twse_taiex_index/`. If the endpoint is unavailable or you want a vendor-cleaned history, index CSV imports expect `trade_date`, `open`, `high`, `low`, and `close`, with optional `index_symbol`, `volume`, `turnover_twd`, and `source`; the importer computes `daily_return`, `ma5`, `ma20`, `ma60`, `close_above_ma20`, `close_above_ma60`, and `drawdown_from_60d_high`. Sector map CSV imports expect `symbol`, `market`, `name`, `sector`, and `industry`; `name` and `industry` can be blank, and public-source-style `industry_code` can be used to derive a broad sector. The public sector-map collector supports TWSE OpenAPI company metadata directly and attempts TPEx official endpoint candidates, but TPEx mapping should be treated as CSV-first if the endpoint changes. Sector daily features are built from `daily_prices` plus `stock_sector_map`.

Import institutional investor flows and margin/short data:

```bash
python -m src.data_collectors.collect_institutional_flows \
  --db data/taiwan_trading.duckdb \
  --csv path/to/institutional_flows.csv

python -m src.data_collectors.collect_margin_short \
  --db data/taiwan_trading.duckdb \
  --csv path/to/margin_short.csv
```

Institutional flow CSV imports expect `symbol`, `trade_date`, `market`, `foreign_net_buy_twd`, `investment_trust_net_buy_twd`, and `dealer_net_buy_twd`, with optional `total_institutional_net_buy_twd` and `source`. Margin/short CSV imports expect `symbol`, `trade_date`, `market`, `margin_buy_balance`, and `short_sale_balance`, with optional `margin_sell_balance`, `day_trade_volume`, and `source`. Event generation joins these tables when available and stores normalized flow ratios, 1-day margin/short balance changes, `short_squeeze_proxy`, and `margin_crowding_proxy` in `event_candidates`.

Build Day 0 event candidates:

```bash
python -m src.backtests.event_study build-events --db data/taiwan_trading.duckdb --start 2023-01-01 --end 2026-06-22
```

This writes to `event_candidates` and prints event counts, average Day 0 returns, median turnover, and the top 20 most frequent symbols.

Run the first daily event-study report:

```bash
python -m src.backtests.event_study run-daily-study --db data/taiwan_trading.duckdb --start 2023-01-01 --end 2026-06-22
```

This writes `reports/event_study_daily.csv`, `reports/event_study_summary.csv`, and `reports/feature_performance_by_bucket.csv`. The summary includes gross next-day behavior, approximate after-cost returns using default day-trade cost assumptions, one-sided p-value columns for whether average returns are positive, and institutional/margin feature bucket performance.

Run the closed-limit-up overnight gap-capture study:

```bash
python -m src.backtests.limit_up_gap_capture \
  --db data/taiwan_trading.duckdb \
  --start 2023-01-01 \
  --end 2026-06-24 \
  --market BOTH \
  --min-turnover-twd 100000000 \
  --min-volume-ratio-20d 2.0
```

This studies only `closed_limit_up` events and writes `reports/limit_up_gap_capture_event_study.csv` and `reports/limit_up_gap_capture_summary.csv`. It compares gross and net Day 0 close-to-Day 1 open returns using normal overnight stock sale tax, plus Day 1 open-to-close returns using day-trade tax assumptions. Filters include market, turnover, volume shock, close location, Day 0 price range, consecutive limit-up count, first-limit-up-only, and whether Day -1 was positive.

Run the actual closed-limit-up overnight strategy backtest:

```bash
python -m src.backtests.closed_limit_up_overnight \
  --db data/taiwan_trading.duckdb \
  --start 2023-01-01 \
  --end 2026-06-24 \
  --market BOTH \
  --fixed-notional-twd 100000 \
  --min-turnover-twd 100000000 \
  --min-volume-ratio-20d 2.0 \
  --max-consecutive-limit-ups 1 \
  --only-first-limit-up true
```

This buys selected `closed_limit_up` events at Day 0 close, sells at Day 1 open, rounds position size down to 1,000-share board lots, skips names that cannot buy one board lot, applies overnight costs with normal stock transaction tax of `0.30%`, writes `reports/closed_limit_up_overnight_trades.csv` and `reports/closed_limit_up_overnight_summary.csv`, and stores selected trades in `backtest_trades` under strategy name `closed_limit_up_overnight`.

Audit closed-limit-up fill realism with daily-data proxies:

```bash
python -m src.backtests.closed_limit_up_fill_audit \
  --db data/taiwan_trading.duckdb \
  --start 2023-01-01 \
  --end 2026-06-24 \
  --market BOTH \
  --fixed-notional-twd 100000 \
  --min-turnover-twd 100000000 \
  --min-volume-ratio-20d 2.0 \
  --only-first-limit-up true \
  --skip-build-events
```

This reruns the closed-limit-up overnight candidate set under `optimistic`, `moderate`, and `conservative` fill assumptions, then applies fill-quality score thresholds of `40`, `50`, `60`, `70`, and `80`. It writes `reports/closed_limit_up_fill_audit_events.csv`, `reports/closed_limit_up_fill_audit_summary.csv`, and `reports/closed_limit_up_fill_assumption_comparison.csv`. The audit uses daily proxies such as Day 0 turnover, volume shock, high-low range, close location, no-trade lock patterns, consecutive limit-up count, and price level. It is not a substitute for auction/order-book fill data.

Run walk-forward validation for the closed-limit-up overnight strategy:

```bash
python -m src.backtests.walk_forward_closed_limit_up_overnight \
  --db data/taiwan_trading.duckdb \
  --start 2023-01-01 \
  --end 2026-06-24
```

This ranks closed-limit-up overnight configurations on each expanding train window, selects the top 5 by a robustness objective, and tests those configs on the next out-of-sample quarter. It writes `reports/walk_forward_closed_limit_up_overnight_results.csv`, `reports/walk_forward_closed_limit_up_overnight_selected_configs.csv`, `reports/walk_forward_closed_limit_up_overnight_oos_trades.csv`, and `reports/walk_forward_closed_limit_up_overnight_summary.csv`.

Simulate live-style portfolio allocation for the best closed-limit-up overnight OOS trades:

```bash
python -m src.backtests.portfolio_sim_closed_limit_up \
  --db data/taiwan_trading.duckdb \
  --oos-trades reports/walk_forward_closed_limit_up_overnight_oos_trades.csv \
  --output-dir reports
```

This filters to the best robust TPEX/moderate-fill strategy family, joins sector and TAIEX context when available, then simulates limited capital, same-day signal ranking, 1,000-share board-lot rounding, one-night cash updates, per-symbol/sector/industry caps, monthly symbol caps, optional not-bear regime filtering, and optional weak-sector avoidance. The default run also evaluates the compact portfolio grid and writes `reports/closed_limit_up_portfolio_trades.csv`, `reports/closed_limit_up_portfolio_daily_equity.csv`, `reports/closed_limit_up_portfolio_grid.csv`, `reports/closed_limit_up_portfolio_monthly_returns.csv`, and `reports/closed_limit_up_portfolio_report.md`.

Run the strategy tournament against the current champion:

```bash
python -m src.backtests.strategy_tournament \
  --db data/taiwan_trading.duckdb \
  --mode compact \
  --output-dir reports

python -m src.backtests.strategy_tournament \
  --db data/taiwan_trading.duckdb \
  --mode full \
  --output-dir reports
```

The tournament keeps `champion_tpex_closed_limit_up_overnight` as the benchmark and evaluates daily-OHLCV challenger families under the same portfolio assumptions. It reports outright challenger performance, daily-return correlation to the champion, and a simple 50/50 champion-plus-challenger blend so diversifying strategies can be useful even if they do not beat the champion alone. It writes `reports/strategy_tournament_results.csv`, `reports/strategy_tournament_top_challengers.csv`, `reports/strategy_tournament_combined_portfolios.csv`, and `reports/strategy_tournament_report.md`.

Generate the daily after-close paper-trading signal sheet:

```bash
python -m src.live.generate_closed_limit_up_signals \
  --db data/taiwan_trading.duckdb \
  --capital-twd 1000000 \
  --signal-date latest \
  --output-dir reports/live_signals
```

This refreshes `event_candidates` for the signal date, filters to the best TPEX closed-limit-up overnight setup, ranks by fill-quality score, applies the 20% per-symbol capital cap and 1,000-share board lots, then writes `reports/live_signals/closed_limit_up_signals_YYYY-MM-DD.csv` and `.md`. The output is explicitly paper-trade only until actual Day 0 close auction/order-book fills are verified.

Evaluate a generated paper signal sheet after Day 1 daily data is available:

```bash
python -m src.live.evaluate_closed_limit_up_signals \
  --db data/taiwan_trading.duckdb \
  --signals-csv reports/live_signals/closed_limit_up_signals_YYYY-MM-DD.csv \
  --output-dir reports/live_signals
```

This finds the next available `daily_prices` row for each symbol, compares the planned Day 0 close entry with the theoretical Day 1 open exit, applies normal overnight Taiwan stock costs, writes `reports/live_signals/closed_limit_up_eval_YYYY-MM-DD.csv` and `.md`, and appends de-duplicated rows to `reports/live_signals/closed_limit_up_paper_ledger.csv`.

Run the full daily closed-limit-up paper-trading pipeline:

```bash
python -m src.live.run_daily_closed_limit_up_pipeline \
  --db data/taiwan_trading.duckdb \
  --capital-twd 1000000 \
  --market BOTH \
  --output-dir reports/live_signals
```

The pipeline can update daily prices, TAIEX, and the TPEx sector map, rebuild latest event candidates, generate the latest paper signal sheet, evaluate older unevaluated signal sheets, update the paper ledger, and write `reports/live_signals/daily_pipeline_report_YYYY-MM-DD.md`. Use `--skip-data-update`, `--skip-index-update`, `--skip-sector-update`, and `--dry-run` when you want a local report without public endpoint calls.

## macOS Scheduled Paper Pipeline

The project includes a local `launchd` setup for running the closed-limit-up paper-trading pipeline automatically after Taiwan market data is likely available. This remains paper-trading only; no script in this workflow submits real broker orders.

Manual test run:

```bash
cd /Users/kelvinhsueh/Documents/taiwan_trading/taiwan_short_term_trading
bash scripts/run_daily_paper_pipeline.sh
```

The runner script:

- changes into `/Users/kelvinhsueh/Documents/taiwan_trading/taiwan_short_term_trading`
- activates `.venv` or `venv` if present
- fails if the project directory or `data/taiwan_trading.duckdb` is missing
- sets safe runtime environment variables for Python and paper-only execution
- writes the daily run log to `logs/daily_pipeline_YYYY-MM-DD.log`
- appends failures to `logs/daily_pipeline_errors.log`
- prints the latest daily pipeline report path after a successful run

The daily Python pipeline refreshes TAIEX after the daily price update. If TAIEX is still older than the signal date, it waits 30 seconds, force-refreshes the TAIEX monthly cache once, and checks again. Profiles that require market-regime data, such as `broad_challenger_097dd332`, are skipped when TAIEX remains stale; non-regime profiles can still generate paper signals. The daily report lists the TAIEX freshness status, retry status, and any skipped stale-regime profiles.

Install the weekday 03:30 America/Chicago LaunchAgent:

```bash
cd /Users/kelvinhsueh/Documents/taiwan_trading/taiwan_short_term_trading
bash scripts/install_launchd_pipeline.sh
```

Install the 03:30 run plus the optional 04:30 retry LaunchAgent:

```bash
cd /Users/kelvinhsueh/Documents/taiwan_trading/taiwan_short_term_trading
bash scripts/install_launchd_pipeline.sh --with-retry
```

Uninstall both agents:

```bash
cd /Users/kelvinhsueh/Documents/taiwan_trading/taiwan_short_term_trading
bash scripts/uninstall_launchd_pipeline.sh
```

Check whether launchd loaded the agents:

```bash
launchctl list | grep taiwan-paper-pipeline
```

View logs:

```bash
tail -n 200 logs/daily_pipeline_$(TZ=America/Chicago date +%F).log
tail -n 200 logs/daily_pipeline_errors.log
tail -n 200 logs/launchd_stdout.log
tail -n 200 logs/launchd_stderr.log
```

The main plist is `scripts/com.kelvin.taiwan-paper-pipeline.plist`; the optional retry plist is `scripts/com.kelvin.taiwan-paper-pipeline-retry.plist`. `launchd` schedules by the Mac's system timezone. If the Mac is set to America/Chicago, the main run fires at 03:30 on weekdays, which is 16:30 Taiwan time during Chicago daylight time and 17:30 Taiwan time during Chicago standard time. The optional 04:30 retry is 17:30 or 18:30 Taiwan time depending on daylight saving time.

## Databricks Mode

The codebase can run in local mode or Databricks mode without manual path edits. Runtime paths are controlled by environment variables:

- `TAIWAN_TRADING_ROOT`: root for data, reports, logs, and default database path
- `TAIWAN_TRADING_CODE_ROOT`: optional project code root when Databricks does not expose `__file__`
- `TAIWAN_TRADING_DB_PATH`: optional explicit DuckDB path
- `TAIWAN_TRADING_DATA_DIR`: optional explicit data directory
- `TAIWAN_TRADING_RAW_DATA_DIR`: optional explicit raw-cache directory
- `TAIWAN_TRADING_PROCESSED_DATA_DIR`: optional explicit processed-data directory

Local mode defaults to the checked-out project directory. Databricks mode should set `TAIWAN_TRADING_ROOT` to a persistent location such as `/dbfs/FileStore/taiwan_trading` or a Unity Catalog Volume path, for example `/Volumes/<catalog>/<schema>/taiwan_trading`.

Install cluster libraries from the lightweight Databricks requirements file:

```bash
pip install -r requirements-databricks.txt
```

For a Databricks Git-based workflow:

1. Put this repository in Databricks Repos or sync it from Git.
2. Upload or create the DuckDB file at `${TAIWAN_TRADING_ROOT}/data/taiwan_trading.duckdb`.
3. Configure the job with environment variable `TAIWAN_TRADING_ROOT`.
4. Run the Python script task from the repo checkout.

Example Databricks job command:

```bash
python scripts/run_databricks_daily_pipeline.py \
  --code-root /Workspace/Users/kelvin970907@gmail.com/TWSE_trading/taiwan_short_term_trading \
  --root /dbfs/FileStore/taiwan_trading \
  --capital-twd 1000000 \
  --profile all \
  --market BOTH
```

Using a Unity Catalog Volume:

```bash
python scripts/run_databricks_daily_pipeline.py \
  --code-root /Workspace/Users/kelvin970907@gmail.com/TWSE_trading/taiwan_short_term_trading \
  --root /Volumes/<catalog>/<schema>/taiwan_trading \
  --capital-twd 1000000 \
  --profile all \
  --market BOTH
```

The Databricks runner treats `--root` as persistent storage and uses local scratch disk for active DuckDB writes. By default on Databricks it copies `${TAIWAN_TRADING_ROOT}/data/taiwan_trading.duckdb` to `/local_disk0/taiwan_trading_work/data/taiwan_trading.duckdb`, runs the pipeline with scratch DB and scratch report paths, then copies the updated DuckDB file and generated reports/logs back to the persistent root after a successful run. Override scratch location with:

```bash
python scripts/run_databricks_daily_pipeline.py \
  --code-root /Workspace/Users/kelvin970907@gmail.com/TWSE_trading/taiwan_short_term_trading \
  --root /Volumes/<catalog>/<schema>/taiwan_trading \
  --scratch-root /local_disk0/taiwan_trading_work \
  --capital-twd 1000000 \
  --profile all \
  --market BOTH
```

On failure, the persistent DuckDB is not overwritten; scratch logs and error logs are copied back when possible.

The Databricks runner writes:

- logs to `${TAIWAN_TRADING_ROOT}/logs/databricks_daily_pipeline_YYYY-MM-DD.log`
- errors to `${TAIWAN_TRADING_ROOT}/logs/databricks_daily_pipeline_errors.log`
- signal reports to `${TAIWAN_TRADING_ROOT}/reports/live_signals/`
- lightweight strategy-analysis refreshes to `${TAIWAN_TRADING_ROOT}/reports/strategy_analysis/`
- raw endpoint caches to `${TAIWAN_TRADING_ROOT}/data/raw/`

Schedule the Databricks Job for weekdays after Taiwan market data is likely available. The local launchd schedule is 03:30 America/Chicago, which corresponds to 16:30 Taiwan time during Chicago daylight time and 17:30 Taiwan time during Chicago standard time. Use the same timing for a Databricks Job unless public source publication delays require a later retry.

The daily Databricks job refreshes only forward paper-trading analysis because it is cheap and does not rerun historical portfolio reconstruction. It updates:

- `${TAIWAN_TRADING_ROOT}/reports/strategy_analysis/paper_equity_curves_by_profile.png`
- `${TAIWAN_TRADING_ROOT}/reports/strategy_analysis/paper_drawdown_curves_by_profile.png`
- `${TAIWAN_TRADING_ROOT}/reports/strategy_analysis/paper_profile_summary.csv`

Run the expensive historical strategy equity analysis manually, or as a separate weekly Sunday Databricks job:

```bash
python scripts/run_databricks_daily_pipeline.py \
  --code-root /Workspace/Users/kelvin970907@gmail.com/TWSE_trading/taiwan_short_term_trading \
  --root /Volumes/work/taiwan_trading/trading_files/taiwan_trading \
  --capital-twd 1000000 \
  --profile all \
  --market BOTH \
  --refresh-strategy-analysis
```

If you want one reusable Databricks task that only refreshes historical charts on Sundays, use:

```bash
python scripts/run_databricks_daily_pipeline.py \
  --code-root /Workspace/Users/kelvin970907@gmail.com/TWSE_trading/taiwan_short_term_trading \
  --root /Volumes/work/taiwan_trading/trading_files/taiwan_trading \
  --capital-twd 1000000 \
  --profile all \
  --market BOTH \
  --weekly-strategy-analysis
```

Historical analysis outputs are written under `${TAIWAN_TRADING_ROOT}/reports/strategy_analysis/`, including `strategy_equity_analysis.md`, `strategy_equity_daily.csv`, `strategy_drawdown_daily.csv`, `strategy_monthly_returns.csv`, `strategy_performance_summary.csv`, historical equity/drawdown/rolling-return PNGs, and the latest forward-paper equity chart. Daily pipeline reports include the current paths to the latest paper equity chart, historical normalized equity chart, historical drawdown chart, and strategy performance summary.

Safeguards:

- the Databricks runner sets `TAIWAN_PAPER_TRADING_ONLY=1`
- it fails clearly if the DuckDB file is missing
- it prints the latest report path after success
- no workflow in this repository submits real broker orders

Log manual broker/order-book fill observations:

```bash
python -m src.live.manual_fill_log export-template \
  --output reports/live_signals/manual_fill_observations_template.csv

python -m src.live.manual_fill_log import-csv \
  --db data/taiwan_trading.duckdb \
  --csv reports/live_signals/manual_fill_observations.csv

python -m src.live.manual_fill_log summarize \
  --db data/taiwan_trading.duckdb \
  --output-dir reports/live_signals
```

The manual log stores broker/order-book evidence for whether Day 0 limit-up close entries were actually submitted and filled. It writes `reports/live_signals/manual_fill_summary.csv` and `.md`, reports fill rates by symbol/sector/industry/limit-lock state, compares paper theoretical PnL with actual fill-adjusted PnL when linked to the paper ledger, and lists generated signals that still have no manual observation.

Diagnose the best closed-limit-up overnight OOS trades by TAIEX market regime:

```bash
python -m src.reports.diagnose_market_regime \
  --db data/taiwan_trading.duckdb \
  --oos-trades reports/walk_forward_closed_limit_up_overnight_oos_trades.csv \
  --output-dir reports
```

This joins the filtered best-strategy OOS trades to TAIEX Day 0 data and writes `reports/closed_limit_up_market_regime_trades.csv`, `reports/closed_limit_up_market_regime_summary.csv`, `reports/closed_limit_up_market_regime_stress_tests.csv`, and `reports/closed_limit_up_market_regime_report.md`. It reports bull, bear, correction, TAIEX return-bucket, year, and quarter performance, plus defensive stress tests such as bull-only, not-bear, avoid-correction, and avoid-weak-market-day.

Diagnose the best closed-limit-up overnight OOS trades by sector and industry:

```bash
python -m src.reports.diagnose_sector_concentration \
  --db data/taiwan_trading.duckdb \
  --oos-trades reports/walk_forward_closed_limit_up_overnight_oos_trades.csv \
  --output-dir reports
```

This writes `reports/closed_limit_up_sector_diagnostic_trades.csv`, `reports/closed_limit_up_sector_summary.csv`, `reports/closed_limit_up_industry_summary.csv`, `reports/closed_limit_up_sector_concentration.csv`, `reports/closed_limit_up_sector_stress_tests.csv`, and `reports/closed_limit_up_sector_report.md`. It keeps trades with missing sector mappings, labels them explicitly, and falls back to symbol/name concentration until a complete map is imported.

Run the first simple daily strategy backtest:

```bash
python -m src.backtests.strategy_limit_momentum \
  --db data/taiwan_trading.duckdb \
  --start 2023-01-01 \
  --end 2026-06-22 \
  --event-types near_limit_8_9 near_limit_9_10 closed_limit_up \
  --fixed-notional-twd 100000 \
  --min-turnover-twd 100000000 \
  --min-volume-ratio-20d 2.0 \
  --min-close-location 0.85 \
  --take-profit-pct 0.03 \
  --stop-loss-pct 0.02 \
  --path-assumption pessimistic \
  --market-regime none \
  --sector-filter none \
  --require-foreign-not-selling-heavily false \
  --require-investment-trust-buying false \
  --avoid-margin-overcrowded false \
  --prefer-short-balance-rising-before-limit-up false
```

This buys at Day 1 open and uses Day 1 high/low to simulate a take-profit and stop-loss under an OHLC path assumption. `optimistic` checks take-profit before stop-loss, `pessimistic` checks stop-loss before take-profit, and `close_only` ignores both and exits at Day 1 close. Trades stored in `backtest_trades` and `reports/strategy_limit_momentum_trades.csv` use the selected `--path-assumption`; `reports/strategy_limit_momentum_summary.csv` compares all three assumptions side by side. Position sizing uses fixed notional rounded down to 1,000-share board lots.

Strategy filters support `--market-regime none|taiex_above_20ma|taiex_above_60ma|taiex_positive_day0`, `--sector-filter none|sector_return_positive_day0|sector_momentum_5d_positive|sector_top_quartile_day0`, and flow/margin flags: `--require-foreign-not-selling-heavily`, `--require-investment-trust-buying`, `--avoid-margin-overcrowded`, and `--prefer-short-balance-rising-before-limit-up`. The foreign-heavy-sell and margin-crowding thresholds are configurable with `--foreign-heavy-sell-threshold` and `--margin-crowding-threshold`.

Run the intraday Day 1 opening-continuation strategy:

```bash
python -m src.backtests.intraday_opening_continuation \
  --db data/taiwan_trading.duckdb \
  --event-types closed_limit_up near_limit_9_10 \
  --entry-window-minutes 3 \
  --min-open-gap-pct 0.005 \
  --max-open-gap-pct 0.05 \
  --take-profit-pct 0.03 \
  --stop-loss-pct 0.015 \
  --vwap-fail-exit true \
  --time-exit 13:20 \
  --market-regime none \
  --sector-filter none \
  --require-foreign-not-selling-heavily false \
  --require-investment-trust-buying false
```

This reads Day 0 events from `event_candidates`, applies the same regime, sector, institutional-flow, and margin/short filters as the daily strategy, joins Day 1 1-minute bars from `intraday_bars`, enters at the close of the opening window only when gap, VWAP, drawdown, and opening-volume filters pass, then exits by stop, take-profit, VWAP failure, or time. It writes trades to `backtest_trades`, `reports/intraday_opening_trades.csv`, and `reports/intraday_opening_summary.csv`.

Run the limit-momentum grid search:

```bash
python -m src.backtests.grid_search_limit_momentum --db data/taiwan_trading.duckdb
```

The default grid evaluates single event types plus all non-empty combinations of `near_limit_8_9`, `near_limit_9_10`, `closed_limit_up`, and `touched_limit_not_closed`; liquidity, volume-ratio, close-location, take-profit, stop-loss, path-assumption, market, and optional flow/margin filters; and a train/test split of `2023-01-01` to `2024-12-31` versus `2025-01-01` to `2026-06-22`. Flow filter grids can be enabled with comma-separated boolean overrides such as `--require-investment-trust-buying-values false,true` or `--avoid-margin-overcrowded-values false,true`. It writes `reports/grid_search_results.csv`, `reports/grid_search_top_train.csv`, and `reports/grid_search_top_test.csv`. Results are resumable by `config_hash`; use `--force` to overwrite prior grid results.

Run walk-forward validation:

```bash
python -m src.backtests.walk_forward --db data/taiwan_trading.duckdb
```

The default walk-forward run uses expanding train windows and quarterly out-of-sample tests: train through `2023-12-31`, test `2024 Q1`; then train through `2024 Q1`, test `2024 Q2`; and so on through `2026-06-22`. For each window it ranks grid parameters by `avg_net_return * sqrt(num_trades) - 0.5 * abs(max_drawdown)`, tests the top configs out of sample, reports stability for event, liquidity, path, market, and flow/margin parameters, and writes `reports/walk_forward_results.csv`, `reports/walk_forward_selected_configs.csv`, and `reports/walk_forward_oos_equity_curve.csv`.

## Taiwan Trading Costs

The cost model in `src/backtests/costs.py` supports configurable buy/sell commission, broker commission discounts, sale tax, slippage, minimum commission, and a short-trading borrow-fee placeholder.

Defaults:

- Standard commission rate: `0.1425%`
- Commission discount multiplier: `0.28`
- Qualified stock day-trade sale tax: `0.15%`
- Normal sale tax option: `0.30%`
- Slippage: `5` bps per side
- Minimum commission: `20` TWD per order

Taiwan brokers often discount the standard commission, so `commission_discount` is configurable and should be set to match the assumed broker/account rather than treated as universal.

```python
from src.backtests.costs import calculate_trade_costs

costs = calculate_trade_costs(
    side="long",
    entry_price=100,
    exit_price=110,
    shares=1000,
    commission_discount=0.28,
    is_day_trade=True,
)
```

Generate a simple event report from the DuckDB database:

```bash
python -m src.reports.generate_event_report \
  --db data/taiwan_trading.duckdb \
  --event-column is_plus_8_to_9_not_limit
```

Generate the consolidated limit-momentum research report:

```bash
python -m src.reports.generate_limit_momentum_report \
  --reports-dir reports \
  --db data/taiwan_trading.duckdb
```

Run tests:

```bash
pytest
```

## Notes

Taiwan price-limit calculations can have exchange-specific details and exceptions. The feature code includes a practical tick-size approximation suitable for research screening, but production-grade trading analysis should verify limit prices against official exchange data when possible.
