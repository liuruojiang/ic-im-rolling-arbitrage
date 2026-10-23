# IC v1.3 网格 Put 合并目标交易账本重放 v2

预注册日期：2026-09-03（Asia/Shanghai）  
状态：研究重放；未批准实盘；v1 独立分袖账本及所有正式主线只读。

## 修正目的

v1 用核心、动量、网格三个独立 510500 Put 账本做袖级归因。IC 的实际可执行实现会把同一标的、同一期限规则的目标 Delta 合并后，用一个账本选约和整数张取整。因此本版在不调任何参数的前提下，补做唯一的实际实现对照：

1. `authoritative_current_combined`：现行目标 `0.5×完整V2 + 0.5×动量权重×纯估值档`；
2. `operational_current_plus_grid_combined`：在现行合并目标上增加 `网格状态×完整V2目标`。

这不是看到 v1 结果后的参数优化，只是把“独立归因账本”和“实际合并下单账本”明确分开。

## 固定条件

- IC v1.3 的核心、动量、0.375/1.000 网格、期货成本、30%保证金/缓冲、3%现金收益和 IC 无 Call 全部不变。
- 网格 Put 使用完整 V2 风险目标；网格 T+1 开盘成交，同日收盘才调整合并 Put。
- 510500 ETF 约 3 个月、95% Put；T 收盘目标、T+1 共同交易日收盘执行；每边 1bp；不每日 Delta 再平衡。
- 真实期权段自 2022-09-19；此前理论 Put 仅为扩展参考。
- 新旧两条路径都从原始期权数据完整重放选约、整数张取整、延期、换月、盯市和成本，不缩放父 Put 收益。

## 判定

网格 Put 只有在真实 Full 最大回撤至少改善 1.0 个百分点、真实 Full CAGR 落后不超过 1.0 个百分点、真实 3Y 最大回撤不恶化超过 1.0 个百分点，且混合 Full/5Y 不出现重大反向失效时，才列为 watchlist；否则保持现行网格无 Put。

## 产物

- 入口：`run_ic_v13_grid_put_operational_combined_replay_v2.py`。
- 输出：`outputs/ic_v13_grid_put_operational_combined_replay_v2/`。
- 扫描记录：`quant_param_scan_runs/20260903_ic_v13_grid_put_operational_combined_replay_v2/`。
- 本研究不修改生产、冻结规格或实盘权限。
