# Closed-Limit-Up Paper Signals - 2026-06-24

## Execution Warning

Paper trade only. Day0 close limit-up fills may not be executable without auction/order-book confirmation.

## Strategy Parameters

| capital_twd | profile_name | candidate_hash | market | market_regime_filter | ranking_method | min_turnover_twd | min_volume_ratio_20d | min_fill_quality_score | max_consecutive_limit_ups | min_price | max_price | prior_5d_return_max | prior_20d_return_max | max_positions | target_notional_twd | max_notional_per_symbol_pct | max_notional_per_sector_pct | max_notional_per_industry_pct | board_lot_size | avoid_sectors | allowed_sectors | warnings | commission_rate | commission_discount | slippage_bps_per_side | minimum_commission_twd |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1,000,000.00 | conservative_tpex_35adc734 | 35adc734 | TPEX | avoid_weak_day | fill_quality_score | 300,000,000.00 | 1.2000 | 60.0000 | 3 | 10.0000 | 100.0000 | 0.3000 | 0.8000 | 5 | 300,000.00 | 0.2000 | 1.0000 | 0.2500 | 1000 | ('Healthcare', 'Materials') | () | ('Conservative TPEX finalist from focused expansion.', 'Uses avoid-weak-day and prior momentum cap from exact audit.') | 0.0014 | 0.2800 | 5.0000 | 20.0000 |

## Market Date

Signal date: `2026-06-24`
Strategy universe: `TPEX`
Profile: `conservative_tpex_35adc734`
Candidate hash: `35adc734`

## Candidate Counts

Raw closed-limit-up candidates: 29
Candidates after strategy filters: 0
Planned paper orders: 0

## Planned Paper Orders

_No rows._

## Skipped Reasons

| reason | count |
| --- | --- |
| turnover_below_500m | 22 |
| price_outside_10_to_100 | 5 |
| volume_ratio_below_1_5 | 1 |
| weak_market_day | 1 |

## Candidate Diagnostics

| symbol | name | sector | industry | day0_close | day0_turnover_twd | volume_ratio_20d | consecutive_limit_up_count | fill_quality_score | fillable_moderate | moderate_fill_reason |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 5328 | 華容 | Technology/Electronics | Electronic Components | 58.1000 | 1,565,817,528.00 | 0.7509 | 1 | 100.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 6829 | 千附精密 | Technology/Electronics | Semiconductor | 226.5000 | 851,129,204.00 | 4.2524 | 1 | 100.0000 | True | moderate_daily_activity_evidence |
| 6462 | 神盾 | Technology/Electronics | Semiconductor | 134.0000 | 650,926,732.00 | 2.2386 | 1 | 100.0000 | True | moderate_daily_activity_evidence |
| 6683 | 雍智科技 | Technology/Electronics | Semiconductor | 1,940.00 | 8,160,380,590.00 | 3.1127 | 2 | 90.0000 | True | moderate_daily_activity_evidence |
| 8054 | 安國 | Technology/Electronics | Semiconductor | 136.5000 | 2,597,867,456.00 | 7.6736 | 3 | 90.0000 | True | moderate_daily_activity_evidence |
| 8040 | 九暘 | Technology/Electronics | Semiconductor | 125.0000 | 1,823,623,219.00 | 6.1492 | 3 | 90.0000 | True | moderate_daily_activity_evidence |
| 8234 | 新漢 | Technology/Electronics | Computer/Peripheral | 70.7000 | 393,434,957.00 | 5.3760 | 1 | 90.0000 | True | moderate_daily_activity_evidence |
| 6620 | 漢達 | Healthcare | Biotechnology/Medical | 99.9000 | 213,042,154.00 | 3.4664 | 1 | 90.0000 | True | moderate_daily_activity_evidence |
| 6877 | 鏵友益 | Technology/Electronics | Other Electronics | 139.5000 | 206,626,514.00 | 4.2502 | 1 | 90.0000 | True | moderate_daily_activity_evidence |
| 3441 | 聯一光電 | Technology/Electronics | Optoelectronics | 90.3000 | 203,178,678.00 | 0.3358 | 1 | 80.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 5011 | 久陽 | Materials | Steel | 24.8500 | 162,308,341.00 | 3.9995 | 1 | 80.0000 | True | moderate_daily_activity_evidence |
| 3360 | 尚立 | Technology/Electronics | Electronic Distribution | 19.7000 | 105,533,423.00 | 3.5443 | 1 | 80.0000 | True | moderate_daily_activity_evidence |
| 6234 | 高僑 | Technology/Electronics | Optoelectronics | 50.4000 | 195,963,217.00 | 1.6246 | 1 | 70.0000 | True | moderate_daily_activity_evidence |
| 3114 | 好德 | Technology/Electronics | Electronic Components | 52.5000 | 178,805,087.00 | 1.4709 | 1 | 70.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 6263 | 普萊德 | Technology/Electronics | Communications/Internet | 173.5000 | 153,062,456.00 | 1.7913 | 1 | 70.0000 | True | moderate_daily_activity_evidence |
| 4707 | 磐亞 | Materials | Chemical | 23.2000 | 107,055,501.00 | 1.2080 | 1 | 70.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 6129 | 普誠 | Technology/Electronics | Semiconductor | 18.9000 | 74,572,089.00 | 3.4603 | 1 | 70.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 3122 | 笙泉 | Technology/Electronics | Semiconductor | 34.9000 | 28,406,390.00 | 2.4344 | 1 | 70.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 6228 | 全譜 | Technology/Electronics | Computer/Peripheral | 21.3500 | 3,496,938.00 | 8.6888 | 1 | 70.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 5464 | 霖宏 | Technology/Electronics | Electronic Components | 98.0000 | 291,839,930.00 | 1.4506 | 3 | 60.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 2061 | 風青 | Industrials/Other | Electrical Cable | 63.8000 | 59,780,762.00 | 0.3480 | 1 | 60.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 5251 | 天鉞電 | Technology/Electronics | Optoelectronics | 34.5000 | 32,591,880.00 | 1.6289 | 1 | 60.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 3434 | 哲固 | Technology/Electronics | Optoelectronics | 32.1500 | 23,231,796.00 | 1.6865 | 1 | 60.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 3297 | 杭特 | Technology/Electronics | Optoelectronics | 33.5500 | 9,071,462.00 | 1.1479 | 1 | 60.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 4416 | 三圓 | Industrials/Other | Building Materials/Construction | 11.1000 | 8,057,651.00 | 1.1797 | 1 | 60.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 8024 | 佑華 | Technology/Electronics | Semiconductor | 15.1000 | 11,546,085.00 | 8.6053 | 2 | 50.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 8444 | 綠河-KY | Industrials/Other | Other | 6.0600 | 337,223.00 | 0.1007 | 1 | 50.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 5489 | 彩富 | Technology/Electronics | Other Electronics | 61.9000 | 91,841,489.00 | 10.6522 | 4 | 25.0000 | False | failed_moderate_turnover_volume_range_or_close_location |
| 5321 | 美而快 | Technology/Electronics | Digital/Cloud | 52.6000 | 21,324,061.00 | 0.8414 | 2 | 0.0000 | False | failed_moderate_turnover_volume_range_or_close_location |

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
