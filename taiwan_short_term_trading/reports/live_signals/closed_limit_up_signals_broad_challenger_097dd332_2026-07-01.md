# Closed-Limit-Up Paper Signals - 2026-07-01

## Execution Warning

Paper trade only. Day0 close limit-up fills may not be executable without auction/order-book confirmation.

## Strategy Parameters

| capital_twd | profile_name | candidate_hash | market | market_regime_filter | ranking_method | min_turnover_twd | min_volume_ratio_20d | min_fill_quality_score | max_consecutive_limit_ups | min_price | max_price | prior_5d_return_max | prior_20d_return_max | max_positions | target_notional_twd | max_notional_per_symbol_pct | max_notional_per_sector_pct | max_notional_per_industry_pct | board_lot_size | avoid_sectors | allowed_sectors | warnings | commission_rate | commission_discount | slippage_bps_per_side | minimum_commission_twd |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1,000,000.00 | broad_challenger_097dd332 | 097dd332 | BOTH | not_bear | fill_quality_score | 200,000,000.00 | 1.5000 | 60.0000 | 3 | 10.0000 | 100.0000 |  |  | 5 | 300,000.00 | 0.2000 | 1.0000 | 1.0000 | 1000 | ('Healthcare', 'Materials') | () | ('Broad BOTH-market challenger; TWSE sector coverage may be incomplete.', 'Paper only until Day0 close fills are verified.') | 0.0014 | 0.2800 | 5.0000 | 20.0000 |

## Market Date

Signal date: `2026-07-01`
Strategy universe: `BOTH`
Profile: `broad_challenger_097dd332`
Candidate hash: `097dd332`

## Candidate Counts

Raw closed-limit-up candidates: 69
Candidates after strategy filters: 6
Planned paper orders: 5

## Planned Paper Orders

| profile_name | candidate_hash | signal_date | symbol | name | market | sector | industry | close_price | limit_up_price | turnover_twd | volume_ratio_20d | consecutive_limit_up_count | fill_quality_score | planned_entry_price | planned_exit | target_notional_twd | capped_notional_twd | planned_shares | planned_buy_notional_twd | estimated_buy_cost | estimated_cash_required | ranking | notes | execution_warning |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| broad_challenger_097dd332 | 097dd332 | 2026-07-01 | 3605 | 宏致 | TWSE | Technology/Electronics | Electronic Components | 85.6000 | 85.5000 | 1,205,798,807.00 | 5.4672 | 1 | 100.0000 | 85.6000 | Day1 open | 300,000.00 | 200,000.00 | 2000 | 171,200.00 | 153.9088 | 171,353.91 | 1 | Ranked by fill_quality_score. Moderate fill proxy reason: moderate_daily_activity_evidence | Paper trade only. Day0 close limit-up fills may not be executable without auction/order-book confirmation. |
| broad_challenger_097dd332 | 097dd332 | 2026-07-01 | 8182 | 加高 | TPEX | Technology/Electronics | Electronic Components | 54.0000 | 54.0000 | 627,094,588.00 | 1.5119 | 1 | 100.0000 | 54.0000 | Day1 open | 300,000.00 | 200,000.00 | 3000 | 162,000.00 | 145.6380 | 162,145.64 | 2 | Ranked by fill_quality_score. Moderate fill proxy reason: moderate_daily_activity_evidence | Paper trade only. Day0 close limit-up fills may not be executable without auction/order-book confirmation. |
| broad_challenger_097dd332 | 097dd332 | 2026-07-01 | 1515 | 力山 | TWSE | Industrials/Other | Electric Machinery | 35.4500 | 35.4000 | 212,638,091.00 | 2.2517 | 1 | 90.0000 | 35.4500 | Day1 open | 300,000.00 | 200,000.00 | 5000 | 177,250.00 | 159.3478 | 177,409.35 | 3 | Ranked by fill_quality_score. Moderate fill proxy reason: moderate_daily_activity_evidence | Paper trade only. Day0 close limit-up fills may not be executable without auction/order-book confirmation. |
| broad_challenger_097dd332 | 097dd332 | 2026-07-01 | 6909 | 創控 | TWSE | Technology/Electronics | Semiconductor | 60.6000 | 60.5000 | 210,139,975.00 | 2.6726 | 1 | 90.0000 | 60.6000 | Day1 open | 300,000.00 | 200,000.00 | 3000 | 181,800.00 | 163.4382 | 181,963.44 | 4 | Ranked by fill_quality_score. Moderate fill proxy reason: moderate_daily_activity_evidence | Paper trade only. Day0 close limit-up fills may not be executable without auction/order-book confirmation. |
| broad_challenger_097dd332 | 097dd332 | 2026-07-01 | 6224 | 聚鼎 | TWSE | Technology/Electronics | Electronic Components | 84.9000 | 84.5000 | 373,920,364.00 | 2.5978 | 2 | 70.0000 | 84.9000 | Day1 open | 300,000.00 | 200,000.00 | 2000 | 169,800.00 | 152.6502 | 169,952.65 | 5 | Ranked by fill_quality_score. Moderate fill proxy reason: moderate_daily_activity_evidence | Paper trade only. Day0 close limit-up fills may not be executable without auction/order-book confirmation. |

## Skipped Reasons

| reason | count |
| --- | --- |
| turnover_below_500m | 34 |
| volume_ratio_below_1_5 | 17 |
| price_outside_10_to_100 | 12 |
| max_positions | 1 |

## Candidate Diagnostics

