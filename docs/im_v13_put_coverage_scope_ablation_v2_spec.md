# IM v1.3 Put Coverage Scope Ablation v2

状态：预注册收盘时序敏感性；研究用途，不是冻结主线或实盘授权。

本规格继承 `im_v13_put_coverage_scope_ablation_v1_spec.md` 的数据、五个候选、Put规则、成本、窗口、指标和决策门槛，只修正新增袖覆盖的日内时序：

- 核心仓 Put 损益、日末数量、市值和成本与 fixed-performance v5 完全一致。
- 动量仓和网格仓的目标数量、Put市值与交易成本在当日收盘调整；因此当日 Put 损益只能按上一交易日已经持有的新增覆盖单位计算。
- 模型层首日和 2022-07-22 真实层首日的新增覆盖持仓从零开始；不得跨数据层继承。
- 期货仓仍按原 v1.3 的当日实际执行权重计算，不作改变。
- 若 v1 的同日缩放与本 v2 收盘时序得出不同决策，以 v2 作为较保守的主结论，v1 只保留为乐观边界。
- Put成本仍根据当日日末目标合约/数量变化重新计边；负现金仍按 v1 的融资诊断处理并自动判为不可行。

正式入口：`scan_im_v13_put_coverage_scope_v2.py`。

结果目录：`quant_param_scan_runs/20260903_ic_im_rolling_arbitrage_im_v1_3_fixed_performance_v5_im_put_coverage_scope_execution_timing_put_coverage_scope_timing/`。
