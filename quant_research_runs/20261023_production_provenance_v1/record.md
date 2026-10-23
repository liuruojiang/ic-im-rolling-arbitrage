# A–H 生产改动归属与运行边界续核

2026-10-23，承接专家对 2959 项报告的续审。此前交付只写本轮增量，缺少整份累计生产文件的归属总表；本报告补齐。旧规范和旧回执不改写。本轮不仅补文档，还修复新核出的缓存消费、回退错误类型和批处理收尾边界。

## 逐项归属

| 项 | 归属与此前验收 | 实际作用及本次处理 |
| --- | --- | --- |
| A：ETF 三源回退 | 10-22 `free_source_integration_v1`，原规范第 1 条；2918 项验收，随后包含于已接受的 2959 版 | SSE→腾讯→新浪，保留实际来源/时刻/昨收。原日角色 ic.etf 包装聚合入口；没有包的历史 ETF 路径不改用实时价。本轮仅补耗尽回退后的缺证据类型，不改来源顺序。 |
| B：挂牌新浪备用 | 同一免费源接入轮，原规范第 3 条 | 用真实观察月份并校验标的、结构和挂牌形状，不推造未上市月份。selector 已在该轮接入。本轮仅补缺证据类型。 |
| C：新浪明细 6 个/批、2 线程、纯传输失败重试一次 | 免费源接入主规范第 2 条及 `bounded_parallel_capture` 补充规范 | 为大链取不齐、串行耗尽预算和批次传输失败改善活性；不是修好代理服务。参数仍为 6/2/一次。本轮改成至多两个待完成任务，失败不继续提交后续批，按原批次顺序回收结果，并检查等待收尾的预算。 |
| D：MO 预取 2 线程 | 预取函数较早已存在；两线程版本属免费源轮 `sina_batch_wire` 补充规范 | MO 仍是 64 标识/批，不是 6。当前调用图中实际预取从独立采集 adapter 发起；普通 Poe 信号查询没有直接发起全挂牌 MO 预取。函数虽然位于 bot 模块，不能据此说每次 Poe 查询均多开这两条线程。本轮与 C 共用同一个受 deadline 约束的批处理收集器。 |
| E：单查询月份链缓存 | 免费源接入主规范第 2 条，2918 项验收已含 | 这是生产采集行为改变，必须单列，不能以“不是通用 URL 缓存”略过。只缓存 helper 的完整年月、行情日、历史日、reader/session 身份组合；查询结束即清空、结果 deepcopy。本轮补普通缓存及具名 ic.chain 去重命中后的重新校验。 |
| F：观察时钟随采集推进 | 时钟 helper 在免费源轮前已经存在；公共 builder 外包 collection_clock、链/选中行验证器改用观察时钟，属免费源轮及 `free_source_replay_contract` 补充 | 防止正常采集期间的新报文被错误判成未来报文；仍拒绝真正未来、错误日期及不满足原门槛的选中行。策略计算日和历史回放时钟不随网络漂移。恐慌 batch 的嵌套按同一 ContextVar 复用。 |
| G：字面逗号发送 | 免费源轮 `sina_batch_wire` 补充规范 | 真实失败是 HTTP 200 但 `%2C` 被新浪当成标识内容，返回空变量；wire_probe_01 的同批 64 标识用字面逗号获得精确非空集合。这是请求编码修复，不是代理超时修复。仅两个精确 Sina 端点、安全 ASCII 列表生效，存证键仍是逻辑 URL+参数 dict。本轮不改。 |
| H：绑定会话、带出完整性状态 | 后续 10-22 `evidence_conflict_review_v1`，2941 项验收，随后包含于 2959 版 | bind_signal 同时将封存前资格设 False；完整封存后才恢复原本合格的绑定对象。StateStore 独立拒绝活跃会话和非 complete 标记，回放带出已封存 annotation。本轮存储实现不改，新增真实临时 StateStore 反例与正常推进用例。 |

A–G 的已验收 bot SHA 为 `565d719cf7ae5b7c6b4fe8c922536cf5e7bde7d485c2b293f4cec1ba86b63af4`；它与冲突轮的修前备份相符。H 后 bot SHA 为 `46c617cc2a8c397a478b2e0035670995ad6e4228da77de675653da0529a45e99`，同时匹配冲突轮 2941 和 HTTP Date 轮 2959 的源码绑定。HTTP Date 轮没有新增 A–H 的这些 bot 逻辑。较早的免费源修前备份为 `0075c16aec68cb56b49569d03c1b89bb2707873fbd309971461082b623d0d19f`。逐函数 AST 对照与完整路径见 `delivery/A_H_provenance.json`。

