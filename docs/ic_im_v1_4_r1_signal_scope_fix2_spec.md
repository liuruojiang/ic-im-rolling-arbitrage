# IC / IM v1.4-r1 信号边界修正 fix2 规格

日期：2026-09-17  
构建：`v1.4-20260917-r1-coreput3x-fixedshort95-fix2`  
规则版本：`ic_im_v1_4_coreput3x_fixed_short95_20260917_v1`（不变）

## 1. 范围

本系统只发布研究模型信号，不接管或核验用户账户的实际成交、行权、结算、交割和持仓。正式信号和持久账本记录的是策略参考路径，不是账户事实。

## 2. 到期信号

1. 卖Put尚未到期时，生命周期状态为`model_short_put_active`，不得因未接入账户回执显示为阻塞。
2. 到期后尚缺经核验的模型结算依据时，发布`PUBLISH_SHORT_PUT_EXPIRY_BRANCHES`：
   - 价外失效分支：结束卖Put周期并返回期货路线；
   - 行权或价内现金结算分支：IC进入模型ETF承接路线，IM进入模型现金结算恢复路线。
3. 条件分支是策略参考信号。程序不得声称用户账户已经行权、获配ETF、完成现金结算或建立恢复仓位。
4. 取得模型结算依据后，可使用`v14_model_settlement_confirmed`、`v14_model_settlement_outcome`和`v14_model_recovery_future_confirmed`推进研究状态；fix1字段仅作输入兼容。

## 3. 发布连续性

缺少到期模型结算依据只使依赖该依据的固定核心路线保持条件状态。动量、网格及其他能够核验的信号继续发布。报告必须显示`research_model_signal_only`和`not_observed_out_of_scope`。

## 4. 不变项

策略参数、2026-09-18生效边界、schema 4、历史回测数学、30%绩效资金口径、IC无Call以及IM `rescue_next_listed`均不变。fix1规格和其SHA保持冻结，不回写。
