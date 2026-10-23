# 专家四点反馈：实现位置、测试与运行边界

## 1. 冲突在哪里生效，查询是否受影响

原实现的 raw_response 只是追加原始响应；adapter._record_http_conflicts 在独立采集结束才标记。因此不能把旧实现说成“raw_response 当即识别冲突”。本轮已在 ic_im_signal_evidence_v1.CaptureSession._raw_response_locked 中追加后调用 record_http_conflicts，同 URL+逻辑参数的 body/status/encoding/严格响应头不同会产生永久 raw_http_conflict，**该冲突本身不抛异常**。_evidence_http_get 仍把同一个实际 Response 返回给 provider 正常验证。存储失败、超限、非法字节等录制异常仍会抛出，未一律吞掉。

正常查询本来就没有 active capture；LedgerCoordinator 的独立 capture_current_inputs 与查询分开。测试：test_raw_conflict_marks_immediately_without_raising（4种差异）、test_http_wrapper_still_returns_both_conflicting_responses、test_normal_http_query_never_opens_capture。原完整正常查询测试 test_ambiguous_capture_is_incomplete_and_does_not_break_normal_query 继续保留。

## 2. 信号能否落账

明确采用“可以展示参考计算值，但这次采集结果不落账”。公共 build_live_trade_signal 将 capture 中的结果绑定到会话。封存前 input_evidence_status=recording、persistence_eligible=False；只有完整包成功发布后，才恢复原本已合格的信号。原本因日历、网格、身份等不合格的信号不会被提升。冲突时 status=inconsistent，或其他未解决失败时 incomplete，保持不合格并附参考/不落账提示。

StateStore.require_persistence_eligible 再次拒绝 active capture 和非 complete 标记。封存前 deepcopy 的快照仍是 recording/False，即使原对象随后成功封存，该旧副本也不能写账。发布失败不提升，迟到冲突也会使已绑定对象失效。成功新包的回放保留完整封存标记，旧已验证历史账本按既有兼容规则读取。

测试：test_public_builder_binds_signal_until_capture_seal、test_conflicted_bound_signal_and_early_copy_cannot_persist、test_only_successfully_sealed_originally_eligible_signal_is_promoted、test_store_boundary_rejects_unbound_signal_inside_active_capture、test_publishing_failure_does_not_promote_signal、test_context_failure_keeps_bound_signal_ineligible。

## 3. 重试与旧包识别

同一会话已经记录的冲突不可解除，后来重读到相同正文也不能修复它。独立采集下一轮 capture_daily 对每个品种重新构造 CaptureSession；后台每五分钟重试。旧失败包隔离保留，不向其中继续追加“更正”。未取得响应的连接失败仍可按原有限请求重试；一旦已有响应冲突，该整包不可放行。

仅有失败包时，has_original 现在明确抛 EvidenceUnavailable；既不返回“可用True”，也不返回 False 允许退回当前数据。公共 builder 将其转为非持久化诊断。以后产生新完整包，正常 loader 选择完整有效的新会话，旧失败包仍留存。正式 loader 也会拒绝旧版错误标称 complete 的原始响应歧义包。

测试：test_repeated_consistent_observation_does_not_repair_conflicted_session、test_legacy_ambiguous_pack_claiming_complete_still_cannot_load；四个 old reader 歧义测试保留原断言，先断言正式 loader 拒绝，再只为该单元测试构造故意坏的内存 reader。另有旧 presence 测试从失败包 True 更新为明确拒绝；原失败和备份保留，没有新增单列/跳过。

## 4. 重试仍失败的最终状态、页面与统计

单轮联网预算/有限请求重试结束仍不完整，返回 incomplete，不推进 last_verified_day；后台下一轮从新会话尝试，没有次数到达后自动把坏包变为合格的规则。当日仍没有任一完整有效包时，页面显示“当日原日存证缺失；本轮重试后仍未取得完整包，需另行补证或人工锚定，不能承诺自动恢复”。该独立失败不污染另一次无 capture 的完整查询。

新增 adapter.capture_day_status，只读扫描并校验封存包，按品种统计 attempts、complete/incomplete_attempts、conflict_attempts、conflict_requests、conflict_frequency。采集器每个运行回执自动保存 consistency_statistics；already_captured 轮询不增加尝试分母。delivery/statistics_report.py 可汇总实际观测交易日及具有同址重复观测的会话数量。首五交易日的统计只有实际运行后才能获得，当前不能声称已有五日数据。

测试：test_sealed_attempt_statistics_count_conflicts_not_polling_receipts、test_capture_failure_warning_identifies_missing_original_inputs、test_server_page_displays_incomplete_original_archive。

## 真实重复探针的重要限制

delivery/time_probe_02/receipt.json 保存两次真实收盘后 SSE 12月 tstyle 请求：20:45:52 与20:46:03收到，正文 SHA 完全相同，行情 time 都为 162906，但 HTTP Date 头不同。现有严格响应等同性也比较这些响应头，故该诊断探针被正确记录为“不完整”，没有生成正式信号。此处是响应元数据差异，不能说期权价格发生矛盾。

这说明不能承诺重试必然收敛，也不能假设收盘后响应头恒定。本轮没有为了通过而忽略 Date，亦没有新增通用 URL 响应缓存。已有具名输入/月链复用属于上一轮免费源接入，继续保留；原日采集不一定每个URL都出现重复，因此零冲突不能证明SSE刷新规律。初次探针误引用不存在的 HEADERS 常量，未实际请求，已另存失败；第二次真实两响应才作为实证。

日历注释仅改成 official weekday closures for covered years；2022–2026 日期集合及日历计算 AST 不变。2027本地日历仍未配置。

本轮需使用新 delivery/capture_source_pin.json 和隐藏启动脚本。旧进程已在修改源码前停止；最终测试/校验通过后才重启。正式账本和原有原始证据不改写，远端 Poe 配置不据本次本机启动推断。

补充边界：同一参数字典的键顺序已统一为逻辑请求键；原始参数及响应字节仍原样存档。test_parameter_key_order_cannot_hide_same_request_conflict 修复前失败，修复后纳入最终累计。此处仅改变一致性比较，不复用网络响应。
