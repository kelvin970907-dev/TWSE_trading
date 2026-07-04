# Closed-Limit-Up Paper Signals - 2026-07-01

## Execution Warning

Paper trade only. Day0 close limit-up fills may not be executable without auction/order-book confirmation.

## Strategy Parameters

| capital_twd | profile_name | candidate_hash | market | market_regime_filter | ranking_method | min_turnover_twd | min_volume_ratio_20d | min_fill_quality_score | max_consecutive_limit_ups | min_price | max_price | prior_5d_return_max | prior_20d_return_max | max_positions | target_notional_twd | max_notional_per_symbol_pct | max_notional_per_sector_pct | max_notional_per_industry_pct | board_lot_size | avoid_sectors | allowed_sectors | warnings | commission_rate | commission_discount | slippage_bps_per_side | minimum_commission_twd |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1,000,000.00 | original_champion_tpex_500m |  | TPEX | none | fill_quality_score | 500,000,000.00 | 1.5000 | 60.0000 | 3 | 10.0000 | 100.0000 |  |  | 5 | 300,000.00 | 0.2000 | 1.0000 | 1.0000 | 1000 | ('Healthcare', 'Materials') | () | ('Original conservative paper champion; execution is still unverified.',) | 0.0014 | 0.2800 | 5.0000 | 20.0000 |

## Market Date

Signal date: `2026-07-01`
Strategy universe: `TPEX`
Profile: `original_champion_tpex_500m`
Candidate hash: ``

## Candidate Counts

Raw closed-limit-up candidates: 34
Candidates after strategy filters: 1
Planned paper orders: 1

## Planned Paper Orders

| profile_name | candidate_hash | signal_date | symbol | name | market | sector | industry | close_price | limit_up_price | turnover_twd | volume_ratio_20d | consecutive_limit_up_count | fill_quality_score | planned_entry_price | planned_exit | target_notional_twd | capped_notional_twd | planned_shares | planned_buy_notional_twd | estimated_buy_cost | estimated_cash_required | ranking | notes | execution_warning |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| original_champion_tpex_500m |  | 2026-07-01 | 8182 | 加高 | TPEX | Technology/Electronics | Electronic Components | 54.0000 | 54.0000 | 627,094,588.00 | 1.5119 | 1 | 100.0000 | 54.0000 | Day1 open | 300,000.00 | 200,000.00 | 3000 | 162,000.00 | 145.6380 | 162,145.64 | 1 | Ranked by fill_quality_score. Moderate fill proxy reason: moderate_daily_activity_evidence | Paper trade only. Day0 close limit-up fills may not be executable without auction/order-book confirmation. |

## Skipped Reasons

| reason | count |
| --- | --- |
| turnover_below_500m | 23 |
| volume_ratio_below_1_5 | 8 |
| price_outside_10_to_100 | 2 |

## Candidate Diagnostics

