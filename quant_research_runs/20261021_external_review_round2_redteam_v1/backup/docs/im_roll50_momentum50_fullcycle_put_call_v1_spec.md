# IM 50/50 全周期 Put + 卖 Call 袖级对比 v1（预注册）

## 研究问题

在已固定的 `50% 裸滚 IM + 50% 动量择时 IM` 组合上，维持“动态 Put 仅保护持续存在的裸滚 50% 袖”，比较两种卖 Call 覆盖范围：

1. `call_bare_only`：卖 Call 仅覆盖裸滚 50% 袖，动量袖不卖 Call；
2. `call_both_sleeves`：裸滚袖与动量袖均按各自当日实际 IM 名义卖 Call。

另保留 `no_call` 作为同口径诊断基线。网格、额外 Put、Call 参数扫描均关闭。

## 冻结输入与样本

- 组合和 Put 基线：`outputs/im_roll50_momentum50_fullcycle_put_v1/daily_nav.csv.gz` 中 `put_fixed_0p5_core`。
- Call 冻结路径：`outputs/im_mo_call_daily_d10_threat_roll_v27/daily_candidates.csv.gz` 中 `front_d10_iv26_daily_threat5_up5_next1_max5`。
- Call 理论样本：2015-04-16 至 2022-07-21；真实 MO/IM 样本：2022-07-22 起；两段在同一交易日历拼接。
- 组合报告样本取上述输入的共同可用区间，截止日不得外推。
- 报告窗口：全周期、最近 10 年、5 年、3 年、1 年，均以共同截止日向前截取并落到可用交易日。

## 固定底层组合与 Put

- 裸滚袖：固定 0.5 倍 IM 名义。
- 动量袖：`0.5 × momentum_weight` 倍 IM 名义；`momentum_weight` 沿用已执行的 T+1 动量仓位，不重新生成或调参。
- 动态 Put：仅裸滚袖持有，为冻结 IM V2 动态 Put 路径的 0.5 倍；动量袖不加 Put。
- 期货成本、Put 成本、Put 现金占用完全沿用冻结 `put_fixed_0p5_core` 基线。

## 冻结卖 Call 规则

- 每日选择 D10 目标；合约隐含波动率至少 26%。
- 每 1 倍 IM 名义卖 2 张 MO Call；允许研究归一化分数张数，以便精确表示 0.5 倍袖及动量子袖。
- 标的上涨触及 5% 威胁阈值时买回并上移执行价至少 5%，同时换到相对旧到期日严格更晚的最近实际挂牌期限；最多救援 5 次。
- 不使用 PE gate、不使用 TP80、不使用网格 Call。
- Call 单边交易成本：每 1 倍 Call 篮子 1 bp。真实期权按收盘成交与结算盯市；理论期权按冻结模型收盘估值。

## 袖级执行与核算

- `call_bare_only` 的 Call 目标名义固定为 0.5 倍（仅在冻结 Call 规则自身持仓时存在）。
- `call_both_sleeves` 的 Call 目标名义为 `0.5 + 0.5 × momentum_weight` 倍。
- 当动量仓位改变时，Call 在同日收盘按新目标名义增减；日内损益归属于前一收盘后仍持有的旧名义，收盘增减仓支付单边成本。
- 若冻结 Call 合约同日换仓：旧合约按旧袖名义平仓，新合约按新袖名义开仓；两边分别计成本。
- 理论段使用冻结 Call 合约路径与 Black-Scholes 估值重建可变袖；真实段使用对应 MO 合约的真实收盘价、结算价及 IM 结算分母重建。
- 现金权重：沿用 Put 基线现金权重，再扣除相应 Call 保证金占用；不得把卖 Call 权利金重复记作现金收益。
- 日收益：先把期货/动量基线、0.5 倍 Put 损益与袖级 Call 损益相加，再依次乘冻结 Put 成本因子和 Call 成本因子。

## 预注册输出

- `daily_nav.csv.gz`：三种情形逐日收益、净值、Call 名义、损益、成本、保证金和现金权重。
- `metrics_by_window.csv`：全周期/10Y/5Y/3Y/1Y年化收益、年化波动、最大回撤、Calmar、Sharpe、累计收益。
- `comparison.csv`：两种 Call 情形相对 `no_call` 的指标差异。
- `validation.json`：样本边界、输入哈希、常数 1 倍 Call 重建对冻结 v27 的逐项误差、仓位集合、成本和现金审计。
- `record.md`、`run_manifest.json` 和输入哈希旁车文件。

## 通过标准与解释边界

- 常数 1 倍 Call 重建的逐日 PnL、成本、期末 mark 和保证金必须与冻结 v27 路径在数值容差内一致；否则停止正式输出。
- 不得出现负现金权重、负保证金或未来数据引用。
- 本轮只回答“动量袖是否也卖 Call”的历史对比，不据此调 Call 参数。
- 所有结果均为研究候选，不修改 V2 主线登记、Poe、实盘或下单面。
