# 策略组独立对抗审计

基线：`58986fda2c17df93eb3925bf09a1cebd55a7869e`。只读生产代码；全部注入与账本写入位于本组隔离目录。IC 无 Call、IM 9/26 起不新开 Call/救援；新挂牌 Put 候选研究未误当正式规则。

## STRAT-01 / P1：缺少既有卖 Put 当日价格仍正常 HOLD 并可写账

触发：已有未到期卖 Put、已确认入场成本、没有既存待展期触发；IC 历史序列格式/正价均合法但止于前一交易日，或者 IM 候选链缺少所持旧腿。bot 6658—6668 把其设为 `v14_short_put_mark=None`；policy 415—423 将此解释成衰减条件未触发，随后 bot 6672 将新候选判为不需要，最后返回正常 `HOLD`。

IC 生产公共 `build_live_trade_signal` 实测输出 `close_confirmed=True`、`calendar_provisional=False`、非 `diagnostic_only`、`mark=None`、`HOLD`。接着实际 `require_persistence_eligible` 通过，临时 `StateStore.append_confirmed_signals` 接受双产品信号并从 seq 0 推进至 1；重开账本 digest 一致。正式 runtime 未打开或写入。详见 `state_reachability.json`、`run03/regression.txt`。

这是缺失输入被当成条件不成立，会漏记潜在衰减触发并把缺行情包装成已确认结果；不代表真实生产已经发生错账。与既有“候选未消费不应阻塞”的规则不同：这里缺的是已持仓旧腿的衰减判断输入。旧测试 `test_required_held_mark_transport_reaches_outer_retry_even_if_candidate_unused` 已要求持仓 mark 传输故障重试，当前遗漏的是合法但落后/缺行的返回值。

IC `fetch_option_closes` 1501—1523 只验证全序列格式及正价，并不要求某个目标日，因此主源止于昨日也会立即成功，不触发备用源。真实上游可能晚更新，这是可达的数据状态，不需要恶意改写账本。IM 仅完成 adapter 层缺旧行验证，未扩大声称全生产链通过。

修补建议：对需要新判断衰减的已持有未到期卖 Put，要求同日有限正 mark；缺少时明确诊断/重试且禁止完整续账。IC 接口增加 required_day，落后时继续备用源；若使用已经验证的同日链价格替代历史收盘，须先明确一致的价格口径。纯 policy 与 StateStore 增加独立门禁。既有 roll_pending 与到期/恢复分支分别保留原语义，不做无差别所有来源缺失即阻塞的改动。

## 测试与证据

- `run01`：5 项，其中 3 项严格期望拒绝的反例失败、2 项正常 HOLD 对照通过。失败为缺陷证据。
- `run03`：1 项完整调用链与实际临时账本接受反例通过；这是证明错误可达的 witness，不能当修复通过。
- `controls01`：2 项有同日低 mark 时生成 WAIT_SHORT_PUT_ROLL 且保存触发的对照。
- `run02`：首次完整落账尝试因合成锚点缺活动周期 event id 被拒；补齐前次合法 event id 后重跑 run03，原失败回执保留。没有更改任何生产校验或旧测试断言。

预期修复后：3 项严格拒绝断言改为通过，正常报价对照不变；追加通过应改为拒绝且 seq/digest 不变。

## 覆盖与限制

静态核对了普通 Put 开盘投影、核心三倍兑现和新卖 Put 优先级、月维护取消开盘计划、卖 Put 连续展期、严格季度切换与 no-Call 边界。除 STRAT-01 外本组没有确认新的策略逻辑缺陷。未重算历史 NAV，没有修改参数、冻结输出或实际账户状态；不能据此宣称所有策略路径无缺陷。
