# IC v1.3 Put 覆盖范围最终正式指标汇总 v3

预注册日期：2026-09-03（Asia/Shanghai）  
状态：度量口径修正版；未批准实盘；v1/v2 交易账本和正式主线只读。

## 目的

v1 独立分袖重放与 v2 实际合并目标重放的逐日收益、选约、整数张、换月和成本已通过对账，但其新汇总脚本把 Sharpe 写成 CAGR/年化波动，且非 Full 窗口未剔除切片首日。仓库正式 fixed-performance v5 使用：

- Full 包含首个正式日收益；
- 其余窗口从请求起点筛选后剔除第一行，避免把窗口前一日持仓收益当作窗口内新起点；
- 年化波动为日收益样本标准差（`ddof=1`）乘 `sqrt(252)`；
- Sharpe 为日均收益除以日收益样本标准差乘 `sqrt(252)`。

本版只从 v1/v2 已冻结逐日结果重算指标，不改变任何交易、信号、期权目标或候选。

## 最终候选

1. `no_put`；
2. `core_only_single_ledger`：只有 0.5 倍核心 Put；
3. `current_core_momentum_combined`：现行核心+动量合并 Put；
4. `current_plus_grid_combined`：在现行合并目标上增加网格完整 V2 Put。

动量 Put 的边际比较为 3 对 2；网格 Put 的边际比较为 4 对 3。网格 Put 判定门槛沿用 v2：真实 Full 最大回撤至少改善 1pp、真实 Full CAGR落后不超过1pp、真实3Y最大回撤不恶化超过1pp，且混合 Full/5Y无重大反向失效。

## 窗口与边界

- 混合参考段 2015-04-16—2026-08-14：2022-09-19 前理论 Put，之后真实 Put；Full/10Y/5Y/3Y/1Y。
- 真实期权段 2022-09-19—2026-08-14；Full/3Y/1Y；5Y/10Y 为 N/A。
- 输出只用于研究判断，不修改 IC v1.3、冻结 V2、Poe、日报或交易权限。

## 产物

- 入口：`finalize_ic_v13_put_scope_metrics_v3.py`。
- 输出：`outputs/ic_v13_put_scope_final_metrics_v3/`。
- 标准扫描记录：`quant_param_scan_runs/20260903_ic_v13_put_scope_final_metrics_v3/`。
