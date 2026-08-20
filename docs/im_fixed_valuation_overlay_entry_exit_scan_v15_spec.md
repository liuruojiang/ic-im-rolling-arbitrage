# IM 固定经济估值增仓开仓/退出阈值扫描 v15 预注册规格

冻结日期：2026-08-19（Asia/Shanghai）  
状态：预注册研究；未批准实盘。

## 1. 研究问题

在冻结的 IM 主线——固定 1 倍滚 IM、`reconstructed_valmom_floor3` 三个月 95% MO Put、每月换约、MOM120 负向时每 1 倍 IM 最低 3 张 Put——之外，增加一个独立的估值增仓子系统：

1. 中证1000固定经济估值分数足够低时增加 1 倍 IM；
2. 分数恢复到较高区间时退出新增 1 倍 IM；
3. 新增仓持有期间继续滚 IM、获得指数与贴水路径；
4. 本层新增仓不增加 Put，只回答估值开平仓信号本身是否有效；同步 Put 必须在阈值冻结后的新版本单独测试。

本版不得改动底仓 Put、绝对动量、保证金、现金收益或 IM 主线。结果只构成研究证据。

## 2. 为什么正式扫描固定分数、相对分位只作诊断

收益计算前的状态几何审计已经冻结：

- 57个月连续滚动分位首次可用于 2020-07-01；在 2020-07-01—2026-08-17 之间，开仓分位 10%—40%、退出分位 60%—95% 的所有有效滞回组合最多只有一次完整周期，即 2022 年进入、2026 年退出；
- 同一套新估值体系的固定经济分数 `unbounded_median_knot` 可从 2015-10-19 使用；在本版冻结网格内，144 组中 72 组至少完成两次周期、17 组至少完成三次周期。

因此：

- 正式选参轴为固定经济分数，不是旧版 `fixed_risk`，也不是历史价格点位；
- 57个月分位仅同批运行 `P25/P75`、`P30/P70` 两条诊断线，禁止按其收益晋升；
- 本版不把固定轴和相对轴再取交集，避免用单一 2022—2026 周期优化双重门控。

## 3. 冻结输入与底仓

### 3.1 底仓

- 底仓候选：v14 的 `reconstructed_valmom_floor3`；
- 模型层：2015-04-16—2026-08-14，中证1000全收益指数作为上市前/理论 IM 收益代理，月度滚动成本已包含在底仓；
- 真实层：2022-07-22—2026-08-14，CFFEX 活跃 IM 合约真实开盘、结算与滚动路径；
- Put：完整继承 v14，不重新定价、不改变目标张数、不因新增仓放大；
- 每 1 倍 IM 占用 30% 保证金/缓冲，底仓 Put 市值从剩余现金扣除，现金年化 3%。

### 3.2 估值

- 固定轴：v3 的 `unbounded_median_knot`，即 PB、ERP、过去一年已实现股息贡献三个固定经济压力刻度的二取三中位数；分数越低表示估值越便宜；
- 相对诊断轴：v7 冻结的 57 个月弱 ECDF 分位，只使用当月以前完整月末历史；
- 估值 T 日收盘确认，T+1 执行；不得使用 T+1 或更晚数据生成 T 日信号。

## 4. 固定参数网格

- 开仓阈值：`1.40—2.10`，步长 `0.05`，共15点；
- 退出阈值：`2.10—2.60`，步长 `0.05`，共11点；
- 只保留 `退出阈值 - 开仓阈值 >= 0.30`；
- 合计144组正式候选；不得在看到收益后补点、删点或改变步长。

状态机只有0/1两档：空仓且分数 `<=开仓阈值` 时发出买入信号；持仓且分数 `>=退出阈值` 时发出卖出信号。最大只增加 1 倍 IM，不加码、不翻倍、不设置止损或持有期上限。

相对分位诊断固定为：

- `relative_P25_P75_diag`：分位 `<=25%` 开仓、`>=75%` 退出；
- `relative_P30_P70_diag`：分位 `<=30%` 开仓、`>=70%` 退出。

## 5. 执行、收益与成本

### 5.1 模型层

- T+1 用当日中证1000指数开盘近似执行；为与全收益指数底仓一致，模型开盘单位定义为 `前一交易日全收益指数收盘 × 当日价格指数开盘 / 前一交易日价格指数收盘`；
- 买入日获得开盘到收盘收益，卖出日只获得前收盘到开盘收益，持续持有日获得全收益指数收盘到收盘收益；
- 模型层是历史风险代理，不声称复制上市前 IM 贴水。

