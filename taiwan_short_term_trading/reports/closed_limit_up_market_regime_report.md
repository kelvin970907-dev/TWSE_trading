# Closed-Limit-Up Market Regime Diagnostic

Source OOS trades: `reports/walk_forward_closed_limit_up_overnight_oos_trades.csv`
Database: `data/taiwan_trading.duckdb`
Index symbol: `TAIEX`

## Executive Answer

Strict verdict: the edge remains positive outside clean bull regimes, including bearish/correction samples where available. A market-regime filter may improve comfort, but the daily evidence does not show the edge is merely a bull-market artifact.

## Coverage

Best-strategy OOS trades after parameter filter: 1,403
Trades with matching index data: 1,403

## Stress Tests

| scenario | trades | trade_share | avg_net_return | median_net_return | win_rate | profit_factor | total_net_pnl | net_pnl_share | max_drawdown |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| trade_all_regimes | 1403 | 1.0000 | 0.0185 | 0.0161 | 0.7341 | 5.6433 | 1,941,315.60 | 1.0000 | -49,615.44 |
| trade_only_bull_regime | 913 | 0.6507 | 0.0168 | 0.0157 | 0.7141 | 5.8570 | 1,161,910.42 | 0.5985 | -36,629.02 |
| trade_only_not_bear_regime | 1181 | 0.8418 | 0.0181 | 0.0161 | 0.7257 | 6.1724 | 1,612,516.93 | 0.8306 | -36,629.02 |
| trade_only_taiex_day0_return_ge_0 | 774 | 0.5517 | 0.0172 | 0.0159 | 0.7261 | 4.8042 | 963,696.55 | 0.4964 | -49,615.44 |
| avoid_correction_regime | 1010 | 0.7199 | 0.0162 | 0.0154 | 0.7188 | 5.6894 | 1,230,677.52 | 0.6339 | -36,629.02 |
| avoid_weak_market_day | 1028 | 0.7327 | 0.0184 | 0.0161 | 0.7422 | 5.6561 | 1,407,003.70 | 0.7248 | -49,615.44 |
| bull_regime_and_avoid_weak_market_day | 738 | 0.5260 | 0.0175 | 0.0159 | 0.7290 | 6.3477 | 973,583.49 | 0.5015 | -36,629.02 |

## Regime Summary

