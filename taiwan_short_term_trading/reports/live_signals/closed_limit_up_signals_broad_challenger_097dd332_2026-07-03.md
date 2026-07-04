# Closed-Limit-Up Paper Signals - 2026-07-03

## Execution Warning

Paper trade only. Day0 close limit-up fills may not be executable without auction/order-book confirmation.

## Strategy Parameters

| capital_twd | profile_name | candidate_hash | market | market_regime_filter | ranking_method | min_turnover_twd | min_volume_ratio_20d | min_fill_quality_score | max_consecutive_limit_ups | min_price | max_price | prior_5d_return_max | prior_20d_return_max | max_positions | target_notional_twd | max_notional_per_symbol_pct | max_notional_per_sector_pct | max_notional_per_industry_pct | board_lot_size | avoid_sectors | allowed_sectors | warnings | commission_rate | commission_discount | slippage_bps_per_side | minimum_commission_twd |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1,000,000.00 | broad_challenger_097dd332 | 097dd332 | BOTH | not_bear | fill_quality_score | 200,000,000.00 | 1.5000 | 60.0000 | 3 | 10.0000 | 100.0000 |  |  | 5 | 300,000.00 | 0.2000 | 1.0000 | 1.0000 | 1000 | ('Healthcare', 'Materials') | () | ('Broad BOTH-market challenger; TWSE sector coverage may be incomplete.', 'Paper only until Day0 close fills are verified.') | 0.0014 | 0.2800 | 5.0000 | 20.0000 |

## Market Date

Signal date: `2026-07-03`
Strategy universe: `BOTH`
Profile: `broad_challenger_097dd332`
Candidate hash: `097dd332`

## Candidate Counts

Raw closed-limit-up candidates: 95
Candidates after strategy filters: 0
Planned paper orders: 0

## Planned Paper Orders

_No rows._

## Skipped Reasons

| reason | count |
| --- | --- |
| turnover_below_500m | 41 |
| price_outside_10_to_100 | 17 |
| bear_regime | 14 |
| avoided_weak_sector | 12 |
| volume_ratio_below_1_5 | 7 |
| too_many_consecutive_limit_ups | 3 |
| fill_quality_below_60 | 1 |

## Candidate Diagnostics

