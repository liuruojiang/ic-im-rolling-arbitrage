# v2 运行失败披露与记录修正

2026-09-04；不修改已冻结预注册、旧研究结果或Put引擎。

首轮运行完成全部25条主组/正式对照和12条次月/两个月联动诊断后，在target3m_T0_sync触发正式模型Put引擎异常：Model resize Delta too small for sizing。首轮未写出汇总CSV，因此不作为完成结果。

已备份新研究脚本至 .codex_backups/20260904_110937/。只将附加sync诊断的RuntimeError记录为blocked_candidates.csv并继续其他候选；不更改任何期货或Put计算规则、不填补失败结果、不绕过Delta保护。主组发生任何异常仍立即停止。

重跑全部候选以产生完整独立证据。失败诊断仍属于尝试网格，元数据保留attempted_candidates与blocked_candidates；它们不进入含有效指标的CSV，也不进入优选。记录失败时的日期、目标和Delta，不能把缺失结果当作零收益。

测试首轮两个失败均来自继承旧测试的异常文本断言（新版正确拒绝了日历不足和新合约无成交量）；只调整新测试对新异常文本的匹配，未放宽执行保护或修改旧测试。

第二轮保留了target3m_T0_sync的模型Delta保护（2015-12-17，entry_delta=4.8058e-10），随后target3m_T1_sync触发组合现金/收益保护Invalid portfolio cash/return。将同一个附加诊断失败记录边界扩展到组合现金核算，不改组合公式。每条成功路径另保存独立checkpoint，再完整重跑；两次未完成尝试均不作为最终结果。
