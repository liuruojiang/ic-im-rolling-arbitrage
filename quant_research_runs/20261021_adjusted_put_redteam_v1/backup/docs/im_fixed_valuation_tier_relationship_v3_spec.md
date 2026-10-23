# 中证1000固定经济估值分档与双定义关系 v3 预注册规格

- 版本：`im_fixed_valuation_tier_relationship_v3`
- 预注册日期：2026-08-18
- 性质：中证1000估值本体第三层；只冻结估值严重度分档和均值/二取三关系，不构成IM、MO、Put、网格或Call回测，不构成实盘授权。
- 前置结论：v2形成2.45—2.60四点共同平台、机械中心2.50；本版不重新扫描经济阈值，也不读取任何交易结果。

## 1. 研究问题

v2只建立了高估平台，尚未回答如何把连续分数转成不陡峭的严重度档位，也没有明确三项均值与二取三谁负责实际状态。本版回答：

1. 用共同平台下沿、中心和上沿构建0/1/2/3档后，状态覆盖与切换是否可解释、是否过度抖动；
2. 二取三单独主判、均值单独主判、两者取较低档、两者取较高档之间有多大差异；
3. 是否可以把二取三冻结为执行主状态、均值仅作非绑定结构确认；若不可以，是否需要两者共同确认。

禁止使用中证1000后续收益、IM贴水、MO价格、Put损益、最大回撤或交易成本挑选状态关系。

## 2. 冻结输入

| 输入 | SHA-256 |
| --- | --- |
| `im_fixed_valuation_duration_normalized_v2.py` | `4cc0238b025d3d7c369e37d889f2fc624c85aa3925f5903decd36a3daea6b6f4` |
| `docs/im_fixed_valuation_duration_normalized_v2_spec.md` | `8b623e3ee8f061bdc54efdf845ee4a518c588882550d6658887e33ac6abeded8` |
| `docs/im_fixed_valuation_duration_normalized_v2_postrun_audit.md` | `a97181738c22b2f24d0c0e097ca6956086694465f056291762d1f570f7748e8d` |
| `docs/ic_510500_put_research_mainline_v1.md` | `6da92d886f184277cffcdbbbd706d43ee057c7e1d4502410b8c7b12cde8eb4b5` |
| `outputs/im_fixed_valuation_duration_normalized_v2/daily_unbounded_fixed_scores.csv.gz` | `1e186ffc943ebcc16769cb86c79fd817bb1d754660f90d8d8a4b9d74a479a49f` |
| `outputs/im_fixed_valuation_duration_normalized_v2/monthly_unbounded_fixed_scores.csv` | `1b173ae29df570825836af7c9c97b6c851254bc7eca8dd91fc45af6546db3cbc` |
| `outputs/im_fixed_valuation_duration_normalized_v2/economic_boundary.csv` | `fb300003bc512054b79b47c0f722d1d0bb50a48b95ec30b0172a92317cffb065` |
| `outputs/im_fixed_valuation_duration_normalized_v2/factor_structure_summary.csv` | `37536c528c113f6982e91d7ca9c46262ab7ec5df90e474416d15917a14ef201b` |
| `outputs/im_fixed_valuation_duration_normalized_v2/price_index_context.csv` | `1b04a18efe8b73f5becb164d8276b5ed07b216f647931873771815983ec6ac8c` |
| `outputs/im_fixed_valuation_duration_normalized_v2/raw_threshold_map.csv` | `32748e84e963643bd6000671067f7be81f17775cbe05f2268c10d240543c0465` |
| `outputs/im_fixed_valuation_duration_normalized_v2/duration_gate_definition.csv` | `900fcf2cc277da37117fba659425072339fda0fe2983bd66346e2efa48b2e9b3` |
| `outputs/im_fixed_valuation_duration_normalized_v2/temporal_episode_audit.csv` | `edb14daa07584d5ca0898a4e948290618dc9e66b983998e8b740913b22cbaeb1` |
| `outputs/im_fixed_valuation_duration_normalized_v2/threshold_selection_v2.csv` | `554b176c5dd6665f6d36330c250829c0c3b0238d4ee0eb2ddd3cfb21238903ac` |
| `outputs/im_fixed_valuation_duration_normalized_v2/current_state.csv` | `b6c8af52f257b34caa836e230bb642a83c350595b8cd7c08c9f6a37f638ab87e` |
| `outputs/im_fixed_valuation_duration_normalized_v2/decision_summary.json` | `3dd22dc83a64dc68342827685561432bd24122cfe0a25c0b5e7857ff92e926e9` |
| `outputs/im_fixed_valuation_duration_normalized_v2/integrity_checks.json` | `3a6bdae037704d97c25070935cc7d92f07f9e13f454c60d52eab810e73d7eb2a` |
| `outputs/im_fixed_valuation_duration_normalized_v2/output_manifest.json` | `c88aea0ff1093826093ee16e886711ecec2a53e4cd109c7e1a3c64c3e3039db4` |

