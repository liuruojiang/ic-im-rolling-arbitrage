# IM v1.3 Put Coverage Scope Ablation v1

状态：预注册研究规格；不是冻结主线，不是实盘授权。

## 1. Research question

在保持 IM v1.3 fixed-performance v5 的期货、动量、网格、Call、现金和执行口径不变时，检验当前仅覆盖核心仓的动态 Put 是否也应覆盖动量仓和/或网格仓。

## 2. Frozen implementation anchor

- 基准构建器：`build_ic_im_mainline_v1_3_fixed_performance.py::build_im`。
- 基准输出：`outputs/ic_im_mainline_v1_3_fixed_performance_v5/im_daily.csv.gz`。
- IM/MO 分量：`quant_param_scan_runs/20260823_im_grid160_put_carry_scan_v23/daily_outputs/daily_candidates.csv.gz`，只取 `current_4tier_mom3`；2022-07-22 前取 `model_avg_basis`，此后取 `real_actual_basis`。
- 目标路径：`outputs/im_mainline_v1_3/target_schedule.csv.gz`。
- 核心仓固定 0.5 倍；动量仓为 `0.5 * momentum_execution_weight`；网格仓为 `grid_held_eod`。
- Put 合约、期限、行权价、估值档位和 MOM120 下限不变；每个被覆盖袖都采用当日同一套动态 Put 比例。
- Call 仍仅覆盖核心仓；不得扩展 Call。

## 3. Candidate grid

1. `no_put`：诊断路径，不持有 Put。
2. `core_only_current`：当前基准，只覆盖 0.5 倍核心仓。
3. `core_plus_momentum`：覆盖核心仓与当日实际动量仓。
4. `core_plus_grid`：覆盖核心仓与当日实际网格仓。
5. `core_plus_momentum_plus_grid`：覆盖全部三类仓位。

Put 目标归一化数量为父路径 `put_qty` 乘覆盖的 IM 单位数。允许研究层出现分数张；这不是小账户整数合约可执行性证明。

## 4. Return, cash and cost model

- 期货毛收益、期货成本、Call 损益、Call 成本、30% 每倍 IM 保证金/缓冲和年化 3% 闲置现金收益完全沿用 fixed-performance v5。
- Put 日损益与市值按被覆盖 IM 单位数同比例缩放。
- Put 交易成本不直接缩放父路径成本；根据各候选的日末目标合约与归一化数量重新计算。相同合约按数量差计边数，换约按旧数量加新数量计边数，每归一化 MO 合约单边成本 `0.00005`。
- 在模型层起点及 2022-07-22 真实层起点重置前一持仓，避免跨数据层虚构平仓/开仓。
- 收益复利顺序与 v5 一致：期货和期权毛损益后依次扣期货、Put、Call 成本，再加剩余现金收益。若新增保护令现金为负，保留原始负现金并按同一 3% 年化率扣资金成本，仅作为融资诊断路径；不得截零、缩减 Put 或视为可行路径，预注册规则中的现金检查自动失败。
- 不计盘口价差、冲击、容量、涨跌停、动态保证金和整数合约映射误差。

## 5. Data and authority boundary

- 全样本为 2015-04-16 至共同截止日；Full/10Y/5Y 含上市前理论 Put 与 `model_avg_basis`，且模型贴水回填含未来信息，只作压力/稳定性参考。
- 真实 IM/MO 决策段从 2022-07-22 开始，是主判断依据。
- 3Y/1Y 如完全落在真实段，可作为真实段子窗口；仍须单列完整 `real_im_mo`。
- 本实验不得修改冻结 V2、v1.3 候选代码、Poe、账本、日报或任何交易配置。

## 6. Metrics and diagnostics

- 必报 Full/10Y/5Y/3Y/1Y 与 `real_im_mo` 的 CAGR、年化波动、仓库口径 Sharpe、最大回撤。
- 必报最差 1/5/20/60 日滚动收益、Put 总成本、平均/最大 Put 数量、Put 活跃日、最低现金。
- 对真实段最大回撤标明峰值与谷底；比较增加保护后改善是否只来自单一事件。
- 基准 `core_only_current` 必须与 fixed-performance v5 的逐日收益、现金、Put 数量在容差内一致。
- 重算的父 Put 成本必须在模型层和真实层分别与原始成本一致。

## 7. Pre-registered decision rule

对动量仓或网格仓新增 Put 保护，只有在真实 IM/MO 段同时满足以下条件时才记为“有证据支持”：

- 最大回撤相对 `core_only_current` 至少改善 1.00 个百分点；
- CAGR 相对基准不降低超过 0.50 个百分点；
- Sharpe 不低于基准；
- 最差 20 日和 60 日收益均不恶化；
- 现金不为负，且结论不是明显由单日异常或模型段单独驱动。

否则保留当前“不覆盖该袖”的研究结论。若全覆盖满足而单袖不满足，仅记为组合交互证据，不自动授权全覆盖。

## 8. Reproducibility

- 正式入口：`scan_im_v13_put_coverage_scope_v1.py`。
- 结果目录：`quant_param_scan_runs/20260903_ic_im_rolling_arbitrage_im_v1_3_fixed_performance_v5_im_put_coverage_scope_put_coverage_scope/`。
- 规格冻结后写入 SHA-256；正式运行只写新研究目录。
