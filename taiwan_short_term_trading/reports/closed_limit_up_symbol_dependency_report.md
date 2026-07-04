# Closed-Limit-Up Symbol Dependency Diagnostic

Source OOS trades: `reports/walk_forward_closed_limit_up_overnight_oos_trades.csv`
Database: `data/taiwan_trading.duckdb`

## Executive Answer

Strict verdict: the edge is not dominated by a small set of symbols. It survives removing the top PnL symbols and survives a 20-trading-day cooldown.

## Coverage

Best-strategy OOS trades: 1,403
Unique symbols: 110

## Symbol Concentration

| symbols | trades | net_pnl | top_1_symbol_trade_share | top_5_symbol_trade_share | top_10_symbol_trade_share | top_20_symbol_trade_share | top_1_symbol_pnl_share | top_5_symbol_pnl_share | top_10_symbol_pnl_share | top_20_symbol_pnl_share | hhi_by_symbol_trades | hhi_by_symbol_pnl |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 110 | 1403 | 1,941,315.60 | 0.0428 | 0.1511 | 0.2495 | 0.4148 | 0.0541 | 0.2258 | 0.3951 | 0.6150 | 0.0147 | 0.0197 |

## Repeat Hotness

| symbol_hotness_bucket | trades | avg_net_return | median_net_return | win_rate | profit_factor | net_pnl |
| --- | --- | --- | --- | --- | --- | --- |
| first_seen | 110 | 0.0177 | 0.0096 | 0.6727 | 5.5945 | 152,247.67 |
| repeat_within_20d | 1194 | 0.0186 | 0.0169 | 0.7362 | 5.5230 | 1,659,277.49 |
| repeat_21_60d | 36 | 0.0270 | 0.0229 | 0.8611 | 39.8368 | 72,903.13 |
| repeat_61_252d | 57 | 0.0127 | 0.0172 | 0.7544 | 4.5419 | 50,748.08 |
| old_repeat | 6 | 0.0118 | 0.0056 | 0.5000 | 4.2414 | 6,139.25 |

## Cooldown Tests

| scenario | trades | trade_share | avg_net_return | median_net_return | win_rate | profit_factor | total_net_pnl | pnl_share | max_drawdown |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| no_cooldown | 1403 | 1.0000 | 0.0185 | 0.0161 | 0.7341 | 5.6433 | 1,941,315.60 | 1.0000 | -49,615.44 |
| cooldown_5_trading_days | 229 | 0.1632 | 0.0176 | 0.0153 | 0.7293 | 6.3198 | 304,823.03 | 0.1570 | -6,101.93 |
| cooldown_10_trading_days | 214 | 0.1525 | 0.0177 | 0.0148 | 0.7243 | 6.5058 | 287,433.83 | 0.1481 | -5,340.35 |
| cooldown_20_trading_days | 203 | 0.1447 | 0.0176 | 0.0153 | 0.7192 | 6.3687 | 272,802.80 | 0.1405 | -5,840.12 |
| cooldown_60_trading_days | 153 | 0.1091 | 0.0172 | 0.0133 | 0.6993 | 5.9194 | 200,373.47 | 0.1032 | -5,840.12 |
| max_1_trade_per_symbol_per_month | 223 | 0.1589 | 0.0177 | 0.0150 | 0.7220 | 5.6211 | 293,685.23 | 0.1513 | -9,923.09 |
| max_2_trades_per_symbol_per_quarter | 376 | 0.2680 | 0.0174 | 0.0146 | 0.7234 | 5.3677 | 484,383.07 | 0.2495 | -19,846.18 |

## Remove Top Symbol Tests

| scenario | trades | trade_share | avg_net_return | median_net_return | win_rate | profit_factor | total_net_pnl | pnl_share | max_drawdown |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| trade_all | 1403 | 1.0000 | 0.0185 | 0.0161 | 0.7341 | 5.6433 | 1,941,315.60 | 1.0000 | -49,615.44 |
| remove_top_1_symbol_by_pnl | 1358 | 0.9679 | 0.0182 | 0.0157 | 0.7290 | 5.4022 | 1,836,346.62 | 0.9459 | -49,615.44 |
| remove_top_5_symbols_by_pnl | 1202 | 0.8567 | 0.0166 | 0.0146 | 0.7063 | 4.7825 | 1,502,954.18 | 0.7742 | -49,615.44 |
| remove_top_10_symbols_by_pnl | 1101 | 0.7847 | 0.0144 | 0.0130 | 0.6866 | 4.0222 | 1,174,210.14 | 0.6049 | -49,615.44 |
| remove_top_20_symbols_by_pnl | 874 | 0.6230 | 0.0116 | 0.0079 | 0.6327 | 3.0782 | 747,391.65 | 0.3850 | -49,615.44 |
| remove_top_1_symbol_by_trade_count | 1343 | 0.9572 | 0.0182 | 0.0157 | 0.7297 | 5.5007 | 1,853,318.28 | 0.9547 | -49,615.44 |
| remove_top_5_symbols_by_trade_count | 1191 | 0.8489 | 0.0177 | 0.0153 | 0.7162 | 5.0619 | 1,585,130.23 | 0.8165 | -49,615.44 |
| remove_top_10_symbols_by_trade_count | 1053 | 0.7505 | 0.0165 | 0.0145 | 0.6933 | 4.4708 | 1,324,545.19 | 0.6823 | -66,033.62 |

