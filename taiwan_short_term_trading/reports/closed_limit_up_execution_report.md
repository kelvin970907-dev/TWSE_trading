# Closed-Limit-Up Execution Diagnostic

Source OOS trades: `reports/walk_forward_closed_limit_up_overnight_oos_trades.csv`
Database: `data/taiwan_trading.duckdb`

## Executive Answer

Strict verdict: the daily-data edge is not coming mostly from likely hard-fill names, and it remains positive after removing the hardest-fill bucket. It is still not proven live-executable until order-book and closing-auction data confirms Day0 close fills.

## Best Strategy Filter

- Market: TPEX
- Fill assumption: moderate
- Minimum fill quality score: 60
- Minimum turnover: 500M TWD
- Minimum volume ratio 20D: 1.5
- Consecutive limit-up count: <= 3
- Price: 10 to 100 TWD

## Execution Bucket Summary

| execution_risk_bucket | trades | trade_share | average_net_return | median_net_return | win_rate | profit_factor | total_net_pnl | net_pnl_share | avg_return_contribution | max_drawdown |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| likely_hard_fill | 0 | 0.0000 |  |  |  |  | 0.0000 | 0.0000 |  | 0.0000 |
| possible_fill | 0 | 0.0000 |  |  |  |  | 0.0000 | 0.0000 |  | 0.0000 |
| likely_fillable | 630 | 0.4490 | 0.0151 | 0.0145 | 0.7111 | 4.3204 | 722,657.38 | 0.3723 | 0.0068 | -49,615.44 |
| very_fillable_proxy | 773 | 0.5510 | 0.0212 | 0.0190 | 0.7529 | 7.0797 | 1,218,658.23 | 0.6277 | 0.0117 | -29,699.40 |
| uncertain_daily_proxy | 0 | 0.0000 |  |  |  |  | 0.0000 | 0.0000 |  | 0.0000 |

## Profit Source

Likely hard-fill trades: 0, net PnL share 0.00%. Likely/very-fillable trades combined: 1403, total net PnL 1,941,316 TWD.

## Stress Tests

| scenario | kept_trades | removed_trades | kept_trade_share | kept_pnl_share | trades | average_net_return | median_net_return | win_rate | profit_factor | total_net_pnl | max_drawdown |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| baseline_best_strategy | 1403 | 0 | 1.0000 | 1.0000 | 1403 | 0.0185 | 0.0161 | 0.7341 | 5.6433 | 1,941,315.60 | -49,615.44 |
| remove_likely_hard_fill | 1403 | 0 | 1.0000 | 1.0000 | 1403 | 0.0185 | 0.0161 | 0.7341 | 5.6433 | 1,941,315.60 | -49,615.44 |
| remove_hard_and_possible_fill | 1403 | 0 | 1.0000 | 1.0000 | 1403 | 0.0185 | 0.0161 | 0.7341 | 5.6433 | 1,941,315.60 | -49,615.44 |
| keep_only_likely_and_very_fillable | 1403 | 0 | 1.0000 | 1.0000 | 1403 | 0.0185 | 0.0161 | 0.7341 | 5.6433 | 1,941,315.60 | -49,615.44 |

## Monte Carlo Partial-Fill Haircut

| fill_rate | iterations | mean_filled_trades | median_filled_trades | mean_total_net_pnl | median_total_net_pnl | p05_total_net_pnl | p95_total_net_pnl | mean_avg_net_return | median_avg_net_return | p05_avg_net_return | p95_avg_net_return |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0.2500 | 1000 | 350.4370 | 351.0000 | 484,988.19 | 485,284.07 | 412,402.58 | 556,827.57 | 0.0184 | 0.0184 | 0.0160 | 0.0207 |
| 0.5000 | 1000 | 701.8970 | 702.0000 | 977,827.91 | 979,040.67 | 892,223.97 | 1,060,099.60 | 0.0185 | 0.0185 | 0.0171 | 0.0198 |
| 0.7500 | 1000 | 1,052.38 | 1,052.00 | 1,454,601.06 | 1,455,504.05 | 1,384,808.54 | 1,526,510.20 | 0.0183 | 0.0183 | 0.0176 | 0.0191 |

## Data Needed Next

- Closing auction executable volume at limit-up price
- Closing auction imbalance by price level
- Best bid size and queue size at limit-up into the close
- Timestamped order-book snapshots for the final 5 to 30 minutes
- Actual matched volume at the closing auction
- Broker/order queue priority or simulated queue-position fill probability
- Opening auction imbalance and indicative open price on Day1
- Whether the Day0 close print came from continuous trading or closing auction only