| summary_level | group | trades | avg_net_return | median_net_return | win_rate | profit_factor | total_net_pnl | max_drawdown |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| overall | all | 1403 | 0.0185 | 0.0161 | 0.7341 | 5.6433 | 1,941,315.60 | -49,615.44 |
| by_bull_regime | False | 490 | 0.0215 | 0.0218 | 0.7714 | 5.3576 | 779,405.18 | -49,615.44 |
| by_bull_regime | True | 913 | 0.0168 | 0.0157 | 0.7141 | 5.8570 | 1,161,910.42 | -36,629.02 |
| by_bear_regime | False | 1181 | 0.0181 | 0.0161 | 0.7257 | 6.1724 | 1,612,516.93 | -36,629.02 |
| by_bear_regime | True | 222 | 0.0202 | 0.0150 | 0.7793 | 4.0922 | 328,798.68 | -49,615.44 |
| by_correction_regime | False | 1010 | 0.0162 | 0.0154 | 0.7188 | 5.6894 | 1,230,677.52 | -36,629.02 |
| by_correction_regime | True | 393 | 0.0244 | 0.0231 | 0.7735 | 5.5656 | 710,638.08 | -49,615.44 |
| by_weak_market_day | False | 1028 | 0.0184 | 0.0161 | 0.7422 | 5.6561 | 1,407,003.70 | -49,615.44 |
| by_weak_market_day | True | 375 | 0.0185 | 0.0172 | 0.7120 | 5.6101 | 534,311.90 | -29,200.59 |
| by_strong_market_day | False | 786 | 0.0170 | 0.0157 | 0.7214 | 4.5932 | 1,016,875.83 | -57,000.59 |
| by_strong_market_day | True | 617 | 0.0203 | 0.0174 | 0.7504 | 7.8432 | 924,439.77 | -29,699.40 |
| by_taiex_day0_return_bucket | -0.5%_0 | 254 | 0.0222 | 0.0185 | 0.7913 | 10.0722 | 443,307.15 | -11,627.28 |
| by_taiex_day0_return_bucket | -1%_-0.5% | 163 | 0.0167 | 0.0150 | 0.6687 | 5.7921 | 198,895.91 | -13,190.44 |
| by_taiex_day0_return_bucket | 0.5_1% | 306 | 0.0210 | 0.0184 | 0.7778 | 10.5849 | 490,266.39 | -13,108.07 |
| by_taiex_day0_return_bucket | 0_0.5% | 157 | 0.0051 | 0.0117 | 0.6306 | 1.3320 | 39,256.78 | -49,615.44 |
| by_taiex_day0_return_bucket | <=-1% | 212 | 0.0199 | 0.0200 | 0.7453 | 5.5085 | 335,415.99 | -33,476.80 |
| by_taiex_day0_return_bucket | >1% | 311 | 0.0196 | 0.0135 | 0.7235 | 6.1725 | 434,173.38 | -29,699.40 |
| by_taiex_5d_return_bucket | -2%_0 | 329 | 0.0238 | 0.0235 | 0.7720 | 9.3157 | 592,205.73 | -15,349.16 |
| by_taiex_5d_return_bucket | -5%_-2% | 120 | 0.0236 | 0.0223 | 0.7083 | 3.3823 | 185,014.97 | -49,615.44 |
| by_taiex_5d_return_bucket | 0_2% | 471 | 0.0174 | 0.0157 | 0.7643 | 7.3913 | 639,331.09 | -34,681.27 |
| by_taiex_5d_return_bucket | 2_5% | 408 | 0.0156 | 0.0147 | 0.6520 | 4.3462 | 468,389.46 | -34,532.29 |
| by_taiex_5d_return_bucket | <=-5% | 15 | -0.0128 | 0.0043 | 0.6667 | 0.5579 | -11,804.72 | -26,701.77 |
| by_taiex_5d_return_bucket | >5% | 60 | 0.0148 | 0.0115 | 0.9167 | 28.2845 | 68,179.07 | -2,498.82 |
| by_taiex_20d_return_bucket | -2%_0 | 80 | 0.0117 | 0.0115 | 0.6375 | 3.1933 | 60,689.95 | -10,372.05 |
| by_taiex_20d_return_bucket | -5%_-2% | 165 | 0.0291 | 0.0290 | 0.8061 | 13.7292 | 370,362.64 | -3,859.57 |
| by_taiex_20d_return_bucket | 0_2% | 196 | 0.0222 | 0.0196 | 0.7194 | 7.6635 | 340,223.41 | -13,612.29 |
| by_taiex_20d_return_bucket | 2_5% | 226 | 0.0181 | 0.0178 | 0.7522 | 9.5144 | 310,957.08 | -16,677.36 |
| by_taiex_20d_return_bucket | <=-5% | 155 | 0.0165 | 0.0120 | 0.7742 | 2.7129 | 168,527.53 | -49,615.44 |
| by_taiex_20d_return_bucket | >5% | 581 | 0.0158 | 0.0130 | 0.7143 | 4.9380 | 690,555.00 | -36,629.02 |
| by_year | 2024 | 136 | 0.0297 | 0.0223 | 0.8529 | 19.3332 | 319,023.81 | -3,432.77 |
| by_year | 2025 | 887 | 0.0165 | 0.0159 | 0.7204 | 4.8389 | 1,065,900.57 | -49,615.44 |
| by_year | 2026 | 380 | 0.0191 | 0.0132 | 0.7237 | 5.5225 | 556,391.22 | -29,699.40 |
| by_quarter | 2024Q4 | 136 | 0.0297 | 0.0223 | 0.8529 | 19.3332 | 319,023.81 | -3,432.77 |
| by_quarter | 2025Q1 | 270 | 0.0165 | 0.0144 | 0.7222 | 5.9566 | 336,560.96 | -18,471.39 |
| by_quarter | 2025Q2 | 255 | 0.0151 | 0.0172 | 0.7255 | 3.1721 | 273,250.58 | -49,615.44 |
| by_quarter | 2025Q3 | 87 | 0.0130 | 0.0079 | 0.7931 | 5.9418 | 74,355.75 | -4,045.39 |
| by_quarter | 2025Q4 | 275 | 0.0188 | 0.0209 | 0.6909 | 6.5397 | 381,733.27 | -36,629.02 |
| by_quarter | 2026Q1 | 380 | 0.0191 | 0.0132 | 0.7237 | 5.5225 | 556,391.22 | -29,699.40 |
| by_index_data_available | True | 1403 | 0.0185 | 0.0161 | 0.7341 | 5.6433 | 1,941,315.60 | -49,615.44 |

## Interpretation

Bull-regime trades: 913, avg net 1.681%, PnL 1,161,910 TWD. Bear-regime trades: 222, avg net 2.018%, PnL 328,799 TWD. Correction-regime trades: 393, avg net 2.436%, PnL 710,638 TWD.
