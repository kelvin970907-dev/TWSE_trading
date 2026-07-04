# Daily Closed-Limit-Up Paper Pipeline - 2026-07-02

Run timestamp: `2026-07-03T22:49:23.844988+08:00`
Database: `data/taiwan_trading.duckdb`
Dry run: `False`
Profile selection: `all`

## Data Coverage

Latest TWSE date: `2026-07-02`
Latest TPEx date: `2026-07-02`
Latest TAIEX date: `2026-07-02`
daily_prices rows: `1,709,606`
event_candidates rows: `56,005`

## Signal Generation

Signal date: `2026-07-02`
Generated signal file: `reports/live_signals/closed_limit_up_signals_all_profiles_2026-07-02.csv`
Raw closed-limit-up candidates: `90`
Selected paper orders: `6`
Selected symbols: 1515, 2302, 2484, 3317, 6133

## Profile Comparison

| profile_name | selected_orders | symbols |
| --- | --- | --- |
| original_champion_tpex_500m | 1 | 3317 |
| broad_challenger_097dd332 | 5 | 2302, 3317, 6133, 2484, 1515 |
| conservative_tpex_35adc734 | 0 |  |

Overlapping symbols:

| profile_pair | overlap_count | symbols |
| --- | --- | --- |
| original_champion_tpex_500m__broad_challenger_097dd332 | 1 | 3317 |
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
| 12 | 5,570.71 | 0.0016 | -0.0037 | 0.5000 | 1.2983 | {'broad_challenger_097dd332': {'trades': 10, 'net_pnl': 11308.881249999997, 'avg_net_return': 0.00542332731655958, 'win_rate': 0.6, 'profit_factor': 1.8742206018088354}, 'conservative_tpex_35adc734': {'trades': 1, 'net_pnl': -2869.0881, 'avg_net_return': -0.0177104203703703, 'win_rate': 0.0, 'profit_factor': 0.0}, 'original_champion_tpex_500m': {'trades': 1, 'net_pnl': -2869.0881, 'avg_net_return': -0.0177104203703703, 'win_rate': 0.0, 'profit_factor': 0.0}} |

Profile-level ledger:

| profile_name | trades | net_pnl | avg_net_return | win_rate | profit_factor |
| --- | --- | --- | --- | --- | --- |
| broad_challenger_097dd332 | 10 | 11,308.88 | 0.0054 | 0.6000 | 1.8742 |
| conservative_tpex_35adc734 | 1 | -2,869.09 | -0.0177 | 0.0000 | 0.0000 |
| original_champion_tpex_500m | 1 | -2,869.09 | -0.0177 | 0.0000 | 0.0000 |

## Manual Fill Observations

| total_signal_orders | missing_manual_observations | missing_symbols |
| --- | --- | --- |
| 18 | 18 | ['2312', '8105', '6695', '3094', '2483', '3605', '8182', '1515', '6909', '6224', '2302', '3317', '6133', '2484', '1515', '8182', '8182', '3317'] |

## Warnings

- skip_data_update enabled: daily_prices were not refreshed.
- skip_index_update enabled: TAIEX index data was not refreshed.
- skip_sector_update enabled: stock_sector_map was not refreshed.
- Manual fill observations are missing for 18 selected paper signal(s).
- Execution remains paper-only until actual Day0 close auction/order-book fills are verified.
