# IC/IM 动量 Put v1.3-r6 定值绩效

状态：`research_only_fixed_reference_not_live_authority`；不生成订单；r5账本只通过独立迁移器进入r6。

## 结论

- IC规则和逐日收益完全沿用v1.3，没有变化。
- IM真实MO区间：r5 CAGR 47.01%、Sharpe 1.462、MaxDD -25.89%；r6分别为 48.37%、1.517、-23.30%。
- IM变化：CAGR +1.37%，Sharpe +0.055，最大回撤改善 +2.59%。
- IM 5Y/10Y为N/A：真实MO历史不足，不用理论Put补齐。

## 规则边界

- IC动量Put保持v1.3-r5估值-only规则；IM动量Put使用完整current_4tier_mom3目标并采用独立合约账本。
- IC/IM网格仓仍无Put；IC无Call；IM Call仍只覆盖核心仓。
- 结果已计既有期货/期权成本与3%现金收益，未计价差、冲击、容量、涨跌停、动态保证金和实际账户整数映射。
