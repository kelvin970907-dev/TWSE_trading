# Closed-Limit-Up Paper Signals - 2026-07-03

## Execution Warning

Paper trade only. Day0 close limit-up fills may not be executable without auction/order-book confirmation.

## Strategy Parameters

| capital_twd | profile_name | candidate_hash | market | market_regime_filter | ranking_method | min_turnover_twd | min_volume_ratio_20d | min_fill_quality_score | max_consecutive_limit_ups | min_price | max_price | prior_5d_return_max | prior_20d_return_max | max_positions | target_notional_twd | max_notional_per_symbol_pct | max_notional_per_sector_pct | max_notional_per_industry_pct | board_lot_size | avoid_sectors | allowed_sectors | warnings | commission_rate | commission_discount | slippage_bps_per_side | minimum_commission_twd |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1,000,000.00 | conservative_tpex_35adc734 | 35adc734 | TPEX | avoid_weak_day | fill_quality_score | 300,000,000.00 | 1.2000 | 60.0000 | 3 | 10.0000 | 100.0000 | 0.3000 | 0.8000 | 5 | 300,000.00 | 0.2000 | 1.0000 | 0.2500 | 1000 | ('Healthcare', 'Materials') | () | ('Conservative TPEX finalist from focused expansion.', 'Uses avoid-weak-day and prior momentum cap from exact audit.') | 0.0014 | 0.2800 | 5.0000 | 20.0000 |

## Market Date

Signal date: `2026-07-03`
Strategy universe: `TPEX`
Profile: `conservative_tpex_35adc734`
Candidate hash: `35adc734`

## Candidate Counts

Raw closed-limit-up candidates: 36
Candidates after strategy filters: 5
Planned paper orders: 5

## Planned Paper Orders

| profile_name | candidate_hash | signal_date | symbol | name | market | sector | industry | close_price | limit_up_price | turnover_twd | volume_ratio_20d | consecutive_limit_up_count | fill_quality_score | planned_entry_price | planned_exit | target_notional_twd | capped_notional_twd | planned_shares | planned_buy_notional_twd | estimated_buy_cost | estimated_cash_required | ranking | notes | execution_warning |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| conservative_tpex_35adc734 | 35adc734 | 2026-07-03 | 5371 | 中光電 | TPEX | Technology/Electronics | Optoelectronics | 93.1000 | 93.0000 | 3,707,330,181.00 | 3.3761 | 1 | 100.0000 | 93.1000 | Day1 open | 300,000.00 | 200,000.00 | 2000 | 186,200.00 | 167.3938 | 186,367.39 | 1 | Ranked by fill_quality_score. Moderate fill proxy reason: moderate_daily_activity_evidence | Paper trade only. Day0 close limit-up fills may not be executable without auction/order-book confirmation. |
| conservative_tpex_35adc734 | 35adc734 | 2026-07-03 | 5314 | 世紀* | TPEX | Industrials/Other | Other | 71.5000 | 71.5000 | 2,095,578,931.00 | 6.0051 | 1 | 100.0000 | 71.5000 | Day1 open | 300,000.00 | 200,000.00 | 2000 | 143,000.00 | 128.5570 | 143,128.56 | 2 | Ranked by fill_quality_score. Moderate fill proxy reason: moderate_daily_activity_evidence | Paper trade only. Day0 close limit-up fills may not be executable without auction/order-book confirmation. |
| conservative_tpex_35adc734 | 35adc734 | 2026-07-03 | 3663 | 鑫科 | TPEX | Technology/Electronics | Other Electronics | 88.4000 | 88.0000 | 961,006,201.00 | 3.8614 | 1 | 100.0000 | 88.4000 | Day1 open | 300,000.00 | 200,000.00 | 2000 | 176,800.00 | 158.9432 | 176,958.94 | 3 | Ranked by fill_quality_score. Moderate fill proxy reason: moderate_daily_activity_evidence | Paper trade only. Day0 close limit-up fills may not be executable without auction/order-book confirmation. |
| conservative_tpex_35adc734 | 35adc734 | 2026-07-03 | 6174 | 安碁 | TPEX | Technology/Electronics | Electronic Components | 61.8000 | 61.5000 | 540,634,148.00 | 1.8054 | 1 | 100.0000 | 61.8000 | Day1 open | 300,000.00 | 200,000.00 | 3000 | 185,400.00 | 166.6746 | 185,566.67 | 4 | Ranked by fill_quality_score. Moderate fill proxy reason: moderate_daily_activity_evidence | Paper trade only. Day0 close limit-up fills may not be executable without auction/order-book confirmation. |
| conservative_tpex_35adc734 | 35adc734 | 2026-07-03 | 4541 | 晟田 | TPEX | Industrials/Other | Other | 60.7000 | 60.5000 | 378,590,968.00 | 2.8621 | 1 | 90.0000 | 60.7000 | Day1 open | 300,000.00 | 107,000.00 | 1000 | 60,700.00 | 54.5693 | 60,754.57 | 5 | Ranked by fill_quality_score. Moderate fill proxy reason: moderate_daily_activity_evidence | Paper trade only. Day0 close limit-up fills may not be executable without auction/order-book confirmation. |

