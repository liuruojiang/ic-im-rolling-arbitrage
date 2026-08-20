# IC固定估值增仓三条观察线Put同步对照 v5 冻结规格

冻结日期：2026-08-18（Asia/Shanghai）  
状态：预注册研究；未批准实盘。

## 1. 研究问题

固定v4选出的三条估值增仓线，不再调整估值参数，正式比较：

1. `core_put_only`：新增1倍IC不增加Put，只有固定1倍底仓沿用主线Put；
2. `sync_put_total_ic`：新增IC持有期间，把同一条主线Put目标Delta按总IC从1倍扩到2倍；新增仓退出后恢复1倍底仓目标。

本层只回答同步保护的回撤收益交换，不重新选择开仓/退出阈值。

## 2. 固定观察线

- 主观察：`low=0.375/high=1.000`。
- 附近确认：`low=0.500/high=1.000`。
- 更早退出确认：`low=0.375/high=0.875`。
- 每条线固定新增1倍IC，估值T收盘确认，T+1活跃IC官方开盘买卖，持有期实际月滚。

## 3. Put定义与同步时点

- 底仓Put完整继承中证500研究主线：510500 ETF三个月、95%行权价Put，月度换约；v6二取三估值1.90/2.00/2.10对应交易时25%/50%/75%绝对Delta，MOM120<=0只提供最低25%，取较大值，不每日Delta再平衡。
- 主线每天产生T收盘目标、T+1共同交易日收盘执行。新增IC在T+1开盘买入的同一天，`sync_put_total_ic`于收盘把目标Delta乘以2；新增IC在T+1开盘卖出的同一天，收盘恢复1倍目标。
- 同步只放大当日主线已有目标：核心目标为0时，同步目标仍为0；不是新增仓永远单独持有Put。
- 模型Put使用理论定价路径；真实Put使用2022-09-19以来510500 ETF期权真实合约、真实收盘、成交量和整数张数路径。

## 4. 样本、资本与成本

- 模型层：2015-04-16至2026-08-14，必须报告full、10Y、5Y、3Y、1Y。
- 真实Put层：2022-09-19至2026-08-14；full、3Y、1Y可用，10Y/5Y必须显示N/A并注明样本不足。
- 固定底仓及每个新增IC单位各占30%保证金/缓冲；新增仓开启时Put前现金从70%降至40%；再扣Put市值后余额按年化3%计息。
- IC和Put每边1bp；新增IC月滚双边2bp。官方开盘/收盘不等于保证成交或容量证明。
- 归一化1倍IC名义，不模拟特定账户整数期货张数；真实Put按既有路径执行整数张数。

## 5. 同批路径

每个模型/真实层包含：

- `base_core_put`；
- 三条观察线×`core_put_only`；
- 三条观察线×`sync_put_total_ic`。

共14条组合路径。`core_put_only`必须与v4相同候选逐日奇偶；底仓必须与v21冻结路径奇偶。

## 6. 预注册判定

对每条估值线，`sync_put_total_ic`相对同线`core_put_only`必须满足：

### 模型层

1. full MaxDD至少改善1个百分点；五个窗口至少3个MaxDD改善。
2. 任一窗口MaxDD不得恶化超过1个百分点。
3. full/10Y/5Y CAGR最多落后1个百分点；3Y/1Y最多落后3个百分点。

### 真实Put层

1. full MaxDD至少改善1个百分点；full/3Y/1Y至少2个窗口MaxDD改善。
2. 任一可用窗口MaxDD不得恶化超过1个百分点。
3. full CAGR最多落后1个百分点；3Y/1Y最多落后3个百分点。

### 共同约束

- 模型目标Delta误差<=`1e-12`，真实整数张目标误差<=2个百分点。
- Put交易后市值不得超过同期Put前现金；最长成交顺延、零成交、实际Delta超过100%、最大名义倍数和Put累计成本必须报告。
- 因果、真实合约、收盘价、成交量、IC开盘、滚动与收益/现金恒等式必须通过。

