# IM / MO Put 95% 行权价基准复测 v1

预注册日期：2026-08-23（Asia/Shanghai）  
状态：独立研究扫描；未批准实盘；不得修改冻结 IC/IM V2、IM v1.1、IM v1.2、Poe 或交易配置。

## 1. 研究问题

当前 IM Put 路径在执行日选择约3个月 MO Put 时，以冻结逐月滚动路径当天实际持有的活动 IM 合约收盘价为95%行权价基准。该活动合约是最近月 IM，持有至最后交易日后进入下一自然月；它不是目标 MO 到期月对应的远月 IM。

当活动 IM 相对中证1000现货指数存在贴水时，`行权价 / 活动IM = 95%` 会使 `行权价 / 中证1000现货` 低于95%，可能系统性选择更深虚值、保护更弱的 Put。本版只检验行权价参考资产，不改变 Put 信号、数量、期限、成交时点、成本或基础 IM 收益。

## 2. 基线与候选

主比较固定虚值度95%：

1. `active_im_m95`：现行基线，`strike / 当日活动IM收盘价`最接近0.95；
2. `csi1000_spot_m95`：用户建议，`strike / 当日中证1000价格指数收盘价`最接近0.95；
3. `matched_expiry_im_m95`：期限错配诊断，`strike / 与目标MO同月份的IM合约收盘价`最接近0.95。

为判断结论是否只是单一虚值度偶然点，对三个参考资产同时运行 `90% / 95% / 100%`，另保留 `no_put`。主决策仍只比较 `active_im_m95` 与 `csi1000_spot_m95`；同到期IM仅作诊断，不直接晋升。

同到期IM合约必须是与目标MO月份相同的 `IMYYMM`。若执行日没有该合约的正收盘价、正成交量和正持仓量，整笔Put调整延迟，不得回退到活动IM、现货指数或未来数据。

## 3. Put信号、数量与期限

- Put目标逐日使用 `im_mainline_v1_1.load_authoritative_local_state()`，不是冻结V2四张负动量下限；
- 绝对估值轴 `2.45 / 2.50 / 2.60` 与57个月相对轴 `75% / 85% / 90% / 92.5%` 取较高档；
- `MOM120 < 0` 时最低3张；只有估值第4档可以产生第4张；每完整1倍核心IM为0至4张MO；
- T日收盘生成目标，T+1共同交易日收盘执行；
- 目标日期为评估日加3个日历月，在当日实际挂牌MO到期日中选择最近者；
- 月度重置，不持有到期，不做每日Delta再平衡；
- 目标月份内只允许 `close>0`、`volume>0`、`open_interest>0` 的Put；最小化 `abs(strike/reference-目标比例)`，并列先选较低行权价、再选较小合约代码。

## 4. 两个收益观察层

### 4.1 `core_1x`

完整1倍逐月滚IM加完整v1.1 Put目标。该层用于检查Put工具本身、资金占用和保护差异。

### 4.2 `im12_core_put`

复用IM 50:50基础路径：0.5倍持续滚IM + 0至0.5倍动量IM；只把上述Put损益、费用和市值按0.5缩放到持续核心袖。Call和估值网格关闭，以隔离行权价基准。该层是本版主决策层，但仍不是完整IM v1.2组合绩效。

## 5. 数据与样本

- IM：中金所官方日线，本地 `data/im_monthly_roll_3m_lowest_put_v1/cffex_im_contracts.csv`；
- MO Put：中金所官方日线，本地 `data/im_monthly_roll_3m_lowest_put_v1/cffex_mo_puts.csv`；
- 中证1000价格指数：中证指数本地冻结 `data/ic_im_valuation_risk_premium_forecast_v3/csindex_000852.csv`；价格指数不复权；
- 活动IM及基础收益：冻结 `outputs/im_monthly_roll_3m_lowest_put_v1/daily_nav.csv`；
- 50:50基础路径：`outputs/im_roll50_momentum50_fullcycle_put_v4/daily_nav.csv.gz` 的无Put底座，并与真实期 `outputs/im_roll50_momentum50_v1/daily_nav.csv` 对账；
- 正式可比样本仅为IM/MO共同真实期：2022-07-22至2026-08-14；不构造2022年前活动IM，不用上市后平均贴水回填；
- 10年和5年窗口因真实历史不足而截短为完整真实样本，并在CSV中标记不可独立使用。

所有日期采用中金所共同交易日和Asia/Shanghai语义；不得前向填充指数、期货、期权或合约链。

## 6. 成本、现金与执行

- 完整1倍IM按30%保证金/风险缓冲；其余现金净年化3%；
- IM基础路径保留原有单边1bp、换月双边2bp；
- MO每张每边按0.5bp完整IM名义成本，2张每边合计1bp；
- Put权利金市值从可计息现金中扣除；
- T+1官方收盘价只作为研究成交代理，不表示可在收盘集合竞价成交；
- 不计买卖价差、冲击、容量、动态保证金、涨跌停、结算异常和整数合约映射误差。

## 7. 预注册判定

主决策只看真实 `im12_core_put` 层：

1. `csi1000_spot_m95` 必须通过选约、T+1、成本、现金和无未来数据检查；
2. 若其完整真实期最大回撤更浅，且完整真实期年化不比 `active_im_m95` 低超过0.50个百分点，同时近3年和近1年没有任一窗口最大回撤恶化超过1.00个百分点，则记为 `watchlist`；
3. 若完整真实期年化和最大回撤均不差，并且90%与100%邻点至少有一个呈同方向，则可记为 `promote_candidate`，但仍需用户另行批准新版本；
4. 若改善只出现在单一窗口、单一95%点或依赖无法执行的同到期IM报价，则保持现行基线；
5. 因真实样本不足5年，任何正面结论最高只代表研究候选，不改写冻结主线。

同时报告：活动IM/现货和同到期IM/现货的分布、选中合约差异率、实际入场虚值度、权利金市值、Put成本、最差日缓冲以及负现金日。

## 8. 强制验证

1. `active_im_m95` 必须逐笔等于未修改的 `im_mo_close_execution_v8.select_close_contract`选择结果；
2. 无Put完整1倍路径必须与冻结v8真实无Put日收益逐日一致；
3. 无Put50:50路径必须与 `im_roll50_momentum50_v1` 重叠日逐日一致；
4. 三种参考资产的选中合约必须逐笔为当日目标月份、流动性过滤后的最近行权价；
5. 信号评估日严格早于执行日；延迟调整不得使用未来报价；
6. 所有收益、Put损益、费用、市值、现金和NAV恒等式误差不超过 `1e-12`；
7. 冻结V2、IM v1.1、IM v1.2、主线登记表、首次正式输出和Poe文件保持不变。

## 9. 产物

- 脚本：`im_mo_put_strike_anchor_scan_v1.py`；
- 测试：`test_im_mo_put_strike_anchor_scan_v1.py`；
- 输出：`outputs/im_mo_put_strike_anchor_scan_v1/`；
- 标准扫描工件：`quant_param_scan_runs/20260823_ic_im_im_v1_2_strike_anchor_diagnostic_v1_im_core_mo_put_strike_reference_asset_x_moneyness/`；
- 本规格及SHA-256：`docs/im_mo_put_strike_anchor_scan_v1_spec.md`、`docs/im_mo_put_strike_anchor_scan_v1_spec.md.sha256`。
