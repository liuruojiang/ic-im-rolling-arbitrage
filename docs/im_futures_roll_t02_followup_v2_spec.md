# IM 期货 T-2 换仓后续扫描 v2：预注册规格

冻结日期：2026-09-03（Asia/Shanghai）  
状态：研究候选；未获准实盘；不改写冻结 V2 主线或 v1.3-r6 研究信号。

## 1. 研究问题

在 v1 已完成 T-1/T-3/T-5/T-10/T-15 扫描后，用户指定补测 T-2。由于 v1 已冻结，本版独立预注册并在同一真实数据、成本和收益口径下重跑 T-1/T-2/T-3，避免把 T-2 结果事后写回原扫描。

分别回答：

1. 对最近更晚挂牌月，T-2 是否优于 T-1 或 T-3？
2. 对约 2 个月和约 3 个月目标月，T-2 是否形成更好的收益—回撤折中？

## 2. 数据与实现锚点

- 复用 `run_im_futures_roll_tenor_timing_scan_v1.py` 的真实数据读取、冻结基线校验、合约到期日、挂牌月选择、收盘价换仓、收益和指标函数，不复制或近似改写公式。
- IM逐合约数据：`data/im_monthly_roll_3m_lowest_put_v1/cffex_im_contracts.csv`，上游为中金所官方历史行情月包。
- 指数基准：`outputs/im_monthly_discount_roll_v1/daily_nav.csv` 中的中证指数官方000852数据。
- 固定样本：2022-07-22至2026-08-14，共986个交易日。原始合约价格不做连续合约复权；时区Asia/Shanghai。
- v1冻结机制基线必须再次通过结算价、净收益和现金层逐日校验。

## 3. 候选

换入期限沿用 v1 的精确定义：

- `next_listed`：严格更晚的最近实际挂牌到期月；
- `target_plus_2m_nearest_listed`：执行日加2个日历月为目标，选择最近挂牌月，同距取较早月；
- `target_plus_3m_nearest_listed`：执行日加3个日历月为目标，选择最近挂牌月，同距取较早月。

每种期限统一重跑最后交易日前 1/2/3 个实际交易日收盘换仓，共9条可交易近似路径；另含 `expiry_settle_front` 冻结机制校验基线，总计10条。禁止运行后再新增其他提前量。

T-2 的语义是：若最后交易日为正常开市的周五，则在周三收盘换仓；遇节假日时按实际交易日倒数，不按自然日。

## 4. 执行、成本与收益

- 初始日按最近到期IM官方收盘价建立1倍方向名义并计1bp。
- 换仓日先取得旧合约截至当日收盘的收益，再按同日官方收盘价平旧开新，合计2bp；下一交易日起持有新合约。
- 收盘价是统一成交近似，不是盘口可成交保证；不计额外冲击、经纪商加收保证金或价格限制成交失败。
- 30%净资产作为每1倍期货保证金/缓冲；70%现金固定净年化3%，不把15%操作上限混入绩效。
- 主指标为IM净收益加70%现金的CAGR、年化波动率、0无风险Sharpe和MaxDD；辅指标包括净基差加现金、换仓次数、实际新合约DTE、成交量和持仓量。

## 5. 窗口与判断

- 报告Full、10Y、5Y、3Y、1Y；10Y和5Y因真实IM历史不足必须显示N/A并说明原因。
- 首先在同一期限内比较T-1/T-2/T-3，再在同一提前量下比较换入期限。
- 若T-2相对相邻两点仅有不足0.10个百分点的Full CAGR差异，视为经济上近似持平，不以微小数值排名晋升。
- 候选若Full或3Y明显落后、Full MaxDD加深超过1个百分点，或改进集中于单一年份，则不进入观察名单。
- 即使T-2占优，本版最高权限也只是`watchlist`；修改主线必须另获用户批准并验证完整IM V2 Put/Grid/Call联动。

## 6. 产物

运行目录：`quant_param_scan_runs/20260903_im_futures_roll_t02_followup_v2/`。

实现脚本：`run_im_futures_roll_t02_followup_v2.py`。标准产物包括`record.md`、`scan_summary.csv`、`window_metrics.csv`、`scan_meta.json`、`command_log.txt`，并保留`daily_candidates.csv.gz`、`roll_events.csv`、`tenor_dte_summary.csv`、`candidate_rankings.csv`、`annual_metrics.csv`和`parity_checks.json`。
