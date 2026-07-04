# Closed-Limit-Up Paper Signals - 2026-06-24

## Execution Warning

Paper trade only. Day0 close limit-up fills may not be executable without auction/order-book confirmation.

## Strategy Parameters

| capital_twd | profile_name | candidate_hash | market | market_regime_filter | ranking_method | min_turnover_twd | min_volume_ratio_20d | min_fill_quality_score | max_consecutive_limit_ups | min_price | max_price | prior_5d_return_max | prior_20d_return_max | max_positions | target_notional_twd | max_notional_per_symbol_pct | max_notional_per_sector_pct | max_notional_per_industry_pct | board_lot_size | avoid_sectors | allowed_sectors | warnings | commission_rate | commission_discount | slippage_bps_per_side | minimum_commission_twd |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1,000,000.00 | broad_challenger_097dd332 | 097dd332 | BOTH | not_bear | fill_quality_score | 200,000,000.00 | 1.5000 | 60.0000 | 3 | 10.0000 | 100.0000 |  |  | 5 | 300,000.00 | 0.2000 | 1.0000 | 1.0000 | 1000 | ('Healthcare', 'Materials') | () | ('Broad BOTH-market challenger; TWSE sector coverage may be incomplete.', 'Paper only until Day0 close fills are verified.') | 0.0014 | 0.2800 | 5.0000 | 20.0000 |

## Market Date

Signal date: `2026-06-24`
Strategy universe: `BOTH`
Profile: `broad_challenger_097dd332`
Candidate hash: `097dd332`

## Candidate Counts

Raw closed-limit-up candidates: 53
Candidates after strategy filters: 9
Planned paper orders: 5

## Planned Paper Orders

| profile_name | candidate_hash | signal_date | symbol | name | market | sector | industry | close_price | limit_up_price | turnover_twd | volume_ratio_20d | consecutive_limit_up_count | fill_quality_score | planned_entry_price | planned_exit | target_notional_twd | capped_notional_twd | planned_shares | planned_buy_notional_twd | estimated_buy_cost | estimated_cash_required | ranking | notes | execution_warning |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| broad_challenger_097dd332 | 097dd332 | 2026-06-24 | 2312 | 金寶 | TWSE | Technology/Electronics | Other Electronics | 38.0000 | 38.0000 | 5,670,949,550.00 | 1.7916 | 1 | 100.0000 | 38.0000 | Day1 open | 300,000.00 | 200,000.00 | 5000 | 190,000.00 | 170.8100 | 190,170.81 | 1 | Ranked by fill_quality_score. Moderate fill proxy reason: moderate_daily_activity_evidence | Paper trade only. Day0 close limit-up fills may not be executable without auction/order-book confirmation. |
| broad_challenger_097dd332 | 097dd332 | 2026-06-24 | 8105 | 凌巨 | TWSE | Technology/Electronics | Optoelectronics | 23.5000 | 23.5000 | 839,384,700.00 | 1.6799 | 1 | 100.0000 | 23.5000 | Day1 open | 300,000.00 | 200,000.00 | 8000 | 188,000.00 | 169.0120 | 188,169.01 | 2 | Ranked by fill_quality_score. Moderate fill proxy reason: moderate_daily_activity_evidence | Paper trade only. Day0 close limit-up fills may not be executable without auction/order-book confirmation. |
| broad_challenger_097dd332 | 097dd332 | 2026-06-24 | 6695 | 芯鼎 | TWSE | Technology/Electronics | Semiconductor | 68.9000 | 68.5000 | 830,618,800.00 | 4.4307 | 1 | 100.0000 | 68.9000 | Day1 open | 300,000.00 | 200,000.00 | 2000 | 137,800.00 | 123.8822 | 137,923.88 | 3 | Ranked by fill_quality_score. Moderate fill proxy reason: moderate_daily_activity_evidence | Paper trade only. Day0 close limit-up fills may not be executable without auction/order-book confirmation. |
| broad_challenger_097dd332 | 097dd332 | 2026-06-24 | 3094 | 聯傑 | TWSE | Technology/Electronics | Semiconductor | 45.2500 | 45.2000 | 650,167,600.00 | 3.9435 | 1 | 100.0000 | 45.2500 | Day1 open | 300,000.00 | 200,000.00 | 4000 | 181,000.00 | 162.7190 | 181,162.72 | 4 | Ranked by fill_quality_score. Moderate fill proxy reason: moderate_daily_activity_evidence | Paper trade only. Day0 close limit-up fills may not be executable without auction/order-book confirmation. |
| broad_challenger_097dd332 | 097dd332 | 2026-06-24 | 2483 | 百容 | TWSE | Technology/Electronics | Electronic Components | 46.1000 | 46.1000 | 535,624,400.00 | 1.9007 | 1 | 100.0000 | 46.1000 | Day1 open | 300,000.00 | 200,000.00 | 4000 | 184,400.00 | 165.7756 | 184,565.78 | 5 | Ranked by fill_quality_score. Moderate fill proxy reason: moderate_daily_activity_evidence | Paper trade only. Day0 close limit-up fills may not be executable without auction/order-book confirmation. |

