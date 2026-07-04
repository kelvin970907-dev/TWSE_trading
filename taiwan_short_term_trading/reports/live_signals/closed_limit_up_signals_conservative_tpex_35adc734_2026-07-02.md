# Closed-Limit-Up Paper Signals - 2026-07-02

## Execution Warning

Paper trade only. Day0 close limit-up fills may not be executable without auction/order-book confirmation.

## Strategy Parameters

| capital_twd | profile_name | candidate_hash | market | market_regime_filter | ranking_method | min_turnover_twd | min_volume_ratio_20d | min_fill_quality_score | max_consecutive_limit_ups | min_price | max_price | prior_5d_return_max | prior_20d_return_max | max_positions | target_notional_twd | max_notional_per_symbol_pct | max_notional_per_sector_pct | max_notional_per_industry_pct | board_lot_size | avoid_sectors | allowed_sectors | warnings | commission_rate | commission_discount | slippage_bps_per_side | minimum_commission_twd |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1,000,000.00 | conservative_tpex_35adc734 | 35adc734 | TPEX | avoid_weak_day | fill_quality_score | 300,000,000.00 | 1.2000 | 60.0000 | 3 | 10.0000 | 100.0000 | 0.3000 | 0.8000 | 5 | 300,000.00 | 0.2000 | 1.0000 | 0.2500 | 1000 | ('Healthcare', 'Materials') | () | ('Conservative TPEX finalist from focused expansion.', 'Uses avoid-weak-day and prior momentum cap from exact audit.') | 0.0014 | 0.2800 | 5.0000 | 20.0000 |

## Market Date

Signal date: `2026-07-02`
Strategy universe: `TPEX`
Profile: `conservative_tpex_35adc734`
Candidate hash: `35adc734`

## Candidate Counts

Raw closed-limit-up candidates: 34
Candidates after strategy filters: 0
Planned paper orders: 0

## Planned Paper Orders

_No rows._

## Skipped Reasons

| reason | count |
| --- | --- |
| turnover_below_500m | 21 |
| price_outside_10_to_100 | 7 |
| volume_ratio_below_1_5 | 4 |
| failed_moderate_fill_proxy | 1 |
| weak_market_day | 1 |

## Candidate Diagnostics

| symbol | name | sector | industry | day0_close | day0_turnover_twd | volume_ratio_20d | consecutive_limit_up_count | fill_quality_score | fillable_moderate | moderate_fill_reason |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 6138 | 茂達 | Technology/Electronics | Semiconductor | 418.5000 | 1,850,819,827.00 | 2.7872 | 1 | 100.0000 | True | moderate_daily_activity_evidence |
| 3289 | 宜特 | Technology/Electronics | Other Electronics | 190.5000 | 1,831,626,225.00 | 2.9937 | 1 | 100.0000 | True | moderate_daily_activity_evidence |
| 6435 | 大中 | Technology/Electronics | Semiconductor | 392.5000 | 1,667,143,231.00 | 3.6030 | 1 | 100.0000 | True | moderate_daily_activity_evidence |
| 5439 | 高技 | Technology/Electronics | Electronic Components | 348.0000 | 1,572,761,040.00 | 2.0897 | 1 | 100.0000 | True | moderate_daily_activity_evidence |
| 5425 | 台半 | Technology/Electronics | Semiconductor | 133.5000 | 1,470,012,007.00 | 0.4252 | 1 | 100.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 6548 | 長科* | Technology/Electronics | Semiconductor | 90.6000 | 1,088,019,118.00 | 1.0920 | 1 | 100.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 3317 | 尼克森 | Technology/Electronics | Semiconductor | 97.3000 | 905,416,998.00 | 1.7469 | 1 | 100.0000 | True | moderate_daily_activity_evidence |
| 3455 | 由田 | Technology/Electronics | Optoelectronics | 260.0000 | 579,072,501.00 | 1.1970 | 1 | 100.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 5299 | 杰力 | Technology/Electronics | Semiconductor | 136.0000 | 383,181,898.00 | 2.8970 | 1 | 90.0000 | True | moderate_daily_activity_evidence |
| 3485 | 敘豐 | Technology/Electronics | Electronic Components | 331.0000 | 270,306,206.00 | 2.1773 | 1 | 90.0000 | True | moderate_daily_activity_evidence |
| 6182 | 合晶 | Technology/Electronics | Semiconductor | 148.5000 | 11,578,217,641.00 | 1.5806 | 3 | 80.0000 | True | moderate_daily_activity_evidence |
| 7828 | 創新服務 | Technology/Electronics | Semiconductor | 2,320.00 | 388,043,975.00 | 0.6050 | 1 | 80.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 6739 | 竹陞科技 | Technology/Electronics | Other Electronics | 1,200.00 | 369,395,290.00 | 1.6436 | 1 | 80.0000 | True | moderate_daily_activity_evidence |
| 4707 | 磐亞 | Materials | Chemical | 30.9000 | 330,145,549.00 | 1.4752 | 1 | 80.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 6693 | 廣閎科 | Technology/Electronics | Semiconductor | 255.0000 | 244,319,957.00 | 0.4708 | 1 | 80.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 4533 | 協易機 | Industrials/Other | Electric Machinery | 31.2000 | 167,977,886.00 | 3.7318 | 1 | 80.0000 | True | moderate_daily_activity_evidence |
| 4510 | 高鋒 | Industrials/Other | Electric Machinery | 45.3000 | 166,325,642.00 | 2.1879 | 1 | 80.0000 | True | moderate_daily_activity_evidence |
| 3628 | 盈正 | Technology/Electronics | Other Electronics | 80.0000 | 118,351,378.00 | 3.8191 | 1 | 80.0000 | True | moderate_daily_activity_evidence |
| 3577 | 泓格 | Technology/Electronics | Computer/Peripheral | 143.0000 | 249,770,157.00 | 4.0341 | 2 | 70.0000 | True | moderate_daily_activity_evidence |
| 4534 | 慶騰 | Industrials/Other | Electric Machinery | 34.1000 | 113,091,231.00 | 1.0614 | 1 | 70.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 5488 | 松普 | Technology/Electronics | Electronic Components | 14.0500 | 57,277,167.00 | 5.3647 | 1 | 70.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 6720 | 久昌 | Technology/Electronics | Semiconductor | 152.0000 | 30,154,659.00 | 2.9373 | 1 | 70.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 3379 | 彬台 | Industrials/Other | Electric Machinery | 36.0500 | 23,543,322.00 | 2.4141 | 1 | 70.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 4741 | 泓瀚 | Materials | Chemical | 61.7000 | 57,418,198.00 | 0.5745 | 1 | 60.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 6148 | 驊宏資 | Technology/Electronics | Information Services | 33.5500 | 25,009,380.00 | 0.3749 | 1 | 60.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 4556 | 旭然 | Industrials/Other | Other | 80.6000 | 22,240,555.00 | 0.5621 | 1 | 60.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 5202 | 力新 | Technology/Electronics | Information Services | 15.8000 | 17,622,441.00 | 0.8446 | 1 | 60.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 4561 | 健椿 | Industrials/Other | Electric Machinery | 43.0000 | 17,579,295.00 | 1.0051 | 1 | 60.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 5211 | 蒙恬 | Technology/Electronics | Information Services | 24.2000 | 8,500,179.00 | 1.9652 | 1 | 60.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 6870 | 騰雲 | Technology/Electronics | Digital/Cloud | 318.0000 | 133,546,760.00 | 1.7348 | 2 | 50.0000 | True | moderate_daily_activity_evidence |

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
