# IC 固定估值增仓开仓/退出阈值扫描 v2 冻结规格

冻结日期：2026-08-18（Asia/Shanghai）  
状态：预注册研究；未批准实盘。

## 1. 研究问题

上一版 `low=1.00/high=2.00` 的新增 1 倍 IC 于 2018-12-20 买入、至 2026-05-12 才卖出，完整经历 2021—2024 年回撤。本版只回答：能否通过更合理的固定经济估值开仓/退出阈值，让新增仓在底仓主要回撤前退出，同时保留低估期间滚 IC 的收益。

## 2. 冻结基线与唯一变化

- 同路径基线：`model_l190_mom25`，即中证500 Put 研究主线的固定 1 倍滚 IC 底仓；三个月、95% 行权价、月换 510500 ETF Put，估值 1.90/2.00/2.10 对应交易时 25%/50%/75% 绝对 Delta，MOM120<=0 只提供最低 25%。
- 新增仓：估值分数小于等于开仓阈值时买入 1 倍 IC；估值分数大于等于退出阈值时卖出。T 日收盘确认，T+1 活跃 IC 官方开盘成交；持有中随活跃 IC 月度滚动。
- 本层新增仓不增加 Put。底仓 Put 路径逐日固定不变，以隔离“估值退出规则”的作用。若本层出现稳健阈值平台，额外 Put 管理只能在新版本复测。
- 每 1 倍 IC 使用 30% 保证金/缓冲；底仓+新增仓开启时现金权重从 70% 降至 40%；剩余现金年化 3%。期货每边 1bp，滚动一次双边 2bp。
- 归一化 1 倍名义，不模拟整数合约账户、冲击成本或开盘容量。

## 3. 正式样本与长历史诊断

### 3.1 正式组合层

- 样本：2015-04-16 至 2026-08-14。
- 使用 CFFEX IC 真实活跃合约官方开盘/结算与实际月度滚动。
- Put 为既有 v21 模型路径；本版不重新估算 Put，也不使用 2022 年后的真实 Put 短样本挑阈值。
- 必须报告 full、last_10y、last_5y、last_3y、last_1y 的 CAGR、年化波动、Sharpe 和 MaxDD。

### 3.2 2007 年以来估值周期诊断

- 样本：`daily_unbounded_fixed_scores.csv.gz` 的最大可用区间（预期 2007-01-15 至 2026-08-14）。
- 只使用中证500价格指数收盘和固定经济单位二取三分数；T 日收盘信号、T+1 下一个指数交易日收盘生效。
- 该层没有上市前 IC 贴水、真实合约开盘、Put 或保证金路径，仅用于审计估值状态、完整周期数和参数是否依赖 2015 年后的单一事件；不得当作正式组合收益或交易建议。

## 4. 预注册网格

- 开仓阈值：0.000 至 1.500，步长 0.125，共 13 点。
- 退出阈值：1.250 至 2.250，步长 0.125，共 9 点。
- 只保留 `退出阈值 - 开仓阈值 >= 0.500`，共 96 个有效组合。
- 当前规则 `1.000/2.000` 必须在同批运行并作为阈值替换基线。
- 不得在看到结果后补插更细阈值；如边界或平台值得续研，必须新建版本。

## 5. 预注册判定

候选相对当前 `1.000/2.000` 必须同时满足：

1. 正式全样本 MaxDD 至少改善 10 个百分点。
2. 五个强制窗口中至少三个窗口 MaxDD 改善。
3. full/10Y/5Y CAGR 相对当前规则最多落后 3 个百分点；3Y/1Y 最多落后 6 个百分点。本版允许更宽收益容忍，是因为硬要求全样本回撤改善至少 10 个百分点。
4. full/10Y/5Y CAGR 均高于同路径的固定 1 倍 IC+Put 底仓。
5. 在底仓全样本最大回撤的峰值日，候选新增仓必须已经退出；底仓最大回撤峰值日从同批底仓 NAV 机械计算，不能手工指定。
6. 2015 年正式样本至少 2 个已完成开仓—退出周期；2007 年指数诊断至少 3 个已完成周期。
7. 资本、因果、官方开盘、滚动成本、收益恒等式和冻结底仓奇偶校验全部通过。

筛选顺序：先应用硬门槛，再在通过者中选择 full CAGR 最高者。若无人通过，输出 Pareto/诊断观察点，但 `selected_candidate=null`，不得事后降低门槛。

## 6. 宽度与稳健性

- 宽度指标：候选相对 `1.000/2.000` 的正式全样本 MaxDD 改善。
- 推荐点在开仓轴和退出轴两侧均须有相邻有效点，且相邻点保留推荐点至少 80% 的回撤改善；相邻点还须满足第 5 节的收益容忍和底仓回撤峰值日已退出。
- 必须报告相邻支持、平台/脊线、是否位于网格边界。薄峰不能晋升。

## 7. 必须输出

- `record.md`
- `metrics_by_window.csv`、`window_metrics_wide.csv`
- `scan_surface.csv`、`candidate_decisions.csv`、`ridge_width.csv`
- `daily_candidates.csv.gz`、`overlay_trade_audit.csv`、`overlay_cycle_summary.csv`
- `index_proxy_metrics_by_window.csv`、`index_proxy_daily.csv.gz`
- `drawdown_overlap_audit.csv`、`annual_metrics.csv`
- `data_manifest.json`、`integrity_checks.json`、`decision_summary.json`、`command_log.txt`、`output_manifest.json`
- 参数扫描目录的 `scan_summary.csv`、`window_metrics.csv`、`scan_meta.json`、`record.md`、`command_log.txt`，并通过 complete strict 检查。

## 8. 冻结输入

- `ic_valuation_overlay_put_sync_v1.py`: `e9049f750e422d128c0378e4c311270ca32495b1d84c0b41588db0db7f460b36`
- `docs/ic_valuation_overlay_put_sync_v1_spec.md`: `7cf83eea40fb8d4aafb6c05a955be010e8b0ad26898c589033fb87a42b6935c3`
- `outputs/ic_valuation_overlay_put_sync_v1/output_manifest.json`: `2167faf26135d5f23bd53421826381b30e3229924fac6df6860468a881ab04ae`
- `outputs/ic_valuation_overlay_put_sync_v1/daily_candidates.csv.gz`: `0423f4f7d9abf1a8de5b15bdb4264cbd46227c4408d32133a3730921c0bb0f18`
- `outputs/ic_valuation_overlay_put_sync_v1/integrity_checks.json`: `12c89231e566b2afd30d9765e7ed1aa7f4a44143bc34c1c89f8b2d8b3a3a70d5`
- `outputs/ic_510500_put_mom120_delta_floor_v21/daily_candidates.csv.gz`: `11a15bffe6536b74399372ed928718751f7a4e0c552fd1393150d5c839ce2f2a`
- `data/ic_monthly_discount_roll_v1/cffex_ic_contracts.csv`: `4e02b889747112459125999382c3ff2fe89017aaea30df05e91bb2a7bc1e2104`
- `outputs/ic_monthly_discount_roll_v1/daily_nav.csv`: `bd575ee101b77791bfad3968e0cd221fb189624b8439d9e5dcecddcd944c092d`
- `outputs/ic_fixed_valuation_unbounded_score_v6/daily_unbounded_fixed_scores.csv.gz`: `34109cf7a5dec87c391f37b23cdc56cbb93611fd48ba7ba2929d74ca8a368b77`

任何冻结输入不匹配、正式输出目录已存在、底仓逐日奇偶误差大于 `1e-14`、因果或回报恒等式失败时，运行必须停止。首次正式输出目录不可覆盖。
