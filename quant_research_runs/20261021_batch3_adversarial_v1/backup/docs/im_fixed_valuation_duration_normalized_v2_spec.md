# 中证1000固定经济估值时长归一确认 v2 预注册规格

- 版本：`im_fixed_valuation_duration_normalized_v2`
- 预注册日期：2026-08-18
- 性质：中证1000估值本体第二层；由v1迁移失败触发的二次确认，不是独立样本，不构成IM、MO、Put、网格或Call回测，也不构成实盘授权。
- 研究顺序：保持v1的经济刻度、分数定义和阈值网格完全不变，只检验“独立事件门槛应否按可用历史长度归一”。

## 1. 研究问题

v1把IC约19.7年历史的“全样本至少5个独立启动段”原样放到IM约10.8年估值历史，结果二取三在2.45—2.60形成平台，而三项均值因全样本只有4段失败。v2在不移动任何经济锚点的前提下回答：

1. 把事件次数按可用月数换算后，均值与二取三是否形成共同结构平台；
2. 通过的事件是否同时出现在样本前后两段，而不是由单一时期贡献；
3. v1其余覆盖率、局部门槛样本、多因子证据和两定义一致性结论是否保持不变。

本版禁止根据v1的2.45—2.60结果缩窄扫描网格，也禁止使用中证1000后续收益、IM贴水、MO损益、最大回撤或交易结果选择阈值。

## 2. 冻结输入

| 输入 | SHA-256 |
| --- | --- |
| `im_fixed_valuation_unbounded_transfer_v1.py` | `56549fc43b1fd70cc1f0dafe1a5b0b6895b1bbcb82529122eaa6e9a70f6bfda3` |
| `docs/im_fixed_valuation_unbounded_transfer_v1_spec.md` | `8d3eeec74f09588dcb1425b88e29adee9e4c1df7901108d9d2b1f254c0734007` |
| `docs/im_fixed_valuation_unbounded_transfer_v1_postrun_audit.md` | `6088e49637c6d3e33c818b9c58f2fa3e8f44861206a9a5967dff091f278a2497` |
| `outputs/im_fixed_valuation_unbounded_transfer_v1/daily_unbounded_fixed_scores.csv.gz` | `1e186ffc943ebcc16769cb86c79fd817bb1d754660f90d8d8a4b9d74a479a49f` |
| `outputs/im_fixed_valuation_unbounded_transfer_v1/monthly_unbounded_fixed_scores.csv` | `1b173ae29df570825836af7c9c97b6c851254bc7eca8dd91fc45af6546db3cbc` |
| `outputs/im_fixed_valuation_unbounded_transfer_v1/economic_boundary.csv` | `fb300003bc512054b79b47c0f722d1d0bb50a48b95ec30b0172a92317cffb065` |
| `outputs/im_fixed_valuation_unbounded_transfer_v1/factor_structure_summary.csv` | `37536c528c113f6982e91d7ca9c46262ab7ec5df90e474416d15917a14ef201b` |
| `outputs/im_fixed_valuation_unbounded_transfer_v1/price_index_context.csv` | `1b04a18efe8b73f5becb164d8276b5ed07b216f647931873771815983ec6ac8c` |
| `outputs/im_fixed_valuation_unbounded_transfer_v1/raw_threshold_map.csv` | `32748e84e963643bd6000671067f7be81f17775cbe05f2268c10d240543c0465` |
| `outputs/im_fixed_valuation_unbounded_transfer_v1/threshold_selection.csv` | `a0da0b7f6023ff8e276721cc4dfe8cce178cb443fd57820b6dccdc7efb7b2da6` |
| `outputs/im_fixed_valuation_unbounded_transfer_v1/vintage_invariance.csv` | `7d14106574cc389e54a556b546598c3bec61ef17b2212e37e9563201ccae2ffe` |
| `outputs/im_fixed_valuation_unbounded_transfer_v1/integrity_checks.json` | `1b8fc2b4f3984cc53b2f94ccb6c64ab234c642af9414a701baff9c7aec2c6967` |
| `outputs/im_fixed_valuation_unbounded_transfer_v1/decision_summary.json` | `49c89290091dd4cc6a1f5f2213eb01cd6ccee3d6db5aa9b800aa8e89b7130fbe` |
| `outputs/im_fixed_valuation_unbounded_transfer_v1/output_manifest.json` | `1f0dad5e100d135e1748cd30629e0e84d04f2ae44cd4de71b6c607e04071a2d7` |