## Skipped Reasons

| reason | count |
| --- | --- |
| turnover_below_500m | 24 |
| price_outside_10_to_100 | 9 |
| volume_ratio_below_1_5 | 9 |
| max_positions | 4 |
| avoided_weak_sector | 2 |

## Candidate Diagnostics

| symbol | name | sector | industry | day0_close | day0_turnover_twd | volume_ratio_20d | consecutive_limit_up_count | fill_quality_score | fillable_moderate | moderate_fill_reason |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2312 | 金寶 | Technology/Electronics | Other Electronics | 38.0000 | 5,670,949,550.00 | 1.7916 | 1 | 100.0000 | True | moderate_daily_activity_evidence |
| 1301 | 台塑 | Materials | Plastics | 54.5000 | 4,128,043,750.00 | 1.8348 | 1 | 100.0000 | True | moderate_daily_activity_evidence |
| 2351 | 順德 | Technology/Electronics | Semiconductor | 217.5000 | 2,444,009,500.00 | 1.5071 | 1 | 100.0000 | True | moderate_daily_activity_evidence |
| 2484 | 希華 | Technology/Electronics | Electronic Components | 69.3000 | 1,814,967,700.00 | 0.6613 | 1 | 100.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 5328 | 華容 | Technology/Electronics | Electronic Components | 58.1000 | 1,565,817,528.00 | 0.7509 | 1 | 100.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 9105 | 泰金寶-DR | Unknown | Industry code 91 | 10.0000 | 1,561,740,000.00 | 1.2658 | 1 | 100.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 4722 | 國精化 | Materials | Chemical | 301.0000 | 1,515,377,500.00 | 2.6787 | 1 | 100.0000 | True | moderate_daily_activity_evidence |
| 5285 | 界霖 | Technology/Electronics | Semiconductor | 110.0000 | 1,429,925,400.00 | 2.4210 | 1 | 100.0000 | True | moderate_daily_activity_evidence |
| 6829 | 千附精密 | Technology/Electronics | Semiconductor | 226.5000 | 851,129,204.00 | 4.2524 | 1 | 100.0000 | True | moderate_daily_activity_evidence |
| 8105 | 凌巨 | Technology/Electronics | Optoelectronics | 23.5000 | 839,384,700.00 | 1.6799 | 1 | 100.0000 | True | moderate_daily_activity_evidence |
| 6695 | 芯鼎 | Technology/Electronics | Semiconductor | 68.9000 | 830,618,800.00 | 4.4307 | 1 | 100.0000 | True | moderate_daily_activity_evidence |
| 6834 | 天二科技 | Technology/Electronics | Electronic Components | 122.5000 | 674,431,500.00 | 1.2987 | 1 | 100.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 6462 | 神盾 | Technology/Electronics | Semiconductor | 134.0000 | 650,926,732.00 | 2.2386 | 1 | 100.0000 | True | moderate_daily_activity_evidence |
| 3094 | 聯傑 | Technology/Electronics | Semiconductor | 45.2500 | 650,167,600.00 | 3.9435 | 1 | 100.0000 | True | moderate_daily_activity_evidence |
| 2302 | 麗正 | Technology/Electronics | Semiconductor | 50.2000 | 544,377,700.00 | 1.1917 | 1 | 100.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 2483 | 百容 | Technology/Electronics | Electronic Components | 46.1000 | 535,624,400.00 | 1.9007 | 1 | 100.0000 | True | moderate_daily_activity_evidence |
| 6683 | 雍智科技 | Technology/Electronics | Semiconductor | 1,940.00 | 8,160,380,590.00 | 3.1127 | 2 | 90.0000 | True | moderate_daily_activity_evidence |
| 6213 | 聯茂 | Technology/Electronics | Electronic Components | 309.0000 | 7,663,695,500.00 | 1.4390 | 1 | 90.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 8054 | 安國 | Technology/Electronics | Semiconductor | 136.5000 | 2,597,867,456.00 | 7.6736 | 3 | 90.0000 | True | moderate_daily_activity_evidence |
| 8040 | 九暘 | Technology/Electronics | Semiconductor | 125.0000 | 1,823,623,219.00 | 6.1492 | 3 | 90.0000 | True | moderate_daily_activity_evidence |
| 8234 | 新漢 | Technology/Electronics | Computer/Peripheral | 70.7000 | 393,434,957.00 | 5.3760 | 1 | 90.0000 | True | moderate_daily_activity_evidence |
| 2460 | 建通 | Technology/Electronics | Electronic Components | 40.4500 | 303,764,900.00 | 5.6392 | 1 | 90.0000 | True | moderate_daily_activity_evidence |
| 3588 | 通嘉 | Technology/Electronics | Semiconductor | 75.9000 | 256,109,600.00 | 3.7426 | 1 | 90.0000 | True | moderate_daily_activity_evidence |
| 6620 | 漢達 | Healthcare | Biotechnology/Medical | 99.9000 | 213,042,154.00 | 3.4664 | 1 | 90.0000 | True | moderate_daily_activity_evidence |
| 6877 | 鏵友益 | Technology/Electronics | Other Electronics | 139.5000 | 206,626,514.00 | 4.2502 | 1 | 90.0000 | True | moderate_daily_activity_evidence |
| 3055 | 蔚華科 | Technology/Electronics | Electronic Distribution | 99.9000 | 282,234,600.00 | 1.2398 | 1 | 80.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 3441 | 聯一光電 | Technology/Electronics | Optoelectronics | 90.3000 | 203,178,678.00 | 0.3358 | 1 | 80.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 5011 | 久陽 | Materials | Steel | 24.8500 | 162,308,341.00 | 3.9995 | 1 | 80.0000 | True | moderate_daily_activity_evidence |
| 3360 | 尚立 | Technology/Electronics | Electronic Distribution | 19.7000 | 105,533,423.00 | 3.5443 | 1 | 80.0000 | True | moderate_daily_activity_evidence |
| 6234 | 高僑 | Technology/Electronics | Optoelectronics | 50.4000 | 195,963,217.00 | 1.6246 | 1 | 70.0000 | True | moderate_daily_activity_evidence |

## Execution Caveats

- These are paper orders, not live trade instructions.
- The strategy assumes a Day0 close entry, but closed limit-up stocks may have no executable sell liquidity.
- Do not mark a paper order as filled unless broker/order-book or auction records support the fill.
- The planned exit is the next trading day's open; no intraday stop or discretionary exit is modeled here.
- Profile warning: Broad BOTH-market challenger; TWSE sector coverage may be incomplete.
- Profile warning: Paper only until Day0 close fills are verified.

## Next-Day Evaluation Template

When Day1 data becomes available, compare each paper order against:

- actual Day1 open
- theoretical exit price
- gross return
- net return after normal overnight Taiwan stock costs
- whether paper fill was assumed
- whether actual broker would have filled if known
