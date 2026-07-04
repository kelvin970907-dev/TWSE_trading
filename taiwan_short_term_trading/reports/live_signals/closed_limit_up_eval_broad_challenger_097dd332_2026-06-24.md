# Closed-Limit-Up Paper Evaluation - 2026-06-24

Signal CSV: `reports/live_signals/closed_limit_up_signals_broad_challenger_097dd332_2026-06-24.csv`
Signal date: `2026-06-24`
Evaluation date: `2026-07-02`

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
| gross_pnl | 20,100.00 |
| net_pnl | 15,793.63 |
| avg_net_return | 0.0166 |
| median_net_return | 0.0083 |
| win_rate | 0.8000 |
| profit_factor | 13.5463 |

## Order-Level Results

| profile_name | candidate_hash | signal_date | evaluation_date | symbol | name | market | sector | industry | status | planned_entry_price | planned_shares | planned_buy_notional_twd | day1_trade_date | day1_open | day1_high | day1_low | day1_close | theoretical_exit_price | gross_return | open_to_high_return | open_to_low_return | open_to_close_return | gross_pnl | net_pnl | net_return | buy_commission | sell_commission | sell_tax | slippage_cost | total_cost | paper_fill_assumed | actual_broker_fill_known | actual_broker_filled | notes | execution_warning |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| broad_challenger_097dd332 | 097dd332 | 2026-06-24 | 2026-07-02 | 2312 | 金寶 | TWSE | Technology/Electronics | Other Electronics | evaluated | 38.0000 | 5000 | 190,000.00 | 2026-06-25 | 38.5000 | 38.5500 | 36.2500 | 36.4000 | 38.5000 | 0.0132 | 0.0013 | -0.0584 | -0.0545 | 2,500.00 | 1,578.63 | 0.0083 | 75.8100 | 76.8075 | 577.5000 | 191.2500 | 921.3675 | True | False |  | Evaluated using next available daily_prices row for the same symbol and market. | This remains theoretical unless actual broker fill data confirms Day0 close execution. |
| broad_challenger_097dd332 | 097dd332 | 2026-06-24 | 2026-07-02 | 8105 | 凌巨 | TWSE | Technology/Electronics | Optoelectronics | evaluated | 23.5000 | 8000 | 188,000.00 | 2026-06-25 | 23.8000 | 25.7500 | 23.1000 | 25.1500 | 23.8000 | 0.0128 | 0.0819 | -0.0294 | 0.0567 | 2,400.00 | 1,488.62 | 0.0079 | 75.0120 | 75.9696 | 571.2000 | 189.2000 | 911.3816 | True | False |  | Evaluated using next available daily_prices row for the same symbol and market. | This remains theoretical unless actual broker fill data confirms Day0 close execution. |
| broad_challenger_097dd332 | 097dd332 | 2026-06-24 | 2026-07-02 | 6695 | 芯鼎 | TWSE | Technology/Electronics | Semiconductor | evaluated | 68.9000 | 2000 | 137,800.00 | 2026-06-25 | 68.6000 | 69.0000 | 64.4000 | 64.7000 | 68.6000 | -0.0044 | 0.0058 | -0.0612 | -0.0569 | -600.0000 | -1,258.83 | -0.0091 | 54.9822 | 54.7428 | 411.6000 | 137.5000 | 658.8250 | True | False |  | Evaluated using next available daily_prices row for the same symbol and market. | This remains theoretical unless actual broker fill data confirms Day0 close execution. |
| broad_challenger_097dd332 | 097dd332 | 2026-06-24 | 2026-07-02 | 3094 | 聯傑 | TWSE | Technology/Electronics | Semiconductor | evaluated | 45.2500 | 4000 | 181,000.00 | 2026-06-25 | 46.0000 | 46.1500 | 42.2500 | 43.3000 | 46.0000 | 0.0166 | 0.0033 | -0.0815 | -0.0587 | 3,000.00 | 2,119.86 | 0.0117 | 72.2190 | 73.4160 | 552.0000 | 182.5000 | 880.1350 | True | False |  | Evaluated using next available daily_prices row for the same symbol and market. | This remains theoretical unless actual broker fill data confirms Day0 close execution. |
| broad_challenger_097dd332 | 097dd332 | 2026-06-24 | 2026-07-02 | 2483 | 百容 | TWSE | Technology/Electronics | Electronic Components | evaluated | 46.1000 | 4000 | 184,400.00 | 2026-06-25 | 49.3000 | 50.7000 | 49.3000 | 50.7000 | 49.3000 | 0.0694 | 0.0284 | 0.0000 | 0.0284 | 12,800.00 | 11,865.34 | 0.0643 | 73.5756 | 78.6828 | 591.6000 | 190.8000 | 934.6584 | True | False |  | Evaluated using next available daily_prices row for the same symbol and market. | This remains theoretical unless actual broker fill data confirms Day0 close execution. |

## Missing Data Warnings

_No rows._

## Fill Verification Checklist

- Confirm whether the Day0 close order was actually accepted and filled by the broker.
- Record auction/order-book evidence when available.
- Compare actual broker fill price with planned Day0 close.
- Compare actual exit with theoretical Day1 open.

Paper trade only. Day0 close limit-up fills may not be executable without auction/order-book confirmation.
