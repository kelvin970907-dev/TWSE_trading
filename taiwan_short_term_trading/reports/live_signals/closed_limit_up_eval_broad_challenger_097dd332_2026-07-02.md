# Closed-Limit-Up Paper Evaluation - 2026-07-02

Signal CSV: `reports/live_signals/closed_limit_up_signals_broad_challenger_097dd332_2026-07-02.csv`
Signal date: `2026-07-02`
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
| gross_pnl | 23,350.00 |
| net_pnl | 18,870.23 |
| avg_net_return | 0.0215 |
| median_net_return | 0.0132 |
| win_rate | 0.6000 |
| profit_factor | 12.6549 |

## Order-Level Results

| profile_name | candidate_hash | signal_date | evaluation_date | symbol | name | market | sector | industry | status | planned_entry_price | planned_shares | planned_buy_notional_twd | day1_trade_date | day1_open | day1_high | day1_low | day1_close | theoretical_exit_price | gross_return | open_to_high_return | open_to_low_return | open_to_close_return | gross_pnl | net_pnl | net_return | buy_commission | sell_commission | sell_tax | slippage_cost | total_cost | paper_fill_assumed | actual_broker_fill_known | actual_broker_filled | notes | execution_warning |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| broad_challenger_097dd332 | 097dd332 | 2026-07-02 | 2026-07-03 | 2302 | 麗正 | TWSE | Technology/Electronics | Semiconductor | evaluated | 55.4000 | 3000 | 166,200.00 | 2026-07-03 | 56.4000 | 58.1000 | 51.5000 | 52.0000 | 56.4000 | 0.0181 | 0.0301 | -0.0869 | -0.0780 | 3,000.00 | 2,190.88 | 0.0132 | 66.3138 | 67.5108 | 507.6000 | 167.7000 | 809.1246 | True | False |  | Evaluated using next available daily_prices row for the same symbol and market. | This remains theoretical unless actual broker fill data confirms Day0 close execution. |
| broad_challenger_097dd332 | 097dd332 | 2026-07-02 | 2026-07-03 | 3317 | 尼克森 | TPEX | Technology/Electronics | Semiconductor | evaluated | 97.3000 | 2000 | 194,600.00 | 2026-07-03 | 97.3000 | 102.0000 | 92.5000 | 96.2000 | 97.3000 | 0.0000 | 0.0483 | -0.0493 | -0.0113 | 0.0000 | -933.6908 | -0.0048 | 77.6454 | 77.6454 | 583.8000 | 194.6000 | 933.6908 | True | False |  | Evaluated using next available daily_prices row for the same symbol and market. | This remains theoretical unless actual broker fill data confirms Day0 close execution. |
| broad_challenger_097dd332 | 097dd332 | 2026-07-02 | 2026-07-03 | 6133 | 金橋 | TWSE | Technology/Electronics | Electronic Components | evaluated | 26.2500 | 7000 | 183,750.00 | 2026-07-03 | 27.3500 | 28.2000 | 25.7500 | 25.8000 | 27.3500 | 0.0419 | 0.0311 | -0.0585 | -0.0567 | 7,700.00 | 6,788.35 | 0.0369 | 73.3163 | 76.3886 | 574.3500 | 187.6000 | 911.6548 | True | False |  | Evaluated using next available daily_prices row for the same symbol and market. | This remains theoretical unless actual broker fill data confirms Day0 close execution. |
| broad_challenger_097dd332 | 097dd332 | 2026-07-02 | 2026-07-03 | 2484 | 希華 | TWSE | Technology/Electronics | Electronic Components | evaluated | 87.7000 | 2000 | 175,400.00 | 2026-07-03 | 93.9000 | 96.4000 | 88.0000 | 96.4000 | 93.9000 | 0.0707 | 0.0266 | -0.0628 | 0.0266 | 12,400.00 | 11,510.08 | 0.0656 | 69.9846 | 74.9322 | 563.4000 | 181.6000 | 889.9168 | True | False |  | Evaluated using next available daily_prices row for the same symbol and market. | This remains theoretical unless actual broker fill data confirms Day0 close execution. |
| broad_challenger_097dd332 | 097dd332 | 2026-07-02 | 2026-07-03 | 1515 | 力山 | TWSE | Industrials/Other | Electric Machinery | evaluated | 38.9500 | 5000 | 194,750.00 | 2026-07-03 | 39.0000 | 42.8000 | 38.0500 | 42.0000 | 39.0000 | 0.0013 | 0.0974 | -0.0244 | 0.0769 | 250.0000 | -685.3853 | -0.0035 | 77.7053 | 77.8050 | 585.0000 | 194.8750 | 935.3853 | True | False |  | Evaluated using next available daily_prices row for the same symbol and market. | This remains theoretical unless actual broker fill data confirms Day0 close execution. |

## Missing Data Warnings

_No rows._

## Fill Verification Checklist

- Confirm whether the Day0 close order was actually accepted and filled by the broker.
- Record auction/order-book evidence when available.
- Compare actual broker fill price with planned Day0 close.
- Compare actual exit with theoretical Day1 open.

Paper trade only. Day0 close limit-up fills may not be executable without auction/order-book confirmation.