### 5.2 真实层

- T+1 在底仓当日活跃 IM 合约官方开盘买卖；买入日获得开盘到结算收益，卖出日获得前结算到开盘收益，持续持有日获得底仓真实 IM 日收益；
- 若某候选在 IM 上市前已经处于持仓状态，2022-07-22 在首个真实交易日开盘建立新增仓并计单边成本，标记为 `initial_listing_carry`；
- 官方开盘价和成交量只证明历史报价存在，不等于保证成交或容量证明。

### 5.3 资本与费用

- 新增 IM 开启时，总 IM 名义从1倍变为2倍，Put 前现金从70%降为40%；再扣底仓 Put 市值，余额按年化3%计息；
- IM 买入/卖出每边1bp；持有新增仓跨底仓月滚日计双边2bp；
- 底仓和 Put 成本严格沿用 v14；组合收益为底仓 IM 毛收益、新增 IM 毛收益和底仓 Put 损益相加后统一乘以期货成本与 Put 成本。

## 6. 强制窗口与指标

模型层报告 `full / last_10y / last_5y / last_3y / last_1y`；真实层报告 `full_real / last_3y / last_1y`，10年和5年明确为样本不足。

每条路径输出 CAGR、年化波动、Sharpe、MaxDD、Calmar、回撤峰谷日期、持仓比例、开仓/退出次数、完成周期、持有日数、累计交易与滚动成本、相对固定1倍底仓差异。另输出逐年指标和每次交易明细。

## 7. 预注册通过门槛

固定分数候选必须同时满足：

### 7.1 事件与状态门槛

1. 模型层至少2个完成周期，开仓日期至少跨2个自然年；
2. 模型层新增仓持有比例不高于70%，排除退化为长期2倍 IM；
3. 真实层至少1个完成周期；
4. 无未执行的历史挂单、无重复买入或空仓卖出、T信号到T+1执行因果无误。

### 7.2 模型层收益风险门槛

相对固定1倍底仓：

1. full、10Y、5Y CAGR 分别至少增加1.5、1.0、0.5个百分点；
2. 3Y、1Y CAGR 均不得落后超过1个百分点；
3. full、10Y、5Y MaxDD 均不得恶化超过3个百分点，且 full MaxDD 不深于 -40%；
4. full Calmar 必须高于底仓。

### 7.3 真实层交叉门槛

相对真实固定1倍底仓：

1. full_real、3Y CAGR 均不得落后超过1个百分点；
2. full_real、3Y MaxDD 均不得恶化超过3个百分点。

真实层只作交叉约束，不按真实样本的最高收益单独选参。

## 8. 宽度与机械选择

- 先标记全部通过第7节的候选；
- 候选必须位于网格内部，开仓轴上下各0.05、退出轴上下各0.05的四个相邻候选都存在、都通过第7节，且模型 full Calmar 至少保留中心的80%，才算 `wide_stable`；
- 在 `wide_stable` 中机械选择模型 full Calmar 最高者；仍并列时依次选择完成周期更多、持仓比例更低、开仓阈值更低、退出阈值更低者；
- 若有硬门槛通过者但没有宽平台，只能 `watchlist_peak_or_ridge`；若无硬门槛通过者，则 `no_fixed_threshold_candidate`；不得用相对分位诊断线替代。

## 9. 完整性与必须产物

必须检查：冻结 SHA-256、正式目录不可覆盖、144组固定候选、2条相对诊断、模型/真实底仓与 v14 逐日奇偶误差不超过 `1e-14`、真实活跃合约开盘/结算完整、模型开收盘恒等式、收益与现金恒等式、30%/60%保证金上限、Put 市值不穿透剩余现金、信号因果、交易状态机、候选日期唯一、无 NaN 和收益不低于 -100%。

必须输出：

- `record.md`
- `daily_candidates.csv.gz`
- `metrics_by_window.csv`、`window_metrics_wide.csv`、`annual_metrics.csv`
- `scan_surface.csv`、`candidate_decisions.csv`、`ridge_width.csv`
- `overlay_trade_audit.csv`、`overlay_cycle_summary.csv`
- `drawdown_audit.csv`、`state_geometry_audit.csv`
- `decision_summary.json`、`integrity_checks.json`、`data_manifest.json`
- `command_log.txt`、`output_manifest.json`
- 参数扫描标准五件套并通过 `complete --strict`。

