# 第二轮独立对抗审计：生命周期与持久边界

审计者仅新增根测试 `test_icim_round2_audit_lifecycle_20261021.py` 与本目录证据，没有修改生产实现、他人测试、正式账本、旧规范或旧输出。所有 StateStore 均显式使用 pytest 临时目录；runner 另设独立 TEMP 与 ICIM_STATE_DIR，未调用默认 StateStore。

## 已核查的实际路径

- 完整 `build_live_trade_signal`：T-1 状态桥接、实际日期/季度与期权日历、SSE 报价适配、共享/独立核心持仓身份、policy、普通 Put 调度均运行。只替换外部行情与研究观测输入。真实保存 SSE 链的标准 M07500/10012099 正常成功；旧证券 10011470 和缺 ID 拒绝；核心与动量分袖身份不会互借；零持仓不要求未持有的 legacy ID。
- IC/IM 的 ordinary/profit3x/short opening 经过实际开盘证据适配与投影后，注入后续失败。公开入口恢复原 anchor 对象和值，临时账本字节不变。短 Put 初次审计输入缺少 IC future 开盘行、IM 旧 verified_core 未归零两项已补齐，初始失败保留，未放宽门禁或断言。
- 2026-10-21 当前严格边界下，IC/IM 普通 Put 实际开盘证据、真实 hash StateStore append、restart 和重复 append 拒绝。测试仅统一合成墙钟到 15:40，保留采集、生成、写入时间与所有 effective date 门禁。
- 当前 short mark 身份防线独立复验：旧 M 标签/当前 A 证券不得创建后续 short 计划。该生产修复由主智能体和报价审计线确认；本审计的该项首次运行已在修复后，不能冒称修复前失败。

## 确认的 adapter 边界与可达性限制

`expiry_initial/` 保存三个未改弱断言的首次失败及运行前后相同源码哈希。旧 M07500/10011470 的短 Put 在到期日已触发身份拒绝，但 `_apply_v14_live_policy` 的异常回退仍重跑 policy 并保留 completed_lifecycle_actions，因而分别发布到期分支、接受注入的 assigned 模型结算或接受 expired_worthless 模型结算。另增加候选源先失败与合法标准 M 到期的正对照，用于防止仅在错误消息出现后才设标志的修复漏掉前置异常。

这是实际 adapter/prepared-signal 边界反例，不能称已证当前完整信号路径可达交易漏洞：将真实 2612 身份推进到 2026-12-23，完整 builder 会先因 IC2703 换仓需要 2027 官方日历而拒绝。审计没有放宽该门禁，也没有把真实 2612 调整证券伪改成 2610 以强造当前可达。`expiry_builder_initial/` 的首失败是临时 journal 文件名未随合成日期变化；`expiry_builder_valid_initial/` 证实实际 2027 日历拒绝，均保留。最终测试对这条完整入口断言日历拒绝与 anchor/ledger 不变。

## 明确排除的两个历史能力探针

首次 `initial/` 的历史 adapter 保留 ID 与合法标准 M overlay 成功期待两项失败，原断言保留。按 ID 定向取得的历史 series 只证明证券，不证明目标日仍是该 M 交易代码、标准 strike 和 unit；因此不能用复制请求 ID 的方式使其通过。当前历史 adapter 没有日主数据绑定，2026-10-21 起严格拒绝是保守的能力限制，未确认为业务漏洞。

累计验收必须明确排除：

```
-k "not historical_adapter_retains and not legal_standard_core_replay"
```

安全闭环需要目标日权威或已验证的证券 ID、交易代码、实际行权价和单位绑定；旧 M07500/10011470 在当前官方数据中已为 A07500、7.367、10180，不能伪标 M/7.5/10000。独立负对照已验证当前 historical overlay 对该情形拒绝。旧日兼容和 A 成本迁移未支持并非本轮新漏洞。

## 证据解释

`initial/` 为 14 项首轮（10 通过、2 能力限制失败、2 审计 fixture 失败）；`expanded/` 为合法 fixture 补齐后的 15 项（13 通过、同 2 能力限制失败）；`builder/` 为完整入口三个身份对照通过；`current_boundary/` 的 19 通过包括当前真实账本 roundtrip，3 个分袖失败是审计筛选误取 Call 行，已限定 Put 行后复验。旧证据和源断言均不覆盖。

最终 `final_frozen/` 为 **36 通过、2 个上述历史能力探针明确 deselect、0 失败/错误/skip**，2.43 秒；1 条既有第三方 Pydantic 警告。运行前后 8 个源码/测试/runner/预注册 SHA 完全相同。冻结根测试 SHA-256：`3c92189672444fd9745f9d30a327aa93aeecd6ba9f1b53fc49ddbe3d91e0b8ff`。

主智能体修复后，6 个旧 M expiry adapter 反例（包括候选源前置失败）均返回 HOLD_FAIL_CLOSED，保留 short route 且不新增 settlement_pending。合法标准 M 的正常/候选源故障两项仍独立发布到期分支。完整公开入口会拒绝带 unverified marker 的 adapter 结果；执行守卫、policy、新信号校验与真实 StateStore 四边界均以精确身份错误拒绝，journal/latest 及全部其他持久载荷字节不变，restart digest 不变。

`final/` 的 31 通过/1 失败来自公开入口保护升级为直接 raise，而原测试只接受 HOLD 输出；已将完整入口断言收紧为必须抛出精确 `unresolved held Put identity`，底层 6 个 adapter 原断言未改弱。`final_recheck/` 的 33 通过/3 失败来自将字节快照扩到协调锁：Windows `.ledger.lock` 在尝试 append 时创建/追加 NUL，即使拒绝也会改变。没有 journal/latest/仓位变化。最终快照明确排除这个协调锁，覆盖全部其他文件，不将锁变化冒称业务账本污染；没有修改生产锁实现。