| symbol | name | sector | industry | day0_close | day0_turnover_twd | volume_ratio_20d | consecutive_limit_up_count | fill_quality_score | fillable_moderate | moderate_fill_reason |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2464 | 盟立 | Technology/Electronics | Other Electronics | 190.0000 | 6,402,570,495.00 | 2.6749 | 1 | 100.0000 | True | moderate_daily_activity_evidence |
| 6223 | 旺矽 | Technology/Electronics | Semiconductor | 6,695.00 | 4,724,904,340.00 | 0.6370 | 1 | 100.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 3042 | 晶技 | Technology/Electronics | Electronic Components | 206.0000 | 2,897,669,921.00 | 0.3957 | 1 | 100.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 3680 | 家登 | Technology/Electronics | Semiconductor | 558.0000 | 2,871,604,757.00 | 2.9476 | 1 | 100.0000 | True | moderate_daily_activity_evidence |
| 6515 | 穎崴 | Technology/Electronics | Semiconductor | 8,890.00 | 2,386,375,220.00 | 0.6830 | 1 | 100.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 6719 | 力智 | Technology/Electronics | Semiconductor | 297.0000 | 2,229,901,022.00 | 2.0552 | 1 | 100.0000 | True | moderate_daily_activity_evidence |
| 4916 | 事欣科 | Technology/Electronics | Computer/Peripheral | 112.5000 | 1,818,126,084.00 | 2.1380 | 1 | 100.0000 | True | moderate_daily_activity_evidence |
| 3221 | 台嘉碩 | Technology/Electronics | Communications/Internet | 64.4000 | 1,299,935,460.00 | 1.3819 | 1 | 100.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 2645 | 長榮航太 | Industrials/Other | Shipping/Transportation | 204.0000 | 1,213,013,540.00 | 3.4893 | 1 | 100.0000 | True | moderate_daily_activity_evidence |
| 3605 | 宏致 | Technology/Electronics | Electronic Components | 85.6000 | 1,205,798,807.00 | 5.4672 | 1 | 100.0000 | True | moderate_daily_activity_evidence |
| 8182 | 加高 | Technology/Electronics | Electronic Components | 54.0000 | 627,094,588.00 | 1.5119 | 1 | 100.0000 | True | moderate_daily_activity_evidence |
| 5483 | 中美晶 | Technology/Electronics | Semiconductor | 198.0000 | 10,101,975,996.00 | 2.7402 | 2 | 90.0000 | True | moderate_daily_activity_evidence |
| 2375 | 凱美 | Technology/Electronics | Electronic Components | 232.5000 | 6,625,469,571.00 | 2.1219 | 2 | 90.0000 | True | moderate_daily_activity_evidence |
| 8033 | 雷虎 | Industrials/Other | Other | 186.0000 | 4,768,588,467.00 | 4.7222 | 3 | 90.0000 | True | moderate_daily_activity_evidence |
| 2484 | 希華 | Technology/Electronics | Electronic Components | 79.8000 | 1,317,771,935.00 | 0.3689 | 1 | 90.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 7402 | 邑錡 | Technology/Electronics | Optoelectronics | 126.5000 | 298,432,822.00 | 4.1798 | 1 | 90.0000 | True | moderate_daily_activity_evidence |
| 2236 | 百達-KY | Consumer/Services | Automobile | 161.0000 | 266,206,060.00 | 4.3475 | 1 | 90.0000 | True | moderate_daily_activity_evidence |
| 1515 | 力山 | Industrials/Other | Electric Machinery | 35.4500 | 212,638,091.00 | 2.2517 | 1 | 90.0000 | True | moderate_daily_activity_evidence |
| 6909 | 創控 | Technology/Electronics | Semiconductor | 60.6000 | 210,139,975.00 | 2.6726 | 1 | 90.0000 | True | moderate_daily_activity_evidence |
| 1303 | 南亞 | Materials | Plastics | 183.0000 | 8,391,316,069.00 | 0.6094 | 2 | 80.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 8043 | 蜜望實 | Technology/Electronics | Electronic Components | 213.0000 | 4,273,624,364.00 | 0.9177 | 2 | 80.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 5328 | 華容 | Technology/Electronics | Electronic Components | 78.2000 | 1,961,825,279.00 | 0.6387 | 2 | 80.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 6207 | 雷科 | Technology/Electronics | Electronic Components | 161.0000 | 1,348,887,462.00 | 0.4673 | 2 | 80.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 6658 | 聯策 | Technology/Electronics | Other Electronics | 257.0000 | 673,244,085.00 | 1.9096 | 2 | 80.0000 | True | moderate_daily_activity_evidence |
| 3055 | 蔚華科 | Technology/Electronics | Electronic Distribution | 127.5000 | 603,787,803.00 | 2.1544 | 3 | 80.0000 | True | moderate_daily_activity_evidence |
| 6640 | 均華 | Technology/Electronics | Semiconductor | 1,125.00 | 278,324,080.00 | 0.8487 | 1 | 80.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 6488 | 環球晶 | Technology/Electronics | Semiconductor | 1,105.00 | 4,250,201,645.00 | 0.9453 | 2 | 70.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 8261 | 富鼎 | Technology/Electronics | Semiconductor | 297.5000 | 3,702,568,039.00 | 1.0749 | 2 | 70.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 6173 | 信昌電 | Technology/Electronics | Electronic Components | 341.5000 | 1,040,480,111.00 | 0.4881 | 2 | 70.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 6224 | 聚鼎 | Technology/Electronics | Electronic Components | 84.9000 | 373,920,364.00 | 2.5978 | 2 | 70.0000 | True | moderate_daily_activity_evidence |

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
