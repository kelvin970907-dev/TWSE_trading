# Closed-Limit-Up Paper Evaluation - 2026-07-01

Signal CSV: `reports/live_signals/closed_limit_up_signals_broad_challenger_097dd332_2026-07-01.csv`
Signal date: `2026-07-01`
Evaluation date: `2026-07-03`

## Reminder

This remains theoretical unless actual broker fill data confirms Day0 close execution.

## Cost Assumptions

| commission_rate | commission_discount | sell_tax_rate | slippage_bps_per_side | minimum_commission_twd |
| --- | --- | --- | --- | --- |
| 0.0014 | 0.2800 | 0.0030 | 5.0000 | 20.0000 |

## Summary Metrics

| metric | value |
| --- | --- |
| planned_orders | 5.0000 |
| evaluated_orders | 5.0000 |
| missing_day1_data | 0.0000 |
| invalid_price | 0.0000 |
| gross_pnl | -350.0000 |
| net_pnl | -4,484.75 |
| avg_net_return | -0.0058 |
| median_net_return | -0.0164 |
| win_rate | 0.4000 |
| profit_factor | 0.6159 |

## Order-Level Results

| profile_name | candidate_hash | signal_date | evaluation_date | symbol | name | market | sector | industry | status | planned_entry_price | planned_shares | planned_buy_notional_twd | day1_trade_date | day1_open | day1_high | day1_low | day1_close | theoretical_exit_price | gross_return | open_to_high_return | open_to_low_return | open_to_close_return | gross_pnl | net_pnl | net_return | buy_commission | sell_commission | sell_tax | slippage_cost | total_cost | paper_fill_assumed | actual_broker_fill_known | actual_broker_filled | notes | execution_warning |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| broad_challenger_097dd332 | 097dd332 | 2026-07-01 | 2026-07-03 | 3605 | 宏致 | TWSE | Technology/Electronics | Electronic Components | evaluated | 85.6000 | 2000 | 171,200.00 | 2026-07-02 | 84.6000 | 92.0000 | 83.2000 | 90.1000 | 84.6000 | -0.0117 | 0.0875 | -0.0165 | 0.0650 | -2,000.00 | -2,813.62 | -0.0164 | 68.3088 | 67.5108 | 507.6000 | 170.2000 | 813.6196 | True | False |  | Evaluated using next available daily_prices row for the same symbol and market. | This remains theoretical unless actual broker fill data confirms Day0 close execution. |
| broad_challenger_097dd332 | 097dd332 | 2026-07-01 | 2026-07-03 | 8182 | 加高 | TPEX | Technology/Electronics | Electronic Components | evaluated | 54.0000 | 3000 | 162,000.00 | 2026-07-02 | 53.3000 | 57.6000 | 51.5000 | 55.3000 | 53.3000 | -0.0130 | 0.0807 | -0.0338 | 0.0375 | -2,100.00 | -2,869.09 | -0.0177 | 64.6380 | 63.8001 | 479.7000 | 160.9500 | 769.0881 | True | False |  | Evaluated using next available daily_prices row for the same symbol and market. | This remains theoretical unless actual broker fill data confirms Day0 close execution. |
| broad_challenger_097dd332 | 097dd332 | 2026-07-01 | 2026-07-03 | 1515 | 力山 | TWSE | Industrials/Other | Electric Machinery | evaluated | 35.4500 | 5000 | 177,250.00 | 2026-07-02 | 37.0000 | 38.9500 | 37.0000 | 38.9500 | 37.0000 | 0.0437 | 0.0527 | 0.0000 | 0.0527 | 7,750.00 | 6,869.34 | 0.0388 | 70.7228 | 73.8150 | 555.0000 | 181.1250 | 880.6627 | True | False |  | Evaluated using next available daily_prices row for the same symbol and market. | This remains theoretical unless actual broker fill data confirms Day0 close execution. |
| broad_challenger_097dd332 | 097dd332 | 2026-07-01 | 2026-07-03 | 6909 | 創控 | TWSE | Technology/Electronics | Semiconductor | evaluated | 60.6000 | 3000 | 181,800.00 | 2026-07-02 | 61.0000 | 61.5000 | 58.2000 | 59.0000 | 61.0000 | 0.0066 | 0.0082 | -0.0459 | -0.0328 | 1,200.00 | 323.0448 | 0.0018 | 72.5382 | 73.0170 | 549.0000 | 182.4000 | 876.9552 | True | False |  | Evaluated using next available daily_prices row for the same symbol and market. | This remains theoretical unless actual broker fill data confirms Day0 close execution. |
| broad_challenger_097dd332 | 097dd332 | 2026-07-01 | 2026-07-03 | 6224 | 聚鼎 | TWSE | Technology/Electronics | Electronic Components | evaluated | 84.9000 | 2000 | 169,800.00 | 2026-07-02 | 82.3000 | 85.6000 | 77.2000 | 80.9000 | 82.3000 | -0.0306 | 0.0401 | -0.0620 | -0.0170 | -5,200.00 | -5,994.43 | -0.0353 | 67.7502 | 65.6754 | 493.8000 | 167.2000 | 794.4256 | True | False |  | Evaluated using next available daily_prices row for the same symbol and market. | This remains theoretical unless actual broker fill data confirms Day0 close execution. |

## Missing Data Warnings

_No rows._

## Fill Verification Checklist

- Confirm whether the Day0 close order was actually accepted and filled by the broker.
- Record auction/order-book evidence when available.
- Compare actual broker fill price with planned Day0 close.
- Compare actual exit with theoretical Day1 open.

Paper trade only. Day0 close limit-up fills may not be executable without auction/order-book confirmation.
