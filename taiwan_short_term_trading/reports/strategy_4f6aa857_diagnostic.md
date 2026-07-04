# Strategy 4f6aa857 Diagnostic

## Exact Rules

| Field | Value |
|---|---:|
| strategy_name | closed_limit_up_with_market_regime_filter_4f6aa857 |
| family | closed_limit_up_with_market_regime_filter |
| event_type | closed_limit_up |
| market | BOTH |
| entry rule | Buy at Day0 close |
| exit rule | Sell at Day1 open |
| min_turnover_twd | 200,000,000.00 TWD |
| min_volume_ratio_20d | 1.5 |
| price range | 10 to 100 TWD |
| sector_filter | avoid_healthcare_materials |
| market_regime_filter | not_bear |
| fill_assumption | moderate |
| min_fill_quality_score | 60 |
| ranking_method | fill_quality_score |
| max_positions_per_day | 5 |
| target_notional | 300,000.00 TWD |
| symbol cap | 20.00% |
| sector cap | 70.00% |
| industry cap | 35.00% |
| monthly symbol cap | 5 |
| prior_5d_return_min/max | none / none |
| prior_20d_return_min/max | none / none |

## Standalone Metrics

| Field | Value |
|---|---:|
| trades | 1,872 |
| active_days | 484 |
| final_equity | 10,496,666.54 TWD |
| net_pnl | 9,496,666.54 TWD |
| CAGR-like return | 160.75% |
| avg_net_return_per_trade | 1.89% |
| median_net_return_per_trade | 1.55% |
| win_rate | 73.50% |
| profit_factor | 5.32 |
| max_drawdown_twd | -141,188.61 TWD |
| max_drawdown_pct | -2.04% |
| positive_quarters / total | 10 / 10 |
| symbol_concentration | 0.75% |
| sector_concentration | 65.65% |

## Champion Comparison

| Field | Value |
|---|---:|
| pnl_vs_champion | 6,537,622.58 TWD |
| cagr_vs_champion | 85.41% |
| maxdd_vs_champion | 0.27% |
| pf_vs_champion | -1.54 |
| median_return_vs_champion | -0.28% |
| daily_return_correlation | 0.468 |
| diversification_score | 1.312 |
| combined_final_equity | 12,932,048.44 TWD |
| combined_CAGR | 114.02% |
| combined_max_drawdown | -1.65% |
| combined_profit_factor | not computed in tournament output |

## Why It Beat Or Improved The Champion

- It admits BOTH-market closed-limit-up opportunities while applying a not-bear regime filter.
- It lowers the turnover gate to 200M, increasing trade count from champion 573 to 1,872.
- Despite broader/lower-turnover exposure, max drawdown improves slightly versus champion (-2.04% vs -2.31%).
- It improves the combined champion-plus-challenger portfolio CAGR and drawdown in the tournament blend.

## New Risks Versus Champion

- It expands from the champion TPEX-only universe to BOTH markets, so TWSE execution and behavior must be audited separately.
- It lowers the turnover floor from champion 500,000,000.00 TWD to 200,000,000.00 TWD, increasing capacity/fill realism risk.
- It keeps the same moderate fill assumption as champion; the improvement is not from a weaker fill model.
- It keeps the same fill-quality threshold as champion.
- It adds a not-bear market-regime gate, which materially reduces drawdown in this compact run.

## Strict Interpretation

This is the cleanest compact challenger because it improves total PnL and combined portfolio drawdown while keeping the same fill assumption and fill-quality threshold. The main unresolved issue is that it broadens to BOTH markets and lowers the turnover floor, so it needs a dedicated execution/liquidity audit before replacing the TPEX champion.