这些归属表示已有对应轮的代码与验收记录，不是补称专家曾逐项签署过每一项。E 的行为变化此前已在规范和免费源报告中记录，但最新累计文件应再次集中披露，本次补上。

## 风险确认及实际修复

### 1. 缓存重新校验与范围

旧 `_fetch_510500_chain_for_day` 确实在缓存命中时直接返回；此外，显式录制时 CaptureSession 的具名 ic.chain 去重会在进入 helper 前直接返回。只修前者不够。本轮同时补两层的消费校验，重新执行原有日期/盘中时间/未来时间验证；实际选中的新浪证券仍执行独立 20 分钟门控，不把全链聚合时间冒称逐行新鲜度。

测试：`test_query_chain_cache_hit_revalidates_and_keeps_selected_row_time_gate`、`test_capture_role_cache_hit_revalidates_consumed_chain`。前者验证只取一次网络、21 分钟后复用仍重新校验，选中行仍被 20 分钟门槛拒绝。

`fetch_sina_510500_security_id` 仍直接调用 `fetch_sina_510500_chain`，不走该缓存，同一会话可能产生第二份新浪报文。这一点没有靠缓存消除。若同 URL/参数实际正文不同，原冲突门禁继续使会话不完整、不落账。测试：`test_security_id_direct_sina_chain_remains_outside_month_cache`。没有增加通用同址 HTTP 缓存。

### 2. 两线程、顺序及预算

CaptureSession 的 raw_response/条目/blob/计数/封存由 RLock 保护，归属免费源并行存证轮；本轮未再改存证模块。raw 清单按实际完成顺序追加，不承诺线程调度顺序固定。reader 以 URL+规范化逻辑参数匹配，严格验证响应身份，不依赖调用 FIFO；C 的汇总结果仍按提交批次顺序。

旧 C/D 按提交顺序等 future，早批阻塞时可能迟迟发现不了第二批失败，队列中的后续请求已经开始。本轮共用 `_collect_quote_batches`：至多两个待完成任务、按完成检测失败、不再提交后批、取消尚未开始者；with 退出仍等待正在运行的 writer 完成，不提前封存。

同一绝对 deadline 经 copy_context 传入 worker，排队、等待、网络和 shutdown 期间持续消耗，不在子任务重置。收尾超过预算时拒绝成功结果。**45 秒不是强制返回的硬墙钟上限**：Python 不能安全强杀线程，requests 的 DNS/连接/读取阶段及已运行请求仍可能带来尾延迟。已等待线程安全退出不等于保证耗时小于 45 秒，不能作这种确认。本轮没有调大生产预算。

测试：`test_threaded_raw_capture_is_atomic_and_replay_is_order_independent`、`test_batch_failure_cancels_unstarted_and_joins_active_writer`、`test_pool_shutdown_time_counts_against_product_budget`，各含 IC/MO 两种；使用真实线程和实际 CaptureSession，行情内容为明示的离线合成报文。前两项同时验证不会提前封存/交错覆盖，最后一项模拟 shutdown 尾延迟并验证不可返回成功。

### 3. 回放回退及错误类型

新增两种 raw 回放测试：原日 SSE 传输失败、没有响应记录，实际使用腾讯 ETF 或新浪月份；回放 SSE 的 EvidenceUnavailable 被聚合层作为单源失败继续探测，最终从原包命中备用。整个过程禁止外部 HTTP。测试：`test_raw_replay_missing_failed_primary_reaches_recorded_fallback`。

这种有意探测会在 reader 的 missing_inputs 中留下失效主源记录，报告明确保留，不能叫“零未命中”。正常完整具名原日包的已验收恢复仍为零外部请求、事实字段一致；这两个测试分别证明不同边界，不混称。

全部候选源耗尽且有原日证据缺失时，旧 `_source_failure_error` 确实会将类型丢成 RuntimeError。本轮只在活跃 reader 的条件下保留 EvidenceUnavailable，外层能输出 `original_evidence_unavailable`。测试：`test_exhausted_raw_replay_preserves_evidence_unavailable`（ETF/挂牌/链三类）、`test_public_original_replay_reports_missing_source_as_original_evidence_unavailable`。正常实时纯传输失败依然进入原有限重试，格式/身份错误不冒充传输故障。

### 4. 完整性门槛

`require_persistence_eligible` 先检查 active session，再检查 input_evidence_status，最后检查原有诊断、calendar、HOLD 和网格门禁。即使手工把 persistence_eligible 设回 True，非 complete 的状态仍不能经 StateStore 推进 verified_day。旧版本未包含新标记的已核验记录保持读取兼容。

