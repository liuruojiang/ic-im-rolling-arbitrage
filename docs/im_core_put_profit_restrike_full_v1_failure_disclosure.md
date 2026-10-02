# IM核心Put盈利兑现完整组合v1失败披露

日期：2026-09-16。

`20260916_ic_im_im_v1_3_current_counterfactual_full_portfolio_core_put_profit_realization_multiple_baseline_2x_3x` 不再作为2倍/3倍完整组合结论使用。

原因：真实期权引擎先计算 `replace`，后读取 `pending_profit`，使报告标记的“T日收盘触发、T+1收盘执行”实际成为T+2收盘执行；理论引擎则为T+1，导致真实与理论执行口径不一致。基线不受影响，但盈利兑现候选失效。

纠正后的统一T+1、T+2压力及2.5/3/3.5倍邻域结果见：

`quant_param_scan_runs/20260916_ic_im_im_v1_3_current_counterfactual_corrected_full_portfolio_core_put_profit_restrike_robustness_2_5x_3x_3_5x_t1_t2/`

旧产物保留用于失败审计，不删除、不覆盖；不得再引用其盈利兑现候选数值作参数判断。