| symbol | name | sector | industry | day0_close | day0_turnover_twd | volume_ratio_20d | consecutive_limit_up_count | fill_quality_score | fillable_moderate | moderate_fill_reason |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 3026 | 禾伸堂 | Technology/Electronics | Electronic Components | 988.0000 | 14,473,721,053.00 | 4.2659 | 1 | 100.0000 | True | moderate_daily_activity_evidence |
| 6213 | 聯茂 | Technology/Electronics | Electronic Components | 394.0000 | 11,465,108,500.00 | 1.7741 | 1 | 100.0000 | True | moderate_daily_activity_evidence |
| 6173 | 信昌電 | Technology/Electronics | Electronic Components | 350.5000 | 8,280,673,931.00 | 3.4463 | 1 | 100.0000 | True | moderate_daily_activity_evidence |
| 1717 | 長興 | Materials | Chemical | 81.6000 | 4,817,980,432.00 | 4.7314 | 1 | 100.0000 | True | moderate_daily_activity_evidence |
| 5371 | 中光電 | Technology/Electronics | Optoelectronics | 93.1000 | 3,707,330,181.00 | 3.3761 | 1 | 100.0000 | True | moderate_daily_activity_evidence |
| 3167 | 大量 | Industrials/Other | Electric Machinery | 877.0000 | 3,274,374,226.00 | 1.8328 | 1 | 100.0000 | True | moderate_daily_activity_evidence |
| 1314 | 中石化 | Materials | Plastics | 9.8800 | 2,833,984,143.00 | 7.1983 | 1 | 100.0000 | True | moderate_daily_activity_evidence |
| 2634 | 漢翔 | Industrials/Other | Shipping/Transportation | 60.0000 | 2,712,615,892.00 | 3.5453 | 1 | 100.0000 | True | moderate_daily_activity_evidence |
| 3363 | 上詮 | Technology/Electronics | Communications/Internet | 581.0000 | 2,566,591,870.00 | 1.3333 | 1 | 100.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 4991 | 環宇-KY | Technology/Electronics | Semiconductor | 578.0000 | 2,406,553,929.00 | 1.3346 | 1 | 100.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 8033 | 雷虎 | Industrials/Other | Other | 204.5000 | 2,192,791,394.00 | 1.2068 | 1 | 100.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 5314 | 世紀* | Industrials/Other | Other | 71.5000 | 2,095,578,931.00 | 6.0051 | 1 | 100.0000 | True | moderate_daily_activity_evidence |
| 2428 | 興勤 | Technology/Electronics | Electronic Components | 342.0000 | 1,541,349,276.00 | 1.6782 | 1 | 100.0000 | True | moderate_daily_activity_evidence |
| 2645 | 長榮航太 | Industrials/Other | Shipping/Transportation | 216.5000 | 1,491,079,605.00 | 2.8441 | 1 | 100.0000 | True | moderate_daily_activity_evidence |
| 5386 | 青雲 | Technology/Electronics | Computer/Peripheral | 420.5000 | 1,126,215,215.00 | 1.8931 | 1 | 100.0000 | True | moderate_daily_activity_evidence |
| 3663 | 鑫科 | Technology/Electronics | Other Electronics | 88.4000 | 961,006,201.00 | 3.8614 | 1 | 100.0000 | True | moderate_daily_activity_evidence |
| 2630 | 亞航 | Industrials/Other | Shipping/Transportation | 55.8000 | 949,290,205.00 | 6.0791 | 1 | 100.0000 | True | moderate_daily_activity_evidence |
| 6753 | 龍德造船 | Industrials/Other | Shipping/Transportation | 154.0000 | 759,510,611.00 | 3.2386 | 1 | 100.0000 | True | moderate_daily_activity_evidence |
| 7402 | 邑錡 | Technology/Electronics | Optoelectronics | 138.5000 | 666,303,125.00 | 5.3585 | 1 | 100.0000 | True | moderate_daily_activity_evidence |
| 4743 | 合一 | Healthcare | Biotechnology/Medical | 57.3000 | 637,530,108.00 | 5.1311 | 1 | 100.0000 | True | moderate_daily_activity_evidence |
| 2332 | 友訊 | Technology/Electronics | Communications/Internet | 20.0000 | 586,939,423.00 | 2.4520 | 1 | 100.0000 | True | moderate_daily_activity_evidence |
| 2233 | 宇隆 | Consumer/Services | Automobile | 275.0000 | 558,088,849.00 | 1.1959 | 1 | 100.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 1313 | 聯成 | Materials | Plastics | 14.3000 | 544,038,214.00 | 4.8541 | 1 | 100.0000 | True | moderate_daily_activity_evidence |
| 6174 | 安碁 | Technology/Electronics | Electronic Components | 61.8000 | 540,634,148.00 | 1.8054 | 1 | 100.0000 | True | moderate_daily_activity_evidence |
| 6143 | 振曜 | Technology/Electronics | Communications/Internet | 106.5000 | 539,058,543.00 | 7.6040 | 1 | 100.0000 | True | moderate_daily_activity_evidence |
| 6477 | 安集 | Technology/Electronics | Optoelectronics | 36.9000 | 254,596,377.00 | 5.4356 | 1 | 100.0000 | True | moderate_daily_activity_evidence |
| 6182 | 合晶 | Technology/Electronics | Semiconductor | 163.0000 | 30,347,150,154.00 | 3.6452 | 4 | 90.0000 | True | moderate_daily_activity_evidence |
| 3042 | 晶技 | Technology/Electronics | Electronic Components | 249.0000 | 21,990,125,268.00 | 2.5500 | 3 | 90.0000 | True | moderate_daily_activity_evidence |
| 2484 | 希華 | Technology/Electronics | Electronic Components | 96.4000 | 10,852,559,394.00 | 2.5222 | 3 | 90.0000 | True | moderate_daily_activity_evidence |
| 1326 | 台化 | Materials | Plastics | 68.0000 | 6,449,369,699.00 | 2.5517 | 2 | 90.0000 | True | moderate_daily_activity_evidence |

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