旧验收测试名：`test_public_builder_binds_signal_until_capture_seal`、`test_conflicted_bound_signal_and_early_copy_cannot_persist`、`test_store_boundary_rejects_unbound_signal_inside_active_capture`。新增实际临时账本测试：`test_state_store_rejects_capture_integrity_without_advancing_day`（两品种×recording/incomplete/inconsistent 六种）及 `test_sealed_complete_capture_allows_normal_state_store_advance`。逐项核对 last_verified_day/摘要/账本文件不变，不只测试一个辅助布尔判断。

### 5. 非阻塞项

`test_collection_clock_nested_observation_batch_preserves_outer_fear_evidence` 验证 nested batch 仍是同一个外层对象，observed_in_batch 可读取已采集证据，返回副本不污染原值。新浪到期日当天可能先下架当月的问题本轮不改；仍安全拒绝。10-28 到期日的真实收盘返回尚未发生，不声称已观察；后续须单独检查实际报文。

## 验收与保护

- 修前 `before_01`：10 项失败、13 项通过，源码绑定一致，保留真实反例及备份；专项 `focused_01`：23 项通过。
- 最终 `accepted_01`：104 个测试文件、2982 项通过，原 22 项单列清单字节不变，1010 个绑定文件前后相同；原第五轮 19 项、HTTP Date 18 项和旧完整性测试均包含。
- 真实 10-22 IC/IM 包按此前已验收的原 manifest 及已验证 blob 复制至本轮独立输入目录，不读取尚未收盘的 10-23 数据。服务端 5 场景通过：两条恢复路径、缺 IC 包、损坏必需 blob、错误目录。正常路径外部 HTTP 0；重复调用不重复写入；负例不允许单腿推进。
- 与旧版本相同包对照，显式与自动路径的 IC 212、IM 227 信号字段及完整 products 状态严格 JSON 类型一致，仅排除既定三个计算时刻。两个离线预检分别以 exit0/exit2 通过，均不联网。
- 最终机器核验 `delivery/final_verification.json`：正式 runtime 156 文件、修前原有归档 248 文件字节不变；生产仅 bot 和 evidence_adapter 两文件改变，四个旧 bot 函数、一个新增批处理 helper 与 adapter 的 _keyed 变更已逐 AST 限定。策略、配量、日历、回测数学与 H 状态存储实现未改；没有重算 NAV，不能撤销此前历史日历修复对 IC 回测的影响。

## 超时、缺失提示及运行状态

按已完成周期回执关联的已封存品种采集尝试统计，轮询/等待不计为新尝试。目前仅 10-22 一个已完成交易日，不是“头几个交易日”已全部观察。

| 品种 | 封存尝试 | 出现过超时 | 最终完整 | 最终不完整 |
| --- | ---: | ---: | ---: | ---: |
| IC | 36 | 34（94.44%） | 2 | 34 |
| IM | 36 | 36（100%） | 1 | 35 |

IM 的一次完整尝试经过重试，故“出现过超时”不等于“最终失败”。20 时各品种 1 次、21 时各 9 次、22/23 时各 12 次出现超时；18 时 IM 两次出现超时，包含重试成功的一次。分母只含这些周期回执关联的尝试，独立手工试验、无回执的中断及其他完整包不混入；所以不与 capture_day_status 的“全部 sealed session”分母混称。详见 `delivery/timeout_frequency.json`。

样本混合此前部署版本，且大量错误在代理连接阶段，不能用它估计 6 个一批的因果效果或证明调批大小能解决代理。统计脚本可对后续保留回执继续按日/时段汇总。首次统计脚本漏加本地模块搜索路径的无效结果保留在 `timeout_frequency_draft_import_failure.json`；有效版本 0 读取错误。

封存失败、没有该日任何完整包时，capture_day_status 给出“当日存证缺失，需另行补证或人工锚定”，server 页面追加显示；相关验收测试为旧 `test_capture_failure_warning_identifies_missing_original_inputs`、`test_server_page_displays_incomplete_original_archive`，已包含于本轮累计。若同日已有完整包、后来一次失败，不能把整个日归档误标为缺失；两种状态分别显示。

旧 PID21280 在本轮开始时已不存活，没有冒称一直常驻。已固定新 28 模块清单，重新启动本机隐藏只读采集 PID27840，实际首轮 10-23 09:03 为 waiting_for_close；当前尚未收盘，未伪造 10-23 包。机器保持运行时每五分钟检查，收盘后按新会话采集/重试。未设置重启自启、未部署远端 Poe，也未写正式账本（仍 10-21 / seq19）。

2027 日历仍未补入本地；本轮不替官方安排背书。以后补齐并核验官方日历后，只对拥有完整证据的每个待补交易日按原历史回放路径逐日恢复；无完整包的日子不能承诺自动补账。
