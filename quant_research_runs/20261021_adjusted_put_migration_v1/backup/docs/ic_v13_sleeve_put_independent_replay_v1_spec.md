# IC v1.3 动量仓与网格仓 Put 独立账本重放 v1

预注册日期：2026-09-03（Asia/Shanghai）  
状态：研究重放；未批准实盘；不修改 IC v1.3、冻结 IC/IM V2、Poe、日报或交易配置。

## 研究问题

保持 IC v1.3 的核心、动量、0.375/1.000 独立网格、期货成本、30%保证金/缓冲、3%组合现金收益和 IC 无 Call 全部不变，只比较 Put 覆盖范围：

1. `no_put`：诊断用，不持有 Put；
2. `independent_core_only`：仅 0.5 倍核心仓持有完整 IC V2 Put；
3. `independent_core_momentum`：核心与动量分别维护独立 Put 账本；
4. `independent_core_grid`：核心与网格分别维护独立 Put 账本；
5. `independent_all`：核心、动量和网格各自维护独立 Put 账本；
6. `authoritative_current_combined`：现行 IC v1.3 的核心+动量合并目标单账本，用作正式路径奇偶基准。

本研究不得把父 Put 的逐日收益按仓位比例缩放。每个被保护袖必须从原始 510500 ETF Put 数据独立选约、整数张取整、盯市、换月、调仓、延期和计费。

## 冻结仓位与 Put 目标

- 核心仓固定 0.5 倍 IC；其 Put 目标为 `0.5 × full_v2_target_delta`。
- 动量仓为 `0.5 × momentum_execution_weight` 倍 IC；权重严格复用 IC v1.3。其 Put 目标为 `0.5 × momentum_execution_weight × valuation_only_target_delta`，不使用 MOM120 下限。
- 网格仓为 0 或 1 倍 IC；状态严格复用 IC v1.3 的 0.375 买入、1.000 卖出路径。其 Put 目标为 `grid_held_eod × full_v2_target_delta`，即复制完整 V2 风险规则；在深度低估期估值档通常为零，实际新增保护主要来自 MOM120 不高于零时的下限。
- 现行合并账本目标仍为核心目标加动量目标；不得改变正式基准定义。
- 网格 T+1 开盘成交；同日收盘才按收盘目标调整网格 Put，不回填开盘至收盘的期权损益。

## 期权、数据与执行

- 复用正式 IC 引擎：510500 ETF、约 3 个月、95% 行权价 Put，与 IC 月换同步月度换约。
- T 日收盘形成目标，T+1 共同交易日收盘执行；不每日 Delta 再平衡。
- 每边 Put 成本 1bp；真实层使用 2022-09-19 起的 510500 ETF Put 历史收盘、成交量与整数张数，缺乏可执行报价时沿用正式顺延规则。
- 2022-09-19 前 Put 为 Black-Scholes/QVIX 理论代理，只用于扩展情景，不得解释为真实成交。
- 所有底层期货收益、动量成本、网格收益与成本均从 IC v1.3 fixed-performance v5 的正式构建链重新组成，不修改信号。
- 每 1 倍 IC 使用 30%保证金/缓冲；Put 日终市值从剩余现金扣除，余额按年化 3%计息；现金不得为负。

## 指标与窗口

- 混合正式参考段：2015-04-16 至 2026-08-14，2022-09-19 前理论 Put、之后真实 Put；报告 Full、10Y、5Y、3Y、1Y。
- 真实期权段：2022-09-19 至 2026-08-14；报告 Full、3Y、1Y；5Y/10Y 明确 N/A（真实历史不足）。
- 指标：CAGR、年化波动、仓库口径 Sharpe、最大回撤、最终净值、Put 成本、市值占用、目标/实际 Delta、交易次数与最大执行延期。

## 预注册判断

- 动量 Put 的边际证据用 `independent_core_momentum` 对比 `independent_core_only`；这只是对现行规则的独立账本复核，不自动改写已登记的 IC v1.3。
- 网格 Put 的主比较为 `independent_all` 对比 `independent_core_momentum`，并用 `independent_core_grid` 对比 `independent_core_only`作交叉归因。
- 网格 Put 只有在真实期权 Full 最大回撤至少改善 1.0 个百分点、真实 Full CAGR 落后不超过 1.0 个百分点、真实 3Y 最大回撤不恶化超过 1.0 个百分点，且混合 Full/5Y 不出现相反方向的重大失效时，才列为 `watchlist`；否则保持网格无 Put。
- 任一日期错位、现行路径奇偶误差大于 `1e-12`、非有限收益、现金为负、目标恒等式误差大于 `1e-12`、真实期权数据或成交审计失败，结论改为 `rerun_required`。

## 产物与权限边界

- 正式入口：`run_ic_v13_sleeve_put_independent_replay_v1.py`。
- 首次输出：`outputs/ic_v13_sleeve_put_independent_replay_v1/`，不可覆盖。
- 扫描记录：`quant_param_scan_runs/20260903_ic_v13_sleeve_put_independent_replay_v1/`。
- 本轮仅生成研究证据，不生成订单，不提供自动或人工下单建议，不切换任何线上入口。
