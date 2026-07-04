# Closed-Limit-Up Paper Signals - 2026-07-02

## Execution Warning

Paper trade only. Day0 close limit-up fills may not be executable without auction/order-book confirmation.

## Strategy Parameters

| capital_twd | profile_name | candidate_hash | market | market_regime_filter | ranking_method | min_turnover_twd | min_volume_ratio_20d | min_fill_quality_score | max_consecutive_limit_ups | min_price | max_price | prior_5d_return_max | prior_20d_return_max | max_positions | target_notional_twd | max_notional_per_symbol_pct | max_notional_per_sector_pct | max_notional_per_industry_pct | board_lot_size | avoid_sectors | allowed_sectors | warnings | commission_rate | commission_discount | slippage_bps_per_side | minimum_commission_twd |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1,000,000.00 | broad_challenger_097dd332 | 097dd332 | BOTH | not_bear | fill_quality_score | 200,000,000.00 | 1.5000 | 60.0000 | 3 | 10.0000 | 100.0000 |  |  | 5 | 300,000.00 | 0.2000 | 1.0000 | 1.0000 | 1000 | ('Healthcare', 'Materials') | () | ('Broad BOTH-market challenger; TWSE sector coverage may be incomplete.', 'Paper only until Day0 close fills are verified.') | 0.0014 | 0.2800 | 5.0000 | 20.0000 |

## Market Date

Signal date: `2026-07-02`
Strategy universe: `BOTH`
Profile: `broad_challenger_097dd332`
Candidate hash: `097dd332`

## Candidate Counts

Raw closed-limit-up candidates: 90
Candidates after strategy filters: 6
Planned paper orders: 5

## Planned Paper Orders

| profile_name | candidate_hash | signal_date | symbol | name | market | sector | industry | close_price | limit_up_price | turnover_twd | volume_ratio_20d | consecutive_limit_up_count | fill_quality_score | planned_entry_price | planned_exit | target_notional_twd | capped_notional_twd | planned_shares | planned_buy_notional_twd | estimated_buy_cost | estimated_cash_required | ranking | notes | execution_warning |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| broad_challenger_097dd332 | 097dd332 | 2026-07-02 | 2302 | 麗正 | TWSE | Technology/Electronics | Semiconductor | 55.4000 | 55.0000 | 968,370,500.00 | 2.2200 | 1 | 100.0000 | 55.4000 | Day1 open | 300,000.00 | 200,000.00 | 3000 | 166,200.00 | 149.4138 | 166,349.41 | 1 | Ranked by fill_quality_score. Moderate fill proxy reason: moderate_daily_activity_evidence | Paper trade only. Day0 close limit-up fills may not be executable without auction/order-book confirmation. |
| broad_challenger_097dd332 | 097dd332 | 2026-07-02 | 3317 | 尼克森 | TPEX | Technology/Electronics | Semiconductor | 97.3000 | 97.0000 | 905,416,998.00 | 1.7469 | 1 | 100.0000 | 97.3000 | Day1 open | 300,000.00 | 200,000.00 | 2000 | 194,600.00 | 174.9454 | 194,774.95 | 2 | Ranked by fill_quality_score. Moderate fill proxy reason: moderate_daily_activity_evidence | Paper trade only. Day0 close limit-up fills may not be executable without auction/order-book confirmation. |
| broad_challenger_097dd332 | 097dd332 | 2026-07-02 | 6133 | 金橋 | TWSE | Technology/Electronics | Electronic Components | 26.2500 | 26.2000 | 222,146,950.00 | 5.0175 | 1 | 90.0000 | 26.2500 | Day1 open | 300,000.00 | 200,000.00 | 7000 | 183,750.00 | 165.1913 | 183,915.19 | 3 | Ranked by fill_quality_score. Moderate fill proxy reason: moderate_daily_activity_evidence | Paper trade only. Day0 close limit-up fills may not be executable without auction/order-book confirmation. |
| broad_challenger_097dd332 | 097dd332 | 2026-07-02 | 2484 | 希華 | TWSE | Technology/Electronics | Electronic Components | 87.7000 | 87.5000 | 5,827,996,100.00 | 1.5430 | 2 | 80.0000 | 87.7000 | Day1 open | 300,000.00 | 200,000.00 | 2000 | 175,400.00 | 157.6846 | 175,557.68 | 4 | Ranked by fill_quality_score. Moderate fill proxy reason: moderate_daily_activity_evidence | Paper trade only. Day0 close limit-up fills may not be executable without auction/order-book confirmation. |
| broad_challenger_097dd332 | 097dd332 | 2026-07-02 | 1515 | 力山 | TWSE | Industrials/Other | Electric Machinery | 38.9500 | 38.9000 | 285,052,750.00 | 2.5221 | 2 | 70.0000 | 38.9500 | Day1 open | 300,000.00 | 200,000.00 | 5000 | 194,750.00 | 175.0803 | 194,925.08 | 5 | Ranked by fill_quality_score. Moderate fill proxy reason: moderate_daily_activity_evidence | Paper trade only. Day0 close limit-up fills may not be executable without auction/order-book confirmation. |

## Skipped Reasons

| reason | count |
| --- | --- |
| turnover_below_500m | 42 |
| volume_ratio_below_1_5 | 20 |
| price_outside_10_to_100 | 18 |
| avoided_weak_sector | 2 |
| too_many_consecutive_limit_ups | 2 |
| max_positions | 1 |

## Candidate Diagnostics

