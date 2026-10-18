# 月度Put维护日报提醒，2026-10-16

用户确认第3项不改维护时点，第4项结算与恢复不改。仅增加前一实际交易日收盘、维护日盘中的显眼提醒：具体合约和数量由用户在维护日盘尾按当时行情与策略配置自行判断，并于当天收盘完成换仓，不推到下个交易日开盘。

本轮不修改选约、数量、收益计算或账本。提醒读取已有逐腿维护日和下一实际交易日；日期/确认状态不符时不展示，不猜节假日。持仓表HOLD不能掩盖维护日，零Put配置不因提醒新增仓位，盘中候选不当作已锁定合约。历史回放标注历史日历复核。

本地Poe提醒在每个品种仓位表前，收盘附件提醒在报告开头，产物JSON单列notice及revision但不写入信号/账本。云端打包器独立从逐腿日期生成标题、正文及HTML提醒，不信任自由文本notice。部分有效邮件保留警告，只在独立日历和逐腿日期吻合时展示日历提醒，不展示未确认目标；无完整信号的失败产物不借旧日期造提醒。

## 变更及验证

- 修改前备份位于 `backups/`，含本轮开始时的本地bot、digest、registry及云端打包器；此前三倍时序/卖Put成本修复仍保持其独立记录和部署状态。
- 本地原33文件、此前开盘修复文件及新提醒文件共831项通过，零失败/错误/跳过，18.62秒；只有原Pydantic弃用提示。详见 `regression.xml`、`regression.txt`、`test_run.json`。测试仅隔离状态，未接生产账本；Windows目录权限兼容仅作用于测试进程。
- 云端原daily-digest专项与新增7项提醒检查共33项、10个subtests通过，1.50秒；详见 `cloud/regression.xml`。首轮3项失败为新测试夹具缺少原门禁要求的数值字段，补齐夹具后通过，未放松生产门禁；原结果保留在 `cloud_initial_failure.txt`。
- `independent_audit.json`：此前426份受保护账本/冻结规范/首次输出哈希全部未变；与本轮备份比较，本地只变更 `_handle_signal`、`render_stored_close_report`、`build_artifacts` 三个展示/交付函数及新增导入。选约、执行、风控、收益函数未变。
- 保存的真实2026-10-09结果经旧/新打包器读取，在非维护日的subject、纯文本和HTML字节完全一致；本地与云端提醒模块字节一致。此前历史四臂回测及代码/输出在本轮没有改写；本次没有重跑收益回测。

## 发布状态

云端4文件补丁为 `1ff34a121fbdab7b89af18c7e75ff3e2283c003b`，PR https://github.com/liuruojiang/codex-daily-automation-probe/pull/152 已合并，merge为 `5520fce411f0a3815287f38747b67e0e5eb789cd`。PR离线CI run `38845218869` 的automation-tests、strategy-tests(icim)、strategy-tests(microcap)三项均成功；这不是正式发送验收。本轮云端发布仅打包器、提醒模块、测试及文档，不切换策略执行pin，不发布此前本地三倍兑现/卖Put成本修复，不改调度/SMTP/intent/marker，不触发正式发送或续账。

合并后4文件从精确merge SHA逐一读回，与本地提交文本一致；日报workflow Git blob仍为 `f43f9abb2454b4abdd48dcc948ef085ea9d8a9de`。详细Git blob、CI链接及无发送/续账标识见 `release.json`。云端后续按原调度使用新模板，未重发昨日邮件或冒称当日已投递。

本地14:15任务既有配置要求先读mainline_registry，本轮将提醒要求登记在其首段；未修改受限目录中的调度配置。本地提醒代码已验证，正式策略仓库的runtime pin未随本轮云端模板发布切换，不能把本地执行修复称为已部署。
