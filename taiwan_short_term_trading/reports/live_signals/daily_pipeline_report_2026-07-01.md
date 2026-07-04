# Daily Closed-Limit-Up Paper Pipeline - 2026-07-01

Run timestamp: `2026-07-03T09:28:43.380221+08:00`
Database: `data/taiwan_trading.duckdb`
Dry run: `False`
Profile selection: `all`

## Data Coverage

Latest TWSE date: `2026-07-02`
Latest TPEx date: `2026-07-01`
Latest TAIEX date: `2026-07-02`
daily_prices rows: `1,708,717`
event_candidates rows: `55,874`

## Signal Generation

Signal date: `2026-07-01`
Generated signal file: `reports/live_signals/closed_limit_up_signals_all_profiles_2026-07-01.csv`
Raw closed-limit-up candidates: `69`
Selected paper orders: `7`
Selected symbols: 1515, 3605, 6224, 6909, 8182

## Profile Comparison

| profile_name | selected_orders | symbols |
| --- | --- | --- |
| original_champion_tpex_500m | 1 | 8182 |
| broad_challenger_097dd332 | 5 | 3605, 8182, 1515, 6909, 6224 |
| conservative_tpex_35adc734 | 1 | 8182 |

Overlapping symbols:

| profile_pair | overlap_count | symbols |
| --- | --- | --- |
| original_champion_tpex_500m__broad_challenger_097dd332 | 1 | 8182 |
| original_champion_tpex_500m__conservative_tpex_35adc734 | 1 | 8182 |
| broad_challenger_097dd332__conservative_tpex_35adc734 | 1 | 8182 |

## Previous Evaluations

Evaluated signal files:

_None_

Pending evaluations:

_None_

## Paper Ledger Summary

| total_evaluated_paper_trades | cumulative_net_pnl | avg_net_return | median_net_return | win_rate | profit_factor | by_profile |
| --- | --- | --- | --- | --- | --- | --- |
| 5 | 15,793.63 | 0.0166 | 0.0083 | 0.8000 | 13.5463 | {'broad_challenger_097dd332': {'trades': 5, 'net_pnl': 15793.632499999998, 'avg_net_return': 0.01662984940805598, 'win_rate': 0.8, 'profit_factor': 13.546328917840048}} |

Profile-level ledger:

| profile_name | trades | net_pnl | avg_net_return | win_rate | profit_factor |
| --- | --- | --- | --- | --- | --- |
| broad_challenger_097dd332 | 5 | 15,793.63 | 0.0166 | 0.8000 | 13.5463 |

## Manual Fill Observations

| total_signal_orders | missing_manual_observations | missing_symbols |
| --- | --- | --- |
| 12 | 12 | ['2312', '8105', '6695', '3094', '2483', '3605', '8182', '1515', '6909', '6224', '8182', '8182'] |

## Warnings

- skip_data_update enabled: daily_prices were not refreshed.
- skip_index_update enabled: TAIEX index data was not refreshed.
- skip_sector_update enabled: stock_sector_map was not refreshed.
- Manual fill observations are missing for 12 selected paper signal(s).
- Execution remains paper-only until actual Day0 close auction/order-book fills are verified.
