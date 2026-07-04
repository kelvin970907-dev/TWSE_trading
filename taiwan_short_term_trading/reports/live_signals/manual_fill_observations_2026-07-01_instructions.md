# Manual Fill Observation Instructions - 2026-07-01 All Profiles

This workflow is for paper-trading observation only. Do not submit real orders from this workflow.

Combined profile-specific template:
`reports/live_signals/manual_fill_observations_2026-07-01_all_profiles_template.csv`

Unique-symbol helper:
`reports/live_signals/manual_fill_observations_2026-07-01_unique_symbols_helper.csv`

## How To Use These Files

The combined template has one row per profile-specific paper signal. Keep those rows separate because the manual-fill import links observations back to the paper ledger by `profile_name + signal_date + symbol + market`.

The unique-symbol helper is for practical broker/order-book observation. It collapses duplicate symbols across profiles so you can observe each symbol once at the broker/order-book level.

If one broker/order-book observation applies to duplicate rows, copy the same observed bid/ask, visible sizes, lock status, submission status, fill status, and notes into each matching profile row before import. For example, `8182` appears in all three profiles, so one observation can be copied into the three `8182` rows in the combined template if the intended entry/order details match.

## Duplicate Symbols Across Profiles

- `8182` 加高: broad_challenger_097dd332, conservative_tpex_35adc734, original_champion_tpex_500m

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
  --csv reports/live_signals/manual_fill_observations_2026-07-01_all_profiles_template_completed.csv
```

Then summarize with:

```bash
python -m src.live.manual_fill_log summarize \
  --db data/taiwan_trading.duckdb \
  --output-dir reports/live_signals
```

Reminder: these observations are meant to answer whether Day0 close limit-up entries were realistically executable. They are not trading instructions.