冻结样本仍为2015-10-19—2026-08-17，共2,634个交易日和131个月末；不得重新下载、刷新或倒推指数发布满一年前的估值。

## 3. 保持不变的经济定义与网格

- `PB压力 = (PB - 1.50) / 0.50`；
- `ERP压力 = (4.50% - ERP) / 1.50%`；
- `股息压力 = (3.00% - 过去一年已实现股息贡献) / 1.00%`；
- `unbounded_mean`为三项均值，`unbounded_median`为二取三中位数；均不截断；
- 阈值仍为1.50—3.00、步长0.05，两家族共62候选；
- 强制窗口仍为全样本、最近10年、5年、3年、1年。

## 4. 唯一允许改变的事件门槛

IC v6冻结样本有236个月末，其事件门槛为全样本至少5段；最近10年有121个月末，门槛至少2段。v2按月数同比换算，且向上取整：

- `IM全样本最低段数 = ceil(IM全样本月数 × 5 / 236)`；
- `IM最近10年最低段数 = ceil(IM最近10年月数 × 2 / 121)`。

按冻结样本，本版预期机械门槛为全样本至少3段、最近10年至少2段。若实际载入月数与131/121不一致，完整性检查必须失败，不得动态接受另一门槛。

“独立启动段”沿用v1定义：月末状态从未启动变为启动时记为一段；样本首月若已启动，按可观察首段计一次。

## 5. 新增时间广度确认

131个月末按时间顺序机械拆分：前`ceil(131/2)=66`个月与后65个月。对每个阈值、每个家族：

- 先在完整月序列上计算启动月`active & ~active.shift(1)`；
- 再按启动月所在位置计入前段或后段；
- 前段至少1个启动、后段至少1个启动，才通过时间广度门槛；
- 跨越分割点的同一段只能按其启动月计入一侧，不能重复计数。

该约束用于防止“降低次数后只剩一个历史阶段”。不得查看结果后移动分割点、改成自然年度或放宽为任一侧通过。

## 6. 保持不变的其余结构门槛

1. 各家族最近10年月度启动率5%—30%；
2. 各家族局部门槛带`[T-0.10,T+0.10]`不少于8个月末；
3. 均值启动月中至少90%满足`unbounded_median >= 1.00`；
4. 均值与中位数状态全样本及最近10年Jaccard均不低于0.70；
5. 最近10年两定义启动率绝对差不超过10个百分点。

各家族核心通过必须同时满足：时长归一事件数、时间广度、覆盖率、局部样本；均值还需满足多因子证据。共同通过还必须满足两定义一致性。连续至少3个相邻0.05点全部通过才构成共同结构平台。

多个平台时选择点数最多者；点数相同选择阈值更高者；机械中心取平台中位点，偶数点取较低者。这一选择规则保持v1不变。

## 7. 决定与解释边界

- 有共同平台：`duration_normalized_transfer_supported_secondary`。平台不少于5点为`wide_stable`，3—4点为`narrow_stable`。
- 无共同平台：`duration_normalized_transfer_not_supported` / `reject`，下一层才允许研究IM专属经济刻度。

即使形成平台，也必须标记为“v1失败触发的二次确认”，不能声称完成了独立样本验证。它只可成为后续IM估值分档研究候选；不得自动启动Put、网格或Call，更不批准实盘。

## 8. 输出与完整性

正式输出至少包括：

- 时长归一门槛定义、前后段边界及逐阈值启动月审计；
- v2阈值选择表、v1/v2门槛差异表、平台决定和当前状态；
- 62候选×5窗口标准扫描表和价格指数背景；
- 冻结输入哈希、样本行数、日期、门槛公式、分段互斥、事件合计、候选完整性和非结果选择检查。

扫描中的收益、波动、Sharpe和MaxDD仍只是同窗中证1000价格指数背景，不是策略收益。本版没有持仓、成本、保证金或执行时点。

- 正式目录：`outputs/im_fixed_valuation_duration_normalized_v2/`，首次生成后不得覆盖；
- 参数工件：`quant_param_scan_runs/20260818_1000_im_fixed_valuation_duration_normalized_v2_valuation_body_duration_normalized_episode_gate/`；
- 状态：`PRE-REGISTERED / SECONDARY CONFIRMATION / RESEARCH ONLY / NOT LIVE APPROVED`。
