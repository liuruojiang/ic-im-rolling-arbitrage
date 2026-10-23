# IC v1.4-r1：季度期货换仓同步 Put、其余月份 T0 v1

日期：2026-09-17  
状态：预注册研究；不修改正式规则、日报、账本或交易接口

## 研究问题

比较两条完整 IC 路径：

- `current_T0`：核心与动量买 Put 的全部月度维护均在原 T0 收盘执行。
- `sync_quarter_else_T0`：若当月属于 IC 季度期货换仓月，则核心与动量 Put 在完整组合实际记录的 IC T-3 `roll_event` 收盘同步维护；其他月份仍在原 T0 收盘维护。

季度节点必须来自同一路径的实际 `roll_event`，不得按自然月份猜测，也不得把预告当成交。每个季度 `roll_event` 必须唯一映射到其后最近的原 Put T0，日历间隔不得超过10天。

## 固定部分

- 完整当前 IC v1.4-r1 联合研究路径：季度期货 T-3、固定核心0.5倍、当前动量权重、网格0.5/1.0的0.5倍、无 Call、固定核心 short95 路由、核心 Put 3x盈利兑现。
- 核心与动量 Put 均保持当前约3个月、95%目标行权价及既有数量、估值、MOM120、防抖和优先级。
- 真实层使用冻结的 IC 与 510500 ETF 期权挂牌日线；理论层保持既有理论期权延展路径。
- 沿用 IC 单边1bp、Put单边5bp、每1倍期货30%保证金/缓冲及现金年化3%；按执行日 close 成交，不计 bid/ask、冲击或容量。

## 评价与闸门

- 同报 Full、10Y、5Y、3Y、1Y；真实历史不足的10Y/5Y记为N/A。
- 硬校验：`current_T0` 必须与保存的当前完整联合基准逐日一致；混合规则的季度 Put 维护请求必须与 IC `roll_event` 同日，非季度月必须仍为T0；检查实际成交日，不得静默延期。
- 若混合规则真实 Full/3Y 年化均不低于 T0 超过0.25个百分点、MaxDD不恶化超过1个百分点，且模型层无显著相反风险，则列入观察；否则维持 T0。
- 研究结果不自动晋级生产或生成下单建议。

## 产物

- 入口：`research_ic_v14_put_sync_quarter_roll_else_t0_v1.py`
- 运行目录：`quant_param_scan_runs/20260917_ic_im_ic_v1_4_r1_current_joint_ic_core_and_momentum_long_put_monthly_maintenance_sync_put_with_ic_quarterly_roll_else_t0/`
