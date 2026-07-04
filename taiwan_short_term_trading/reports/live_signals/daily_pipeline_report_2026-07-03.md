# Daily Closed-Limit-Up Paper Pipeline - 2026-07-03

Run timestamp: `2026-07-05T05:55:58.005044+08:00`
Database: `data/taiwan_trading.duckdb`
Dry run: `False`
Profile selection: `all`

## Data Coverage

Latest TWSE date: `2026-07-03`
Latest TPEx date: `2026-07-03`
Latest TAIEX date: `2026-07-03`
TAIEX freshness status: `fresh`
daily_prices rows: `1,711,863`
event_candidates rows: `56,169`

## TAIEX Freshness

| taiex_freshness_status | taiex_retry_attempted | taiex_retry_succeeded | skipped_profiles_due_to_stale_regime |
| --- | --- | --- | --- |
| fresh | False | False |  |

## Signal Generation

Signal date: `2026-07-03`
Generated signal file: `reports/live_signals/closed_limit_up_signals_all_profiles_2026-07-03.csv`
Raw closed-limit-up candidates: `95`
Selected paper orders: `9`
Selected symbols: 3663, 4541, 5314, 5371, 6174

## Profile Comparison

| profile_name | selected_orders | symbols |
| --- | --- | --- |
| original_champion_tpex_500m | 4 | 5371, 5314, 3663, 6174 |
| broad_challenger_097dd332 | 0 |  |
| conservative_tpex_35adc734 | 5 | 5371, 5314, 3663, 6174, 4541 |

Overlapping symbols:

| profile_pair | overlap_count | symbols |
| --- | --- | --- |
| original_champion_tpex_500m__broad_challenger_097dd332 | 0 |  |
| original_champion_tpex_500m__conservative_tpex_35adc734 | 4 | 3663, 5314, 5371, 6174 |
| broad_challenger_097dd332__conservative_tpex_35adc734 | 0 |  |

## Previous Evaluations

Evaluated signal files:

_None_

Pending evaluations:

_None_

## Paper Ledger Summary

| total_evaluated_paper_trades | cumulative_net_pnl | avg_net_return | median_net_return | win_rate | profit_factor | by_profile |
| --- | --- | --- | --- | --- | --- | --- |
| 18 | 23,507.24 | 0.0067 | -0.0009 | 0.5000 | 2.1074 | {'broad_challenger_097dd332': {'trades': 15, 'net_pnl': 30179.108999999993, 'avg_net_return': 0.010777560922113768, 'win_rate': 0.6, 'profit_factor': 3.073448146826256}, 'conservative_tpex_35adc734': {'trades': 1, 'net_pnl': -2869.0881, 'avg_net_return': -0.0177104203703703, 'win_rate': 0.0, 'profit_factor': 0.0}, 'original_champion_tpex_500m': {'trades': 2, 'net_pnl': -3802.7789, 'avg_net_return': -0.01125421018518515, 'win_rate': 0.0, 'profit_factor': 0.0}} |

Profile-level ledger:

| profile_name | trades | net_pnl | avg_net_return | win_rate | profit_factor |
| --- | --- | --- | --- | --- | --- |
| broad_challenger_097dd332 | 15 | 30,179.11 | 0.0108 | 0.6000 | 3.0734 |
| conservative_tpex_35adc734 | 1 | -2,869.09 | -0.0177 | 0.0000 | 0.0000 |
| original_champion_tpex_500m | 2 | -3,802.78 | -0.0113 | 0.0000 | 0.0000 |

## Manual Fill Observations

| total_signal_orders | missing_manual_observations | missing_symbols |
| --- | --- | --- |
| 27 | 27 | ['2312', '8105', '6695', '3094', '2483', '3605', '8182', '1515', '6909', '6224', '2302', '3317', '6133', '2484', '1515', '8182', '5371', '5314', '3663', '6174', '4541', '8182', '3317', '5371', '5314', '3663', '6174'] |

## Warnings

- Manual fill observations are missing for 27 selected paper signal(s).
- Execution remains paper-only until actual Day0 close auction/order-book fills are verified.
