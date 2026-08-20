# IM + MO Call 5%威胁救援最大历史价格指数代理检验 v26r2 预注册规格

冻结日期：2026-08-19（Asia/Shanghai）  
状态：修正v26r1底仓指数口径；长期合成诊断；未批准实盘。

## 1. 修正原因

v26r1在最终交叉检查时发现代理底仓使用中证1000全收益指数，与用户此前明确要求“股指期货按价格指数口径研究”不一致。v26r1正式结果和工件永久保留，不覆盖。本版只把代理底仓日收益从`H00852`全收益指数改为`000852`价格指数；Call估值仍用同一价格指数，其他规则、样本、成本、候选、敏感性和判定门槛全部继承v26r1，不得据新结果改参。

## 2. 固定样本与波动率

- 无估值轴：2007-01-04—2026-08-14；
- PE20/60轴：2012-06-29—2026-08-14，只用官方滚动PE与市场共同有效日；
- 2015-04-16以后完全沿用冻结QIVX模型；此前用中证1000价格指数60日RV乘固定P25/P50/P75 `IV/RV60`倍数0.9984611590392641/1.1352106814268557/1.2692067075266782；
- 2007—2014指数、2012—2014 PE和2015年前IV均为发布前回算或后见代理，只作诊断。

## 3. 固定5%救援

正常入场仍要求拟卖Call自身IV不低于26%。旧Call每日收盘满足`K/S-1 <= 5%`时，T日冻结，T+1收盘买回旧Call并卖出严格下一挂牌到期月、行权价至少为旧行权价105%的Call；救援豁免IV26。每链连续最多5次，第6次或无下一有效期限时平仓并暂停至下一月度评估。无TP80、分批止盈、急跌向内移仓、风险度扩仓或参数扫描。

## 4. 代理底仓与成本

- 1倍中证1000官方价格指数日收益代理滚IM方向收益，不含历史IM贴水；
- 代理底仓月度换仓2bp、首日1bp；Call每边1bp，救援换仓合计2bp；
- 70%资本上限减去Call保证金后的余额按净年化3%计息；
- 不含Put、真实IM保证金波动、买卖价差、冲击成本、涨跌停或挂牌流动性。

组合绝对CAGR/MaxDD不是可交易IM历史；正式判断只比较同一价格指数代理底仓的有无救援增量。

## 5. 候选与判定

每个P25/P50/P75场景运行`normal_no_rescue`、`normal_threat5`、`pe20_60_no_rescue`、`pe20_60_threat5`，共12条。必须报告full/10Y/5Y/3Y/1Y CAGR与MaxDD、年度收益、Call损益/成本、最大和95分位Delta、最大保证金、救援/停止/最大连续次数。

两轴分别判定，完全继承v26r1：full/10Y CAGR落后不超过1pp，5Y/3Y/1Y不超过3pp；最大Call Delta至少下降10pp；每场景至少5次救援；最终待执行、资本穿透和审计错误为0。两轴均通过、仅一轴通过、IV假设敏感、均失败分别记为`extended_price_proxy_directionally_supported_both_axes`、`extended_price_proxy_axis_dependent`、`extended_price_proxy_iv_assumption_sensitive`、`extended_price_proxy_not_supported`。

## 6. 正式路径

- 脚本：`im_mo_call_threat_roll_extended_price_proxy_v26r2.py`；
- 输出：`outputs/im_mo_call_threat_roll_extended_price_proxy_v26r2/`，首次不可覆盖；
- 参数工件：`quant_param_scan_runs/20260819_new_strategy_research_im_mo_call_threat_roll_extended_price_proxy_v26r2_price_index_p25_p50_p75_fixed_threat5_split_pe_history/`；
- 上游：冻结v26r1脚本/规格/正式输出、v25r2真实结果和同一数据快照。

冻结后不得改写。本版只修正价格指数口径，不替代2022年后的官方IM/MO真实样本。