## Top Symbols By PnL

| symbol | name | sector | industry | trades | avg_net_return | median_net_return | win_rate | profit_factor | total_net_pnl | max_drawdown | first_trade_date | last_trade_date | active_quarters | max_trades_single_quarter | trade_share | pnl_share |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 4931 | 新盛力 | Technology/Electronics | Computer/Peripheral | 45 | 0.0275 | 0.0282 | 0.8889 | 112.3976 | 104,968.98 | -942.2910 | 2024-11-12 | 2025-06-25 | 3 | 20 | 0.0321 | 0.0541 |
| 8096 | 擎亞 | Technology/Electronics | Electronic Distribution | 60 | 0.0234 | 0.0286 | 0.8333 | 14.9522 | 87,997.33 | -4,627.76 | 2025-03-04 | 2026-03-18 | 4 | 30 | 0.0428 | 0.0453 |
| 5351 | 鈺創 | Technology/Electronics | Semiconductor | 43 | 0.0278 | 0.0186 | 0.8837 | 7.0859 | 82,096.01 | -13,489.51 | 2025-08-08 | 2026-03-18 | 3 | 25 | 0.0306 | 0.0423 |
| 3379 | 彬台 | Industrials/Other | Electric Machinery | 25 | 0.0455 | 0.0448 | 1.0000 | inf | 81,887.90 | 0.0000 | 2025-01-14 | 2025-03-04 | 1 | 25 | 0.0178 | 0.0422 |
| 3297 | 杭特 | Technology/Electronics | Optoelectronics | 28 | 0.0363 | 0.0304 | 1.0000 | inf | 81,411.20 | 0.0000 | 2025-05-23 | 2025-08-20 | 2 | 25 | 0.0200 | 0.0419 |
| 5328 | 華容 | Technology/Electronics | Electronic Components | 24 | 0.0379 | 0.0567 | 1.0000 | inf | 78,057.55 | 0.0000 | 2024-12-04 | 2026-02-24 | 2 | 20 | 0.0171 | 0.0402 |
| 4903 | 聯光通 | Technology/Electronics | Communications/Internet | 19 | 0.0432 | 0.0218 | 1.0000 | inf | 71,497.71 | 0.0000 | 2024-10-01 | 2026-03-23 | 3 | 10 | 0.0135 | 0.0368 |
| 6140 | 訊達電腦 | Technology/Electronics | Information Services | 23 | 0.0347 | 0.0353 | 0.6522 | 8.1084 | 62,734.74 | -5,363.31 | 2025-02-05 | 2025-07-14 | 2 | 20 | 0.0164 | 0.0323 |
| 8043 | 蜜望實 | Technology/Electronics | Electronic Components | 15 | 0.0494 | 0.0355 | 1.0000 | inf | 58,409.35 | 0.0000 | 2025-11-14 | 2026-01-14 | 2 | 10 | 0.0107 | 0.0301 |
| 3709 | 鑫聯大投控 | Technology/Electronics | Computer/Peripheral | 20 | 0.0399 | 0.0320 | 1.0000 | inf | 58,044.70 | 0.0000 | 2025-04-23 | 2026-02-25 | 3 | 10 | 0.0143 | 0.0299 |
| 4939 | 亞電 | Technology/Electronics | Electronic Components | 25 | 0.0261 | 0.0260 | 1.0000 | inf | 55,462.69 | 0.0000 | 2025-11-25 | 2026-01-20 | 2 | 15 | 0.0178 | 0.0286 |
| 5340 | 建榮 | Technology/Electronics | Electronic Components | 20 | 0.0353 | 0.0178 | 1.0000 | inf | 53,694.71 | 0.0000 | 2025-11-12 | 2026-01-19 | 2 | 10 | 0.0143 | 0.0277 |
| 3323 | 加百裕 | Technology/Electronics | Computer/Peripheral | 32 | 0.0181 | 0.0219 | 0.6875 | 10.8643 | 46,728.06 | -4,737.08 | 2024-11-21 | 2025-12-18 | 4 | 12 | 0.0228 | 0.0241 |
| 8088 | 品安 | Technology/Electronics | Semiconductor | 25 | 0.0215 | 0.0265 | 1.0000 | inf | 45,925.84 | 0.0000 | 2025-03-17 | 2026-01-08 | 4 | 10 | 0.0178 | 0.0237 |
| 4541 | 晟田 | Industrials/Other | Other | 23 | 0.0281 | 0.0248 | 1.0000 | inf | 39,483.65 | 0.0000 | 2025-03-19 | 2025-08-29 | 3 | 15 | 0.0164 | 0.0203 |
| 4510 | 高鋒 | Industrials/Other | Electric Machinery | 23 | 0.0257 | 0.0347 | 0.8261 | 65.3876 | 38,973.90 | -605.3012 | 2024-11-26 | 2025-04-30 | 3 | 10 | 0.0164 | 0.0201 |
| 6244 | 茂迪 | Technology/Electronics | Optoelectronics | 20 | 0.0220 | 0.0328 | 0.7500 | 4.3682 | 37,371.37 | -11,095.22 | 2025-12-23 | 2026-03-02 | 2 | 15 | 0.0143 | 0.0193 |
| 6265 | 方土昶 | Technology/Electronics | Electronic Distribution | 15 | 0.0277 | 0.0480 | 0.6667 | 4.0013 | 37,367.21 | -12,450.23 | 2025-12-30 | 2026-03-16 | 2 | 10 | 0.0107 | 0.0192 |
| 8042 | 金山電 | Technology/Electronics | Electronic Components | 20 | 0.0331 | 0.0254 | 1.0000 | inf | 36,054.65 | 0.0000 | 2025-11-21 | 2026-01-14 | 2 | 10 | 0.0143 | 0.0186 |
| 8234 | 新漢 | Technology/Electronics | Computer/Peripheral | 24 | 0.0194 | 0.0172 | 1.0000 | inf | 35,756.41 | 0.0000 | 2024-12-31 | 2025-06-26 | 3 | 15 | 0.0171 | 0.0184 |
| 6175 | 立敦 | Technology/Electronics | Electronic Components | 10 | 0.0409 | 0.0409 | 1.0000 | inf | 35,677.92 | 0.0000 | 2025-10-23 | 2025-10-27 | 1 | 10 | 0.0071 | 0.0184 |
| 8111 | 立碁 | Technology/Electronics | Optoelectronics | 26 | 0.0204 | 0.0161 | 1.0000 | inf | 35,431.63 | 0.0000 | 2024-10-01 | 2025-11-05 | 4 | 10 | 0.0185 | 0.0183 |
| 6530 | 創威 | Technology/Electronics | Communications/Internet | 19 | 0.0247 | -0.0120 | 0.4737 | 2.9441 | 35,248.04 | -18,130.73 | 2024-10-01 | 2026-03-23 | 3 | 10 | 0.0135 | 0.0182 |
| 6148 | 驊宏資 | Technology/Electronics | Information Services | 28 | 0.0188 | 0.0231 | 0.6429 | 6.4451 | 34,820.59 | -4,905.10 | 2025-02-07 | 2025-08-21 | 3 | 15 | 0.0200 | 0.0179 |
| 1815 | 富喬 | Technology/Electronics | Electronic Components | 32 | 0.0128 | 0.0153 | 0.8438 | 15.5171 | 34,394.99 | -2,369.27 | 2024-11-21 | 2025-06-04 | 3 | 12 | 0.0228 | 0.0177 |
| 4973 | 廣穎電通 | Technology/Electronics | Semiconductor | 10 | 0.0330 | 0.0330 | 1.0000 | inf | 31,416.69 | 0.0000 | 2026-01-07 | 2026-01-08 | 1 | 10 | 0.0071 | 0.0162 |
| 3162 | 精確 | Industrials/Other | Electric Machinery | 31 | 0.0145 | 0.0172 | 0.8387 | 13.1533 | 27,033.72 | -2,224.39 | 2025-03-17 | 2026-01-23 | 5 | 10 | 0.0221 | 0.0139 |
| 8064 | 東捷 | Technology/Electronics | Optoelectronics | 24 | 0.0164 | -0.0080 | 0.4167 | 3.6202 | 26,639.83 | -5,776.46 | 2024-12-24 | 2026-03-26 | 2 | 20 | 0.0171 | 0.0137 |
| 6425 | 易發 | Industrials/Other | Electric Machinery | 4 | 0.0882 | 0.0882 | 1.0000 | inf | 26,451.43 | 0.0000 | 2024-10-01 | 2024-10-01 | 1 | 4 | 0.0029 | 0.0136 |
| 6127 | 九豪 | Technology/Electronics | Electronic Components | 14 | 0.0199 | 0.0180 | 0.6429 | 8.9454 | 26,072.05 | -3,281.41 | 2024-12-04 | 2026-03-19 | 3 | 5 | 0.0100 | 0.0134 |