## Skipped Reasons

| reason | count |
| --- | --- |
| turnover_below_500m | 22 |
| price_outside_10_to_100 | 6 |
| avoided_weak_sector | 2 |
| too_many_consecutive_limit_ups | 1 |

## Candidate Diagnostics

| symbol | name | sector | industry | day0_close | day0_turnover_twd | volume_ratio_20d | consecutive_limit_up_count | fill_quality_score | fillable_moderate | moderate_fill_reason |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 6173 | 信昌電 | Technology/Electronics | Electronic Components | 350.5000 | 8,280,673,931.00 | 3.4463 | 1 | 100.0000 | True | moderate_daily_activity_evidence |
| 5371 | 中光電 | Technology/Electronics | Optoelectronics | 93.1000 | 3,707,330,181.00 | 3.3761 | 1 | 100.0000 | True | moderate_daily_activity_evidence |
| 3363 | 上詮 | Technology/Electronics | Communications/Internet | 581.0000 | 2,566,591,870.00 | 1.3333 | 1 | 100.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 4991 | 環宇-KY | Technology/Electronics | Semiconductor | 578.0000 | 2,406,553,929.00 | 1.3346 | 1 | 100.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 5314 | 世紀* | Industrials/Other | Other | 71.5000 | 2,095,578,931.00 | 6.0051 | 1 | 100.0000 | True | moderate_daily_activity_evidence |
| 5386 | 青雲 | Technology/Electronics | Computer/Peripheral | 420.5000 | 1,126,215,215.00 | 1.8931 | 1 | 100.0000 | True | moderate_daily_activity_evidence |
| 3663 | 鑫科 | Technology/Electronics | Other Electronics | 88.4000 | 961,006,201.00 | 3.8614 | 1 | 100.0000 | True | moderate_daily_activity_evidence |
| 7402 | 邑錡 | Technology/Electronics | Optoelectronics | 138.5000 | 666,303,125.00 | 5.3585 | 1 | 100.0000 | True | moderate_daily_activity_evidence |
| 4743 | 合一 | Healthcare | Biotechnology/Medical | 57.3000 | 637,530,108.00 | 5.1311 | 1 | 100.0000 | True | moderate_daily_activity_evidence |
| 6174 | 安碁 | Technology/Electronics | Electronic Components | 61.8000 | 540,634,148.00 | 1.8054 | 1 | 100.0000 | True | moderate_daily_activity_evidence |
| 6143 | 振曜 | Technology/Electronics | Communications/Internet | 106.5000 | 539,058,543.00 | 7.6040 | 1 | 100.0000 | True | moderate_daily_activity_evidence |
| 6182 | 合晶 | Technology/Electronics | Semiconductor | 163.0000 | 30,347,150,154.00 | 3.6452 | 4 | 90.0000 | True | moderate_daily_activity_evidence |
| 4541 | 晟田 | Industrials/Other | Other | 60.7000 | 378,590,968.00 | 2.8621 | 1 | 90.0000 | True | moderate_daily_activity_evidence |
| 4147 | 中裕 | Healthcare | Biotechnology/Medical | 70.0000 | 328,709,255.00 | 2.2566 | 1 | 90.0000 | True | moderate_daily_activity_evidence |
| 6732 | 昇佳電子 | Technology/Electronics | Semiconductor | 190.0000 | 227,823,070.00 | 6.0906 | 1 | 90.0000 | True | moderate_daily_activity_evidence |
| 6907 | 雅特力-KY | Technology/Electronics | Semiconductor | 170.5000 | 273,699,973.00 | 0.5889 | 1 | 80.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 4128 | 中天 | Healthcare | Biotechnology/Medical | 17.1500 | 138,856,043.00 | 3.9774 | 1 | 80.0000 | True | moderate_daily_activity_evidence |
| 8109 | 博大 | Technology/Electronics | Electronic Components | 140.5000 | 126,911,024.00 | 3.6324 | 1 | 80.0000 | True | moderate_daily_activity_evidence |
| 7717 | 萊德光電-KY | Technology/Electronics | Communications/Internet | 462.5000 | 145,342,983.00 | 0.7326 | 1 | 70.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 3294 | 英濟 | Technology/Electronics | Electronic Components | 40.4000 | 116,443,105.00 | 1.8102 | 1 | 70.0000 | True | moderate_daily_activity_evidence |
| 5490 | 同亨 | Technology/Electronics | Computer/Peripheral | 30.4500 | 91,131,340.00 | 5.9176 | 1 | 70.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 4109 | 加捷生醫 | Healthcare | Biotechnology/Medical | 12.8000 | 12,721,159.00 | 2.4420 | 1 | 70.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 3313 | 斐成 | Industrials/Other | Building Materials/Construction | 12.4000 | 9,987,363.00 | 2.5146 | 1 | 70.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 7820 | 立盈 | Industrials/Other | Green Energy/Environmental | 124.0000 | 9,564,707.00 | 2.2024 | 1 | 70.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 8092 | 建暐 | Technology/Electronics | Other Electronics | 14.7000 | 2,904,418.00 | 2.6815 | 1 | 70.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 5488 | 松普 | Technology/Electronics | Electronic Components | 15.4500 | 106,096,901.00 | 8.1073 | 2 | 60.0000 | True | moderate_daily_activity_evidence |
| 2061 | 風青 | Industrials/Other | Electrical Cable | 74.2000 | 56,805,910.00 | 0.3971 | 1 | 60.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 5302 | 太欣 | Technology/Electronics | Semiconductor | 14.0500 | 44,987,437.00 | 1.5655 | 1 | 60.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 6535 | 順藥 | Healthcare | Biotechnology/Medical | 107.5000 | 37,917,262.00 | 1.5933 | 1 | 60.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 3066 | 李洲 | Technology/Electronics | Optoelectronics | 26.5000 | 33,972,415.00 | 1.2028 | 1 | 60.0000 | False | failed_moderate_turnover_volume_range_or_close_location |

## Execution Caveats

- These are paper orders, not live trade instructions.
- The strategy assumes a Day0 close entry, but closed limit-up stocks may have no executable sell liquidity.
- Do not mark a paper order as filled unless broker/order-book or auction records support the fill.
- The planned exit is the next trading day's open; no intraday stop or discretionary exit is modeled here.
- Profile warning: Conservative TPEX finalist from focused expansion.
- Profile warning: Uses avoid-weak-day and prior momentum cap from exact audit.

## Next-Day Evaluation Template

When Day1 data becomes available, compare each paper order against:

- actual Day1 open
- theoretical exit price
- gross return
- net return after normal overnight Taiwan stock costs
- whether paper fill was assumed
- whether actual broker would have filled if known