判定顺序：先逐线判定；只有主观察线通过且另外两条至少一条也通过，才把同步Put列为该层首选。主线通过但无确认仅为`watchlist_peak_only`；主线失败则保留新增仓不加Put。

## 7. 必须输出

- `record.md`、`metrics_by_window.csv`、`window_metrics_wide.csv`
- `pairwise_put_management.csv`、`candidate_decisions.csv`
- `daily_candidates.csv.gz`、`annual_metrics.csv`
- `overlay_trade_audit.csv`、`put_trade_audit.csv`、`evaluation_schedule.csv.gz`
- `exposure_cost_delta.csv`、`timing_sync_audit.csv`
- `decision_summary.json`、`integrity_checks.json`、`data_manifest.json`、`command_log.txt`、`output_manifest.json`
- 参数工件标准五件套并通过complete strict。

## 8. 冻结输入

- `ic_valuation_overlay_exit_boundary_scan_v4.py`: `c9839805cc60710fbcbcbbb2045f40322082eb90710bab3cda0c4bb535c9c16d`
- `docs/ic_valuation_overlay_exit_boundary_scan_v4_spec.md`: `49721edc580711da6106fed3c691defae7eb93c8cdfe621550ed484a6973dfda`
- `outputs/ic_valuation_overlay_exit_boundary_scan_v4/output_manifest.json`: `c81ea42d2d0ba6834aece7dbbb87df633e14ec360a6d8ade4555475e3b7e1d3d`
- `outputs/ic_valuation_overlay_exit_boundary_scan_v4/daily_candidates.csv.gz`: `7bc9673cc010b9fedd977077a3d68535b67bf9927ae4a6faed0f9e2d7fbcfad9`
- `outputs/ic_valuation_overlay_exit_boundary_scan_v4/candidate_decisions.csv`: `25186963ee4b4b53b659a85904ca36c2ac99ff64ccde39ee19b76a97bea13c25`
- `ic_valuation_overlay_put_sync_v1.py`: `e9049f750e422d128c0378e4c311270ca32495b1d84c0b41588db0db7f460b36`
- `ic_510500_put_mom120_delta_floor_v21.py`: `e43a80085d3030d8ec87a6c89ad3be73331cf83f18226a9c88dfe7ea2299106e`
- `docs/ic_510500_put_mom120_delta_floor_v21_spec.md`: `a928a8f8b6d03d42cb4156c861653974aaccaae1953d9bbd23153f2e4e28c329`
- `outputs/ic_510500_put_mom120_delta_floor_v21/output_manifest.json`: `0d7fa231586d31aa0d0c093f4ca5624ae8fb6dd43c7bb794ae5b2310d699cef6`
- `outputs/ic_510500_put_mom120_delta_floor_v21/daily_candidates.csv.gz`: `11a15bffe6536b74399372ed928718751f7a4e0c552fd1393150d5c839ce2f2a`
- `outputs/ic_510500_put_mom120_delta_floor_v21/evaluation_schedule.csv.gz`: `dba99b2aa67a52c9b17a25e03e89325207aae6614bc651052b99168575a38d7a`
- `outputs/ic_510500_put_mom120_delta_floor_v21/trade_audit.csv`: `fb692bb0388018680891027ef3328c7b99abab86e9cac4f0a8b61d8e5437c22e`
- `data/ic_monthly_discount_roll_v1/cffex_ic_contracts.csv`: `4e02b889747112459125999382c3ff2fe89017aaea30df05e91bb2a7bc1e2104`
- `outputs/ic_monthly_discount_roll_v1/daily_nav.csv`: `bd575ee101b77791bfad3968e0cd221fb189624b8439d9e5dcecddcd944c092d`
- `outputs/ic_fixed_valuation_unbounded_score_v6/daily_unbounded_fixed_scores.csv.gz`: `34109cf7a5dec87c391f37b23cdc56cbb93611fd48ba7ba2929d74ca8a368b77`

任何冻结输入不匹配、正式输出已存在、路径数不是14、同步Put未在新增IC开平仓日收盘改变目标、基线/核心模式奇偶误差超过`1e-14`、因果或恒等式失败时必须停止。