| symbol | name | sector | industry | day0_close | day0_turnover_twd | volume_ratio_20d | consecutive_limit_up_count | fill_quality_score | fillable_moderate | moderate_fill_reason |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 6223 | 旺矽 | Technology/Electronics | Semiconductor | 6,695.00 | 4,724,904,340.00 | 0.6370 | 1 | 100.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 3680 | 家登 | Technology/Electronics | Semiconductor | 558.0000 | 2,871,604,757.00 | 2.9476 | 1 | 100.0000 | True | moderate_daily_activity_evidence |
| 3221 | 台嘉碩 | Technology/Electronics | Communications/Internet | 64.4000 | 1,299,935,460.00 | 1.3819 | 1 | 100.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 8182 | 加高 | Technology/Electronics | Electronic Components | 54.0000 | 627,094,588.00 | 1.5119 | 1 | 100.0000 | True | moderate_daily_activity_evidence |
| 5483 | 中美晶 | Technology/Electronics | Semiconductor | 198.0000 | 10,101,975,996.00 | 2.7402 | 2 | 90.0000 | True | moderate_daily_activity_evidence |
| 7402 | 邑錡 | Technology/Electronics | Optoelectronics | 126.5000 | 298,432,822.00 | 4.1798 | 1 | 90.0000 | True | moderate_daily_activity_evidence |
| 8043 | 蜜望實 | Technology/Electronics | Electronic Components | 213.0000 | 4,273,624,364.00 | 0.9177 | 2 | 80.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 5328 | 華容 | Technology/Electronics | Electronic Components | 78.2000 | 1,961,825,279.00 | 0.6387 | 2 | 80.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 6207 | 雷科 | Technology/Electronics | Electronic Components | 161.0000 | 1,348,887,462.00 | 0.4673 | 2 | 80.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 6640 | 均華 | Technology/Electronics | Semiconductor | 1,125.00 | 278,324,080.00 | 0.8487 | 1 | 80.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 6488 | 環球晶 | Technology/Electronics | Semiconductor | 1,105.00 | 4,250,201,645.00 | 0.9453 | 2 | 70.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 6173 | 信昌電 | Technology/Electronics | Electronic Components | 341.5000 | 1,040,480,111.00 | 0.4881 | 2 | 70.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 6684 | 安格 | Technology/Electronics | Semiconductor | 64.5000 | 165,495,406.00 | 1.7961 | 1 | 70.0000 | True | moderate_daily_activity_evidence |
| 6259 | 百徽 | Technology/Electronics | Electronic Components | 39.3500 | 148,892,149.00 | 1.6693 | 1 | 70.0000 | True | moderate_daily_activity_evidence |
| 5426 | 振發 | Technology/Electronics | Computer/Peripheral | 34.4000 | 111,271,216.00 | 0.7788 | 1 | 70.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 6603 | 富強鑫 | Industrials/Other | Electric Machinery | 25.1500 | 74,323,478.00 | 2.1769 | 1 | 70.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 3466 | 德晉 | Technology/Electronics | Communications/Internet | 32.9000 | 42,880,920.00 | 2.7296 | 1 | 70.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 5460 | 同協 | Technology/Electronics | Electronic Components | 17.2500 | 18,850,503.00 | 5.9759 | 1 | 70.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 8176 | 智捷 | Technology/Electronics | Communications/Internet | 10.9500 | 14,302,881.00 | 5.7585 | 1 | 70.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 3349 | 寶德 | Technology/Electronics | Computer/Peripheral | 20.0000 | 9,755,810.00 | 2.1798 | 1 | 70.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 3521 | 台鋼建設 | Industrials/Other | Building Materials/Construction | 13.3000 | 6,580,630.00 | 4.8973 | 1 | 70.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 3672 | 康聯訊 | Technology/Electronics | Communications/Internet | 12.8500 | 5,594,393.00 | 5.3664 | 1 | 70.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 6870 | 騰雲 | Technology/Electronics | Digital/Cloud | 289.5000 | 56,525,522.00 | 0.8156 | 1 | 60.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 3490 | 單井 | Technology/Electronics | Optoelectronics | 33.9000 | 55,608,340.00 | 1.7850 | 1 | 60.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 3577 | 泓格 | Technology/Electronics | Computer/Peripheral | 130.0000 | 48,570,964.00 | 0.8118 | 1 | 60.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 6877 | 鏵友益 | Technology/Electronics | Other Electronics | 134.0000 | 34,407,302.00 | 0.5860 | 1 | 60.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 3520 | 華盈 | Technology/Electronics | Electronic Components | 17.2500 | 15,268,617.00 | 0.5701 | 1 | 60.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 5345 | 馥鴻 | Industrials/Other | Other | 29.3500 | 3,131,703.00 | 0.7304 | 1 | 60.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 3664 | 安瑞-KY | Technology/Electronics | Communications/Internet | 7.7000 | 824,683.00 | 3.2732 | 1 | 60.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 6617 | 共信-KY | Healthcare | Biotechnology/Medical | 76.6000 | 83,155,718.00 | 6.0204 | 2 | 50.0000 | False | failed_moderate_turnover_volume_range_or_close_location |

## Execution Caveats

- These are paper orders, not live trade instructions.
- The strategy assumes a Day0 close entry, but closed limit-up stocks may have no executable sell liquidity.
- Do not mark a paper order as filled unless broker/order-book or auction records support the fill.
- The planned exit is the next trading day's open; no intraday stop or discretionary exit is modeled here.
- Profile warning: Original conservative paper champion; execution is still unverified.

## Next-Day Evaluation Template

When Day1 data becomes available, compare each paper order against:

- actual Day1 open
- theoretical exit price
- gross return
- net return after normal overnight Taiwan stock costs
- whether paper fill was assumed
- whether actual broker would have filled if known