冻结样本仍为2015-10-19—2026-08-17，共2,634个交易日、131个月末。IC研究主线只提供“二取三比均值更适合作为可解释执行状态、分档可映射严重度”的架构参考；其1.90/2.00/2.10数值和Put结果不得迁移到IM。

## 3. 固定分档

分档点完全由v2共同平台机械取得，不新增阈值搜索：

- 0档：分数`<2.45`；
- 1档：`2.45 <= 分数 < 2.50`；
- 2档：`2.50 <= 分数 < 2.60`；
- 3档：分数`>=2.60`。

2.45为共同平台下沿，2.50为机械中心，2.60为共同平台上沿。未来Put层可以把1/2/3档解释为25%/50%/75%保护严重度候选，但本版只输出0/1/2/3状态，不生成Put数量、Delta或交易。

边界使用大于等于进入更高档；不加滞后、确认天数、移动平均或回看分位。日度状态用于评估潜在抖动，月末状态用于结构确认。

## 4. 四种关系候选

对每个交易日和月末先分别计算`mean_tier`与`median_tier`，再形成：

1. `median_primary`：直接使用二取三档位；事前主候选；
2. `mean_primary`：直接使用均值档位；诊断候选；
3. `consensus_min`：`min(mean_tier, median_tier)`，两定义取较低档，相当于更严格的共同确认；后备候选；
4. `either_max`：`max(mean_tier, median_tier)`，任一定义较高即取较高档；宽松诊断候选，不得晋升为主规则。

二取三主候选的经济理由是：每个累计档位均严格等价于PB、ERP、股息三个固定经济条件至少满足两项，不允许单一极端分量独自触发；均值仍用于验证二取三是否偏离同一估值周期。

## 5. 结构指标

每个候选在全样本、最近10年、5年、3年、1年输出：

- 日度和月末0/1/2/3档占比、平均档位、非零覆盖；
- 累计达到1/2/3档的覆盖率；
- 日度和月末状态切换次数、年化切换次数、升级/降级次数、一步以上跳档次数；
- 非零状态段数、最长持续期；
- 与`median_primary`的精确档位一致率和平均绝对档差。

另在全样本与最近10年输出均值—二取三：

- 日度/月末精确档位一致率；
- 线性加权Cohen kappa；
- 绝对档差不低于2档的比例；
- 4×4混淆矩阵及最大分歧日期；
- 每个累计档位的全样本/最近10年启动段、前后半段启动数。

年化收益、波动、Sharpe和最大回撤只使用同窗中证1000价格指数背景，同一窗口四候选必须完全一致，语义仍为`underlying_price_index_context_only_no_strategy_return`。

## 6. 预注册选择顺序

### 6.1 二取三主候选通过条件

`median_primary`须同时满足：

1. 均值与二取三月末精确档位一致率：全样本和最近10年均不低于85%；
2. 日度精确档位一致率：全样本和最近10年均不低于80%；
3. 月末绝对档差不低于2档的比例：全样本和最近10年均不高于5%；
4. 二取三的1/2/3档累计门槛均保持v2的全样本至少3段、最近10年至少2段、前后半段各至少1次启动；
5. 二取三日度年化切换次数不超过24次，排除每天反复跨线的病态状态。

全部通过，则冻结`median_primary`为后续IM Put层的估值状态候选；均值只作非绑定确认，不在实盘状态上再次取交集。

### 6.2 后备共同确认

若二取三只因第1—3项定义一致性条件失败，则检查`consensus_min`：

- 最近10年累计1档覆盖仍在5%—30%；
- 累计1/2/3档均满足全样本至少3段、最近10年至少2段、前后半段各至少1次启动；
- 日度年化切换次数不超过24次。

通过则冻结`consensus_min`为后备估值状态候选。若二取三因自身事件、时间广度或抖动失败，或`consensus_min`也失败，则本版不得冻结估值分档，结论为`no_executable_tier_relationship`。

`mean_primary`与`either_max`只作覆盖和分歧诊断，无论表现如何都不得越过上述顺序晋升。禁止看到状态分布后修改85%/80%/5%/24次门槛。

## 7. 当前状态与执行边界

输出冻结日四候选档位及三项原始经济条件。即使状态候选通过：

- 只表示后续MO Put回测有了固定估值输入；
- 不等于已证明分档能降低回撤或提高收益；
- 不自动替代旧固定1.75纸面规则；
- 不生成交易建议，不接入自动或人工下单。

## 8. 完整性与产物

- 固定输入哈希、v2平台2.45—2.60与中心2.50、四候选公式、边界归属、日/月行数和五窗口完整性全部自动检查；
- 二取三每个累计档位须与原始三项经济条件至少二取三逐日完全等价；
- 均值/二取三档位、min/max恒等式、混淆矩阵合计、事件前后段互斥、历史时点无未来数据全部检查；
- 正式输出目录`outputs/im_fixed_valuation_tier_relationship_v3/`首次生成后不得覆盖；
- 参数工件目录`quant_param_scan_runs/20260818_1000_im_fixed_valuation_tier_relationship_v3_valuation_body_mean_median_tier_relation/`；
- 状态：`PRE-REGISTERED / RESEARCH ONLY / NOT LIVE APPROVED`。
