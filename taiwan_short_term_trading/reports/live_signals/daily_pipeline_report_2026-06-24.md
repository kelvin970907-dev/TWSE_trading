# Daily Closed-Limit-Up Paper Pipeline - 2026-06-24

Run timestamp: `2026-07-02T13:15:23.230677+08:00`
Database: `data/taiwan_trading.duckdb`
Dry run: `False`
Profile selection: `all`

## Data Coverage

Latest TWSE date: `2026-06-24`
Latest TPEx date: `2026-06-24`
Latest TAIEX date: `2026-06-24`
daily_prices rows: `1,696,066`
event_candidates rows: `55,715`

## Signal Generation

Signal date: `2026-06-24`
Generated signal file: `reports/live_signals/closed_limit_up_signals_all_profiles_2026-06-24.csv`
Raw closed-limit-up candidates: `53`
Selected paper orders: `5`
Selected symbols: 2312, 2483, 3094, 6695, 8105

## Profile Comparison

| profile_name | selected_orders | symbols |
| --- | --- | --- |
| original_champion_tpex_500m | 0 |  |
| broad_challenger_097dd332 | 5 | 2312, 8105, 6695, 3094, 2483 |
| conservative_tpex_35adc734 | 0 |  |

Overlapping symbols:

| profile_pair | overlap_count | symbols |
| --- | --- | --- |
| original_champion_tpex_500m__broad_challenger_097dd332 | 0 |  |
| original_champion_tpex_500m__conservative_tpex_35adc734 | 0 |  |
| broad_challenger_097dd332__conservative_tpex_35adc734 | 0 |  |

## Previous Evaluations

Evaluated signal files:

_None_

Pending evaluations:

_None_

## Paper Ledger Summary

| total_evaluated_paper_trades | cumulative_net_pnl | avg_net_return | median_net_return | win_rate | profit_factor | by_profile |
| --- | --- | --- | --- | --- | --- | --- |
| 0 | 0.0000 |  |  |  |  | {} |

Profile-level ledger:

_No rows._

## Manual Fill Observations

| total_signal_orders | missing_manual_observations | missing_symbols |
| --- | --- | --- |
| 5 | 5 | ['2312', '8105', '6695', '3094', '2483'] |

Manual fill template: `reports/live_signals/manual_fill_observations_2026-06-24_broad_challenger_097dd332_template.csv`

Manual fill instructions: `reports/live_signals/manual_fill_observations_2026-06-24_broad_challenger_097dd332_instructions.md`

Reminder: `broad_challenger_097dd332` requires 5 manual fill observations for 2026-06-24. This is paper-observation only; no real orders were submitted.

## Warnings

- skip_data_update enabled: daily_prices were not refreshed.
- skip_index_update enabled: TAIEX index data was not refreshed.
- skip_sector_update enabled: stock_sector_map was not refreshed.
- Manual fill observations are missing for 5 selected paper signal(s).
- Execution remains paper-only until actual Day0 close auction/order-book fills are verified.
