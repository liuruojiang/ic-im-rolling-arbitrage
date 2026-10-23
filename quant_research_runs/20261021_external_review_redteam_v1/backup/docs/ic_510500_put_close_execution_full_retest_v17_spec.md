# IC + 510500 ETF Put 收盘执行全量重测 v17 预注册规格

冻结日期：2026-08-17  
研究状态：仅研究，未批准实盘

## 研究问题

此前 IC Put 保护研究将估值或动量信号在 T 日收盘确认后，于 T+1 日开盘交易 510500 ETF Put。开盘集合竞价和早盘流动性不足可能使该成交假设失真。本版只把 Put 买入、卖出、换月、调仓和行权价选择改为 T+1 收盘执行，重测所有曾成功形成正式结果的 IC 版本；滚 IC 的结算价、换月、手续费和 70% 现金年化 3% 假设均不改变。

## 冻结重测范围

- 来源版本：`ic_510500_put_proxy_validation_v1`、`full_cycle_valuation_v2`、`rolling_continuous_valuation_v4`、`absolute_valuation_stress_v5`、`v4_monthly_tenor_rerun_v6`、`persistent_stress_hold3m_v7`、`tail_value_gate_v8`、`extreme_valuation_gate_v9`、`extreme_valuation_absolute_momentum_v10`、`absolute_momentum_protection_tool_v13`、`dynamic_valuation_absolute_momentum_front95_v14`、`absolute_momentum_horizon_scan_front95_v15`、`dynamic_lower_threshold_front95_v16`。
- 不纳入：没有成功正式产物的 v3、v11、v12；它们不具备可冻结的完整候选结果。
- 每个来源版本的信号定义、评估频率、保护档位、期限、价外程度、持有/换月规则和候选集合原样保留。
- 包含模型层及真实 510500 ETF Put 层；所有候选均与同层 no-Put 比较。

## 唯一允许改变的执行层

- 信号仍为 T 日收盘确认；Put 仓位于下一可执行共同交易日收盘成交。
- 真实层：行权价选择使用执行日 510500 ETF 收盘价；Put 买卖使用同日官方/已冻结日线收盘价；要求收盘价为正且当日成交量为正。无可执行合约时整笔调整顺延。
- 模型层：Black–Scholes 的现货、波动率、利率和股息输入使用执行日收盘状态；成交日不计入新仓的盘中收益。
- 持有期间仍按日收盘盯市；到期内在价值、严格三周期持有、月度强制换月及不提前卖出规则不变。
- IC 基线、保证金/现金、Put 成本率及分母口径不变；不得把收盘执行结果与其他信号修订混合。

## 实现与审计方法

- 复用各冻结来源版本的真实入口和数据加载器，在内存中把“执行参考 open”替换为同日 close；源文件、冻结规格和旧输出均不修改。
- 每个来源版本写入本版独立子目录；随后汇总旧开盘结果与新收盘结果，逐候选检查日期、候选集合、no-Put 逐日一致性和执行腿价格。
- 真实层每一笔非到期交易都必须能回查到同日同合约收盘价；模型层每一笔新仓成交日收益应为零（期货基线收益除外）。
- 每个用户可见表必须显示全样本、最近10年、5年、3年、1年的年化收益和最大回撤；数据不足显示 N/A。

## 预注册判定

- 本版是执行假设纠错，不以寻找新最优参数为目标。
- 原结论只有在收盘执行后仍满足原版本门槛且邻点/期限支持不恶化时，才可称为“收盘口径下仍成立”。
- 任一路径若真实层无成交或信号从未触发，标为 `not_testable`，不得当作失败或成功。
- 即使结果改善，也维持“未批准实盘”；晋升需另行完成盘口滑点、收盘集合竞价容量、保证金和实盘数据审计。

## 正式产物

- 入口：`ic_510500_put_close_execution_full_retest_v17.py`
- 输出：`outputs/ic_510500_put_close_execution_full_retest_v17/`
- 参数记录：`quant_param_scan_runs/20260817_ic_510500_put_close_execution_full_retest_v17/`