正式输出目录：`outputs/im_fixed_valuation_overlay_entry_exit_scan_v15/`，首次生成后不可覆盖。  
参数工件目录：`quant_param_scan_runs/20260819_im_fixed_valuation_overlay_entry_exit_scan_v15/`。

## 10. 冻结输入 SHA-256

| 输入 | SHA-256 |
| --- | --- |
| `im_mo_reconstructed_floor_selection_v14.py` | `55c27b4b4bcdbf814f2f7edb3636d9f2ffa2149b20141e9eeefa773747d796d6` |
| `docs/im_mo_reconstructed_floor_selection_v14_spec.md` | `7a0bcbc15019a75b1527c06c15be9cd0a57f6b660ec5ce1114b09de5356c9bc0` |
| `docs/im_mo_reconstructed_floor_selection_v14_postrun_audit.md` | `f67041096d088bba0f27e74c265c62b8d3cab701e9be23f5d76d7e9a68da88e1` |
| `outputs/im_mo_reconstructed_floor_selection_v14/daily_candidates.csv.gz` | `c013e2ffdbe5435ae87601af319a3e263850e7d55f31e25fa3eee8a7ebb56614` |
| `outputs/im_mo_reconstructed_floor_selection_v14/data_manifest.json` | `d6caa2000d4706a3da1b3ad0c6f6207b56df428c0808b051012fb7d36c1c9212` |
| `outputs/im_mo_reconstructed_floor_selection_v14/decision_summary.json` | `c5a13a8ce868ffd49c2148f0682decdf8a2f2a157febabe4c076a4e60bb0e878` |
| `docs/im_mo_put_research_mainline_v1.md` | `0caafc8a48518babd68108e067d3b61e4cda4694b7ac2b3c90dfda8718330738` |
| `im_valuation_window_ladder_scan_v7.py` | `29d54597690115710020cdcc1bd0d84d57e1bdbb3f281d88f5b90912b6015d1a` |
| `docs/im_valuation_window_ladder_scan_v7_spec.md` | `2a92ef1f1708d6930e8d56d9d0ed84f5de3c2bf5c57288d8e44ff6b4e21cde6f` |
| `outputs/im_valuation_window_ladder_scan_v7/daily_window_percentiles.csv.gz` | `844839dbc1cc704aa4e88ead12f617044a9e3e7c05c0338692488f5982d8cdd9` |
| `outputs/im_valuation_window_ladder_scan_v7/output_manifest.json` | `c043559b301605a002139312bf47e5e82d9bb2ec9f8b88e73aee2f0a47a6c1c9` |
| `im_fixed_valuation_tier_relationship_v3.py` | `4e5c36ab2dcc5ec9d8e6d3ba3c8dd4ee9e2bf705c54c620390326efab967fe4d` |
| `docs/im_fixed_valuation_tier_relationship_v3_spec.md` | `dbc096f7dfbbfec2724f6889e0000564b283c8b52dc00e73da18e430ba3759c5` |
| `outputs/im_fixed_valuation_tier_relationship_v3/daily_tier_states.csv.gz` | `dd91b80172553a1dbe53e79bdc5870ca32af7e7ed5171c001e356ad28c9e3912` |
| `outputs/im_fixed_valuation_tier_relationship_v3/output_manifest.json` | `d428a21b4d8e40ab4c9a4146f5607aaaac64ae382df2d94fb6e3594878c75dbf` |
| `outputs/im_monthly_roll_3m_lowest_put_v1/daily_nav.csv` | `0a3719ade254a32eaf1886dc7d00e9d84aa93498e9a2fecf2868cbefefb60b99` |
| `data/im_monthly_roll_3m_lowest_put_v1/cffex_im_contracts.csv` | `6f19f04824026e3cf7e4fc7ebfeb20f60637e53bfc3caebc616fae47794f3cc0` |
| `data/im_mo_csi1000_put_protection_battery_v6/sina_sh000852_index.csv` | `9d3995a7189137fee79e5aaa2a58aced57101a1329f1236aca8a0adc86babe74` |
| `data/ic_im_valuation_risk_premium_forecast_v3/csindex_000852.csv` | `e42b94ad52a39687a5a0d92fe7f3c28481f34420bac6ac0d0c62ffcdf0e68bf9` |
| `data/ic_im_valuation_risk_premium_forecast_v3/csindex_H00852.csv` | `6483caa2cba5c2bf7e300c949380ddc8ffeaf7877152679e3754a99d841ae40a` |

