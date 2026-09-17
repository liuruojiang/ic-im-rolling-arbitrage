# IC / IM v1.4-r1 fix2 信号边界修正验证

## 结果

账户实际成交、行权、结算、交割和持仓已明确排除在信号系统职责之外。卖Put持有期不再因缺少账户证据标记阻塞；到期缺少模型结算依据时发布条件分支，不停止其他可核验信号。

构建更新为`v1.4-20260917-r1-coreput3x-fixedshort95-fix2`，策略规则版本、schema、参数和2026-09-18生效日不变。

## 验证

- 策略输出明确标记`research_model_signal_only`和`not_observed_out_of_scope`。
- 到期条件动作是`PUBLISH_SHORT_PUT_EXPIRY_BRANCHES`。
- 新模型字段可推进研究生命周期；旧字段仅保留输入兼容。
- fix1冻结规格及SHA未改写；正式运行账本未写入。

- fix2规格SHA-256：`5cb06b020e3a7f6386cbef940aabd382a3881a63869c7f51382370e97d8bcc41`

## 最终检查

- 回归：265 passed，1 warning。
- 生产模块语法检查通过。
- 历史同引擎对照：NO_CHANGE_IN_SAME_ENGINE_HISTORICAL_OUTPUTS；58个文件、119527行，最大数值差异0.0。
- 真实样本截止日仍为2026-08-14；本次信号职责修正不进入冻结历史回测数学。


