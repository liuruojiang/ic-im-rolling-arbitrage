# IC + 510500 ETF Put 无界固定估值门控 v18 预注册规格

- 版本：`ic_510500_put_unbounded_valuation_gate_v18`
- 预注册日期：2026-08-18，Asia/Shanghai
- 状态：研究回测，未批准实盘
- 前置估值版本：`ic_fixed_valuation_unbounded_score_v6`
- 前置执行权威：`ic_510500_put_close_execution_full_retest_v17`

## 1. 研究问题

v6只根据固定经济含义、覆盖和事件数选出1.85—2.15的无界估值结构平台，没有读取IC或Put收益。本层首次把该估值本体接入既有IC三个月95% Put月换、收盘执行路径，检验均值、二取三中位数及两者交集能否在不显著损失收益的前提下改善回撤。

不得根据本次Put结果改变v6公式、平台、阈值网格、期限、虚值度、保护比例或成交时点。

## 2. 冻结输入与实现锚点

| 输入 | SHA-256 |
| --- | --- |
| `ic_fixed_valuation_unbounded_score_v6.py` | `f0b615d097fde668bb6896a9dd0b884f7bcf164091d332f9d9b44c4993e9a825` |
| `outputs/ic_fixed_valuation_unbounded_score_v6/daily_unbounded_fixed_scores.csv.gz` | `34109cf7a5dec87c391f37b23cdc56cbb93611fd48ba7ba2929d74ca8a368b77` |
| `ic_510500_put_close_execution_full_retest_v17.py` | `24c1702082e08f6cdf1538a879586ac684480dba8890d9ea3649c34a36150629` |
| `outputs/ic_510500_put_close_execution_full_retest_v17/data_manifest.json` | `c8b48171674bf25323bd809509e0d92e680aad61238515a0155e3ae1a0bf6bbc` |
| `ic_510500_put_absolute_momentum_protection_tool_v13.py` | `8e8514e9c9d2985b2b77b35ec7469e6ea243be1a477e40e052e0a852216ae058` |
| `ic_510500_put_absolute_momentum_protection_tool_v11.py` | `2149d52637304bf09a2d1be674ff3c761d8d56033a9391a2ed46f3387ed3d4f7` |
| v17中v13组件`daily_candidates.csv.gz` | `6ad15a734b60860004a1d96e0fb8fdd8a8658657b6e6a20bb4abef25f2bd1f04` |

程序须复用v13/v11的`3m_monthly_exit_m95`工具与v17内存收盘替换：510500 ETF和期权日线的`open`在执行引擎内替换为同日`close`，模型层的现货、波动率、利率和股息执行状态也替换为收盘状态。原文件和既有输出不得修改。

v17清单及v13组件内嵌源清单中的原始数据哈希须在运行前复核；任何变化均停止运行。

## 3. 冻结候选

正式估值候选共9条：

- `mean_190`、`mean_200`、`mean_210`：`unbounded_mean_knot >= 1.90/2.00/2.10`；
- `median_190`、`median_200`、`median_210`：`unbounded_median_knot >= 1.90/2.00/2.10`；
- `intersection_190`、`intersection_200`、`intersection_210`：均值和中位数同时达到相同阈值。

同批基准共3条：

- `no_put`：滚IC及现金，不持有Put；
- `old_fixed175_only`：旧离散固定风险分`old_fixed_risk >= 1.75`，只用于估值本体替换比较；
- `paper_fixed175_or_mom120`：旧离散固定风险分不低于1.75，或中证500全收益指数120日绝对动量不高于0；这是既有纸面备选的执行参照，不参与新估值家族的机械选择。

模型与真实层各12条，共24条路径。阈值取自v6平台内预先指定的下邻点、中心和上邻点；本版不扫描1.85、1.95、2.05、2.15或其他结果驱动点。

## 4. 信号与执行

- 日评；T日收盘确认，下一共同交易日收盘执行；样本首日沿用既有引擎的初始状态例外。
- 保护比例仅0%或100%，1倍IC名义，不因30%保证金放大方向仓位。
- Put工具固定`3m_monthly_exit_m95`：目标到期月约为执行日后3个月；信号持续时在每个IC月度换仓日卖旧买新；信号关闭则在下一可执行共同交易日收盘提前退出；不是持有到期。
- 模型层执行价严格等于执行日中证500现货收盘的95%；真实层在目标月份中选择执行价/510500 ETF收盘最接近95%、且同日Put收盘价为正、成交量为正的合约，平手按较低执行价及较小证券代码。
- 所有真实非到期交易腿必须匹配同日同合约正close和正成交量；无可执行合约时按既有引擎顺延，最长5个交易日。

## 5. 资本、成本与样本

- IC及Put每边1bp；Put月换卖旧买新计双边成本。
- 30%净资产作为保证金/缓冲；70%现金按净年化3%计息；Put市值从可计息现金扣除。
- 模型层：2015-04-16—2026-08-14；模型Put为Black–Scholes/QVIX理论代理，不是可成交历史价。
- 真实层：2022-09-19—2026-08-14；使用冻结510500 ETF Put日线close和volume，日线close不是收盘集合竞价盘口或容量保证。
- 估值输入可测自2007-01-15；策略共同截止日固定2026-08-14，不使用v6中其后的行。

## 6. 指标、窗口与判定

每条路径必须显示含现金的CAGR、年化波动、仓库口径Sharpe和MaxDD：全样本、最近10年、5年、3年、1年。真实层因历史不足的10年和5年显示N/A，不得省略。

每条新估值候选相对同层`no_put`计算收益差和回撤改善。沿用后层默认容忍：

1. 模型全样本/10年/5年CAGR落后不超过1个百分点，3年/1年落后不超过3个百分点；全样本MaxDD必须改善，并在五个窗口至少三个改善；
2. 真实层全样本CAGR落后不超过1个百分点，3年/1年落后不超过3个百分点；全样本MaxDD必须改善，并在三个可用窗口至少两个改善；
3. 模型和真实层均须至少20个保护日、至少一笔真实建仓，并通过成交完整性；
4. 家族稳定性要求同一候选至少有一个0.10相邻阈值也满足上述同线门槛；孤立点最多`peak_only/watchlist`；
5. 新候选能否替换旧固定估值，另与`old_fixed175_only`同窗展示，但不得因为旧线较弱而降低no-Put门槛；`paper_fixed175_or_mom120`只作不同信号结构的诊断参照。

无人通过为`keep_default/reject`；有相邻支持但真实期约4年且无独立OOS时最多`watchlist`，不得直接晋升实盘。

## 7. 完整性与正式产物

- v6日度均值、中位数和旧固定分逐日哈希冻结；候选信号须直接由这些列生成，不得重估参数。
- `paper_fixed175_or_mom120`的目标信号及模型/真实no-Put须与v17对应组件逐日一致，最大误差不超过`1e-14`。
- 24条路径齐全、无候选日期重复、无未来信号、无提前执行、收益无缺失且均大于-100%。
- 输出候选日线、交易审计、信号历史、五窗口指标、年度指标、暴露/成本、真实合约选择、收盘价成交审计、基准奇偶、候选判定、数据清单、命令记录和独立post-run audit。
- 正式输出：`outputs/ic_510500_put_unbounded_valuation_gate_v18/`，首次生成后不得覆盖。
- 参数工件：`quant_param_scan_runs/20260818_500_ic_510500etf_put_ic_510500_put_unbounded_valuation_gate_v18_ic_3m_monthly_exit_m95_close_unbounded_valuation_family_threshold/`。
- 研究状态固定`RESEARCH_ONLY_NOT_LIVE_APPROVED`。
