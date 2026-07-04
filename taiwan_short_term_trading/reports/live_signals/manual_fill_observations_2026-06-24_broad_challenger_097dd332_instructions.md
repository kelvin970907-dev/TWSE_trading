# Manual Fill Observation Instructions - 2026-06-24 Broad Challenger

This file is for paper-trading observation only. Do not submit real orders from this workflow.

Template CSV:
`reports/live_signals/manual_fill_observations_2026-06-24_broad_challenger_097dd332_template.csv`

Profile: `broad_challenger_097dd332`
Candidate hash: `097dd332`
Signal date: `2026-06-24`
Symbols requiring observation: `2312, 8105, 6695, 3094, 2483`

## What To Observe

During paper trading, observe the broker quote screen, auction/order-book view, or paper order preview around the Day0 close. Record the best bid/ask, visible bid/ask sizes, whether the stock was locked at limit-up, and whether a hypothetical Day0 close limit order looked likely to fill.

If using a broker paper order preview, record whether the order was accepted, queued, fully filled, partially filled, not filled, or left unknown. Attach screenshot paths when available.

## Fields To Fill Manually

- `observed_time`: local Taiwan time of the observation, for example `13:25` or `13:30`.
- `broker`: broker/platform or data source observed.
- `displayed_best_bid`, `displayed_best_ask`: observed quote prices.
- `displayed_bid_size_shares`, `displayed_ask_size_shares`: visible size in shares.
- `was_limit_up_locked`: `true` if best bid was at limit-up and no meaningful ask/available sellers were visible.
- `was_order_submitted`: keep `false` unless an actual paper/simulated order was submitted.
- `simulated_queue_position`: queue estimate if available.
- `actual_filled_shares`, `actual_avg_fill_price`: fill result if a paper/simulated broker order was used.
- `fill_status`: one of `not_submitted`, `fully_filled`, `partially_filled`, `not_filled`, or `unknown`.
- `reason_not_filled`: queue not reached, no sellers, broker rejected, observation only, etc.
- `screenshot_path`: local path to screenshot evidence if available.
- `notes`: any context about quote behavior or data quality.

## Import Completed Observations

After manually filling the CSV, save a completed copy, then import it with:

```bash
python -m src.live.manual_fill_log import-csv \
  --db data/taiwan_trading.duckdb \
  --csv reports/live_signals/manual_fill_observations_2026-06-24_broad_challenger_097dd332_template_completed.csv
```

Then summarize with:

```bash
python -m src.live.manual_fill_log summarize \
  --db data/taiwan_trading.duckdb \
  --output-dir reports/live_signals
```

Reminder: these observations are meant to answer whether Day0 close limit-up entries were realistically executable. They are not trading instructions.
