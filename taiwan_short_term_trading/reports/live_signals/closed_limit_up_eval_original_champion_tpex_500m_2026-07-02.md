# Closed-Limit-Up Paper Evaluation - 2026-07-02

Signal CSV: `reports/live_signals/closed_limit_up_signals_original_champion_tpex_500m_2026-07-02.csv`
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
| planned_orders | 1.0000 |
| evaluated_orders | 1.0000 |
| missing_day1_data | 0.0000 |
| invalid_price | 0.0000 |
| gross_pnl | 0.0000 |
| net_pnl | -933.6908 |
| avg_net_return | -0.0048 |
| median_net_return | -0.0048 |
| win_rate | 0.0000 |
| profit_factor | 0.0000 |

## Order-Level Results

| profile_name | candidate_hash | signal_date | evaluation_date | symbol | name | market | sector | industry | status | planned_entry_price | planned_shares | planned_buy_notional_twd | day1_trade_date | day1_open | day1_high | day1_low | day1_close | theoretical_exit_price | gross_return | open_to_high_return | open_to_low_return | open_to_close_return | gross_pnl | net_pnl | net_return | buy_commission | sell_commission | sell_tax | slippage_cost | total_cost | paper_fill_assumed | actual_broker_fill_known | actual_broker_filled | notes | execution_warning |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| original_champion_tpex_500m |  | 2026-07-02 | 2026-07-03 | 3317 | 尼克森 | TPEX | Technology/Electronics | Semiconductor | evaluated | 97.3000 | 2000 | 194,600.00 | 2026-07-03 | 97.3000 | 102.0000 | 92.5000 | 96.2000 | 97.3000 | 0.0000 | 0.0483 | -0.0493 | -0.0113 | 0.0000 | -933.6908 | -0.0048 | 77.6454 | 77.6454 | 583.8000 | 194.6000 | 933.6908 | True | False |  | Evaluated using next available daily_prices row for the same symbol and market. | This remains theoretical unless actual broker fill data confirms Day0 close execution. |

## Missing Data Warnings

_No rows._

## Fill Verification Checklist

- Confirm whether the Day0 close order was actually accepted and filled by the broker.
- Record auction/order-book evidence when available.
- Compare actual broker fill price with planned Day0 close.
- Compare actual exit with theoretical Day1 open.

Paper trade only. Day0 close limit-up fills may not be executable without auction/order-book confirmation.
