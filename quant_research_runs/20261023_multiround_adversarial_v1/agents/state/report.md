# 独立状态与服务端对抗审计

审计日期 2026-10-23；审计者 ma23_state。范围：H 完整性门禁、StateStore 真实写入/崩溃恢复、Coordinator 联合写入与提示、共享快照锁。只新增测试和本目录材料，未改生产、旧测试、冻结工件或正式 runtime；测试均离线，使用临时有效账本及明确合成的输入。

## 修前结论

`state_03`：21 项，16 通过、5 失败、0 错误/跳过；28 个生产模块和本组测试共 29 个文件前后 SHA 完全一致。回执在本目录 `state_03/receipt.json`，pytest 工件在本轮根目录 `state_03/`。阶段 state_01/state_02 为开发中较小测试集的失败记录，也保留，不混加计数。

确认 2 类问题。没有复现已披露的 H 门禁失效、半品种写账或错误推进 last_verified_day；以下发现是提示/恢复就绪误报，不称正式目标污染。

## S1：原日建包之前失败时，页面漏掉缺失提示（P1）

触发路径一：独立 CaptureRunner.cycle 在 archive 初始化、读写或建包之前抛错，cycle 捕获成 status=incomplete，再由 write_receipt 写下周期回执。这时没有 sessions manifest。

触发路径二：真实 `LedgerCoordinator.capture_current_inputs` 捕获 capture_daily 的异常，返回并保存 `last_evidence_capture={day,status:incomplete,reason:...}`；也可能完全没有 manifest 或落盘回执。

`capture_day_status` 只枚举 sessions，空集合给 status=not_recorded、recovery_warning=None；`execute_query` 既不读取失败周期回执，也不读取 `last_evidence_capture`，因此两个路径最终页面只留下已计算信号，没有“当日存证缺失，需补证或人工锚定”的提示。与专家要求不符，也容易让用户将正常实时查询等同于日后可恢复。

失败测试：

- `test_failed_capture_receipt_without_manifest_must_show_original_archive_missing`
- `test_server_capture_exception_without_manifest_must_show_original_archive_missing`

正对照 `test_waiting_for_close_receipt_is_not_missing_evidence_warning` 通过：当日 10:00 只有 waiting_for_close 回执，不能提前叫缺证据。修复须保留此区分；历史/收盘后的明确失败与从未到采集时点不是同一情况。

## S2：结构完整不等于 load_original 可恢复，统计却称 captured（P1/P2）

`capture_day_status` 仅筛 original_close/product，再经 evidence._load_one 检查结构、blob 与哈希，之后就计 complete_attempts、可能返回 status=captured，并取消 recovery_warning。它没有检查 load_original 实际要求的 INPUT_REVISION、必需角色或原日收盘时点。

通过合法 CaptureSession API 生成的三种包：旧 revision、缺 market.live 角色、采集跨午夜结束，在 `load_original` 均明确拒绝后，`capture_day_status` 仍对两品种返回 captured。

失败测试：`test_daily_archive_status_does_not_claim_recovery_ready_for_unloadable_packets[old_revision|missing_role|crossed_midnight]`。

这些测试使用合成角色载荷，加载器在消费任何行情载荷前就拒绝，绝不声称它们是有效市场证据或可落账信号。当前 capture_daily 自己补必需角色并限制时点，因此普通新采集不容易产生前两种；旧版/其他合法采集 API 调用及已运行请求跨午夜尾延迟使这种“统计已完成，实际不可恢复”边界值得明确防守。若将 captured 定义仅为结构封存，则须另有明确的 recoverable 指标和页面门槛，不能把该统计当可自动恢复的证据。

建议与 load_original 的实际路由资格复用共同检查。不要只检测 manifest.complete，也不要把本问题写成 StateStore 接受错误信号。

## 通过的独立反例

- 已发布 journal 但更新 head 前模拟 OSError：IC/IM 任一 recording/inconsistent/不合格/diagnostic 重试仍被拒绝，不借恢复旧 journal 推进；8 项。
- 正常重试恢复第一次发布的同字节 journal，陈旧 current 重复提交被拒绝；1 项。
- 已绑定但未封存的 deepcopy 被交给不继承 ContextVar 的另一线程，即使离开会话后再次提交，仍因 recording 标记拒绝；2 项。
- 两品种都产生计算结果，任一品种 input_evidence_status=incomplete、persistence_eligible 被设回 True，真实 Coordinator 仍不部分写账，计算文本保留；2 项。
- waiting_for_close 不错误告警；1 项。
- 模拟 renderer 运行异常后恢复全局 Poe 对象与 observer，账本不变；1 项。
- A 线程持策略锁并临时替换核心合约时，B 线程读最后核验快照会等待，恢复锚点后只渲染原合约；1 项。

总计 16 通过；与 5 个失败共 21。不能将本组有限覆盖说成状态系统无其他缺陷。