| symbol | name | sector | industry | day0_close | day0_turnover_twd | volume_ratio_20d | consecutive_limit_up_count | fill_quality_score | fillable_moderate | moderate_fill_reason |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 3661 | 世芯-KY | Technology/Electronics | Semiconductor | 4,895.00 | 18,076,335,000.00 | 2.3007 | 1 | 100.0000 | True | moderate_daily_activity_evidence |
| 1301 | 台塑 | Materials | Plastics | 59.7000 | 6,517,259,300.00 | 2.2006 | 1 | 100.0000 | True | moderate_daily_activity_evidence |
| 1326 | 台化 | Materials | Plastics | 61.9000 | 3,875,321,900.00 | 1.6675 | 1 | 100.0000 | True | moderate_daily_activity_evidence |
| 3016 | 嘉晶 | Technology/Electronics | Semiconductor | 134.0000 | 3,646,874,500.00 | 2.3635 | 1 | 100.0000 | True | moderate_daily_activity_evidence |
| 4989 | 榮科 | Technology/Electronics | Electronic Components | 100.5000 | 2,334,826,600.00 | 3.4541 | 1 | 100.0000 | True | moderate_daily_activity_evidence |
| 2481 | 強茂 | Technology/Electronics | Semiconductor | 213.5000 | 2,330,693,000.00 | 0.3590 | 1 | 100.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 4919 | 新唐 | Technology/Electronics | Semiconductor | 185.0000 | 2,178,073,500.00 | 0.9225 | 1 | 100.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 2486 | 一詮 | Technology/Electronics | Optoelectronics | 254.0000 | 2,069,542,500.00 | 0.9550 | 1 | 100.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 6138 | 茂達 | Technology/Electronics | Semiconductor | 418.5000 | 1,850,819,827.00 | 2.7872 | 1 | 100.0000 | True | moderate_daily_activity_evidence |
| 3289 | 宜特 | Technology/Electronics | Other Electronics | 190.5000 | 1,831,626,225.00 | 2.9937 | 1 | 100.0000 | True | moderate_daily_activity_evidence |
| 6435 | 大中 | Technology/Electronics | Semiconductor | 392.5000 | 1,667,143,231.00 | 3.6030 | 1 | 100.0000 | True | moderate_daily_activity_evidence |
| 5439 | 高技 | Technology/Electronics | Electronic Components | 348.0000 | 1,572,761,040.00 | 2.0897 | 1 | 100.0000 | True | moderate_daily_activity_evidence |
| 5425 | 台半 | Technology/Electronics | Semiconductor | 133.5000 | 1,470,012,007.00 | 0.4252 | 1 | 100.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 2359 | 所羅門 | Technology/Electronics | Other Electronics | 146.5000 | 1,436,489,000.00 | 2.7815 | 1 | 100.0000 | True | moderate_daily_activity_evidence |
| 6548 | 長科* | Technology/Electronics | Semiconductor | 90.6000 | 1,088,019,118.00 | 1.0920 | 1 | 100.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 6215 | 和椿 | Technology/Electronics | Other Electronics | 118.0000 | 1,080,026,000.00 | 6.4787 | 1 | 100.0000 | True | moderate_daily_activity_evidence |
| 1409 | 新纖 | Consumer/Services | Textiles | 28.2000 | 1,005,371,750.00 | 1.3884 | 1 | 100.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 3673 | TPK-KY | Technology/Electronics | Optoelectronics | 83.8000 | 996,734,000.00 | 0.5643 | 1 | 100.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 1708 | 東鹼 | Materials | Chemical | 57.0000 | 988,617,900.00 | 1.1814 | 1 | 100.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 2302 | 麗正 | Technology/Electronics | Semiconductor | 55.4000 | 968,370,500.00 | 2.2200 | 1 | 100.0000 | True | moderate_daily_activity_evidence |
| 3317 | 尼克森 | Technology/Electronics | Semiconductor | 97.3000 | 905,416,998.00 | 1.7469 | 1 | 100.0000 | True | moderate_daily_activity_evidence |
| 1718 | 中纖 | Materials | Chemical | 13.6000 | 847,845,000.00 | 1.1687 | 1 | 100.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 2401 | 凌陽 | Technology/Electronics | Semiconductor | 34.3000 | 641,206,300.00 | 1.2540 | 1 | 100.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 4576 | 大銀微系統 | Industrials/Other | Electric Machinery | 235.0000 | 641,131,000.00 | 1.5086 | 1 | 100.0000 | True | moderate_daily_activity_evidence |
| 6525 | 捷敏-KY | Technology/Electronics | Semiconductor | 166.5000 | 600,036,500.00 | 0.9460 | 1 | 100.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 3135 | 凌航 | Technology/Electronics | Semiconductor | 183.5000 | 592,157,500.00 | 1.1371 | 1 | 100.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 3455 | 由田 | Technology/Electronics | Optoelectronics | 260.0000 | 579,072,501.00 | 1.1970 | 1 | 100.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 3042 | 晶技 | Technology/Electronics | Electronic Components | 226.5000 | 15,979,970,500.00 | 2.0928 | 2 | 90.0000 | True | moderate_daily_activity_evidence |
| 8261 | 富鼎 | Technology/Electronics | Semiconductor | 327.0000 | 9,030,334,000.00 | 2.4694 | 3 | 90.0000 | True | moderate_daily_activity_evidence |
| 3055 | 蔚華科 | Technology/Electronics | Electronic Distribution | 140.0000 | 786,632,500.00 | 2.5960 | 4 | 90.0000 | True | moderate_daily_activity_evidence |

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
