# IM v1.4-r1：季度期货换仓同步 Put、其余月份 T0 v1

日期：2026-09-17  
状态：预注册研究；不修改正式规则、日报、账本或交易接口

## 研究问题

比较两条完整 IM 路径：

- `current_T0`：核心与动量买 Put 的全部月度维护均在 T0 收盘执行。
- `sync_quarter_else_T0`：若某月 T0 的前一真实交易日正是完整组合记录的 IM 季度期货 `roll_event`，则 Put 在该 T-1 收盘与 IM 同步换约；其余月份仍在 T0 收盘维护。

不得按自然月份猜测季度节点，也不得把预告当成交；同步日必须来自同一候选的实际 IM 换仓事件。

## 固定部分

- 完整 IM v1.4-r1 联合研究路径：季度期货 T-1、固定核心0.5倍、当前动量权重、1.6/2.0的0.5倍网格、D10/IV26 Call、固定核心 short95 路由、核心 Put 3x盈利兑现。
- 核心与动量 Put 均保持约3个月、102%目标行权价、原数量/估值/MOM120/防抖和优先级。
- 真实层使用冻结中金所 IM/MO 官方日线；理论层保持既有中证1000及理论期权路径。
- 沿用现有期货成本、30%/倍缓冲、现金年化3%和 Put 单边费用5倍压力；按执行日 close 成交，不计 bid/ask、冲击或容量。

## 评价

- 同报 Full、10Y、5Y、3Y、1Y；真实历史不足的10Y/5Y为N/A。
- 硬校验：当前 T0 必须与保存的完整联合基准逐日一致；混合规则的同步 Put 事件必须与 IM `roll_event` 同日，非季度月必须仍为 T0；不得静默延期。
- 若混合规则真实 Full/3Y 年化均不低于 T0 超过0.25个百分点、MaxDD不恶化超过1个百分点，且模型层无显著相反风险，则列入观察；否则维持 T0。
- 研究结果不自动晋级生产。

## 产物

- 入口：`research_im_v14_put_sync_quarter_roll_else_t0_v1.py`
- 运行目录：`quant_param_scan_runs/20260917_ic_im_im_v1_4_r1_current_joint_im_core_and_momentum_long_put_monthly_maintenance_sync_put_with_im_quarter_roll_else_t0/`

