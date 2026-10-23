# 独立审计：历史影响、改动归属与验收证明链

2026-10-23。只读生产、旧回执与历史产物；新增独立测试 test_icim_ma23_history_20261023.py。没有联网、正式账本写入或生产修复。

## 修前发现

### H-AUDIT-1：验收器不核对 XML 用例身份与实际执行集合（建议 P1，验收工具）

位置：本轮复制的 evidence_gate.py 的 validate_receipt。validate_run 验证 XML 内部数量、状态与重复身份；validate_receipt 验证 collection 的 selected/executed 集合及数量，但两者之间只比较数量，不比较用例身份。

在临时目录复制上一轮真实 2982 项回执的全部七个工件：

1. 只将 XML 中一个成功 testcase 改成另一个成功名字，重新计算 XML 工件哈希，其余不变，旧验证器仍接受。
2. 保留 XML，改 collection 的 collected/selected/executed 中一个节点为未执行的模块/测试名并重算该工件哈希，旧验证器仍接受。

这能将“同样数量的另一组成功 XML”和本组 collection 误配通过，违反原报告的“XML、退出码、实际执行与收集清单相符”承诺。不是生产交易逻辑漏洞，也不能据此声称旧 2982 回执实际混用；本组另行使用 pytest 自身的 mangle_test_address 核对真实旧 XML/collection，结果一致。

反例测试：test_receipt_rejects_xml_test_identity_not_in_executed_collection、test_receipt_rejects_wrong_collection_identity_even_with_same_xml_count。

修复宜在本轮新副本校验 XML 的 (classname,name) 与每个 executed nodeid 的规范化身份，保留参数中的 ::、点、斜线、Unicode 和 Windows 反斜杠；不改旧冻结工具或回执。已有五个合法边界用例验证这些情况。

### H-AUDIT-2：非前缀真实模块未列入运行时依赖清单（建议 P2，部署防错）

位置：capture_ic_im_v1_4_inputs.loaded_sources。函数只接受 sys.modules 中以 ic_im_ / poe_ic_im_ 开头的名字；真实生产直接依赖 im_put_policy 不匹配。

当前正式 28 项 source pin **已经包含** im_put_policy.py，16 个静态本地采集依赖也全部被覆盖，所以本组未发现当前发布版实际漏保护。问题在自动验证：即使 im_put_policy 已经真实加载，把它从一个新建 pin 的 files 中删去（其余27文件用当前实际 SHA），SourcePin 构造及 verify(loaded_sources()) 仍通过。回执 source_sha256 也不会列该模块。以后重新打包发生同样漏绑时不能自动发现。

反例测试：test_loaded_inventory_includes_actual_nonprefixed_production_dependency、test_pin_missing_real_loaded_im_policy_is_rejected。测试加载真实 im_put_policy，不伪造 sys.modules。生产应补入这项真实依赖检测，或使用排除测试模块的实际本地依赖闭包，不能把当前 pin 已人工补齐误说成自动检测已经完整。

## 已通过的反证及归属核对

- 免费源、冲突、HTTP Date、生产归属四轮 exclusions.json 的哈希完全相同，仍为22项，未发现新增排除掩盖新行为。
- A–H 原始源码快照均与其冻结回执 SHA 对上；565d719c…属于免费源验收，46c617cc…同时对上冲突与 Date 轮的 bot 绑定。没有发现 Date 轮新增未披露 bot 改动。
- 原 2982 项真实 XML 与实际 collection 节点身份相符。本组确认的是工具接受不一致工件的缺口，不是历史造假或旧结果必错。
- 当前28文件 pin 覆盖16个静态本地采集依赖，包含 im_put_policy。
- 选择核对 BS/IV 和历史价格/开盘/收盘函数的 AST，与免费源轮修前快照一致；前次验收绑定的历史 calendar_repair 输入/研究源码/回执 SHA 仍一致。未发现最近取数、存证修订新增对这些已确认历史计算路径的改动。
- 历史日历扩展曾改变 IC 实算：原报告 full CAGR 为35.392395%→34.814538%。不能把最近一轮未改数学或冻结输出保持，扩大成所有历史修复零影响。没有重新运行或改写历史 NAV。

## 执行与素材边界

首次 history_01 为6失败/8通过，其中两项是本组新素材断言错误：错误地用字符串 split(::) 解析含 :: 的真实参数，以及把报告原精确百分数当成四位小数文本。已保留原测试副本 test_history_before_fixture_correction.py 和首轮输出；改用本地 pytest 官方实现并匹配原精确值，未改既有测试或生产。

有效修前复验：agents/history/before_02/receipt.json，14项、4失败/10通过、0 errors/skips。28生产模块、自有测试、新 gate、runner和本组驱动哈希在运行前后相同；XML在 history_before_02/regression.xml。市场数据不存在合成冒充；反例只合成测试回执内容，修改均限临时副本。

待 root 修复后，使用同一测试独立复验，再由 root 统一对最终源码做全量绑定。当前报告截至修前，不自动把别组或未来修复计为本组已验证通过。

## 经 root 明确委派后的修复与独立复验

随后 root 将上述两处修复明确委派给本组。本组仅修改本轮 `evidence_gate.py` 与生产 `capture_ic_im_v1_4_inputs.py`，修前两文件逐字备份于本组 backup/，其 SHA 另有清单；未改任何旧 gate、旧回执或既有测试。

- 新 gate 使用本地 pytest 的 mangle_test_address 将 executed nodeid 映射到 JUnit 的 classname/name，逐一核对 XML 身份集合并拒绝规范化碰撞。参数内 ::、斜线、点和 Unicode 保留。原两种同数量错身份反例均被拒绝。
- loaded_sources 从采集入口递归解析本地 import 依赖闭包，再与实际加载的模块文件位置相交；同时保留原生产前缀动态模块识别。真实 im_put_policy 不再漏掉，无关 pytest 或研究模块不会因仅位于工作区而加入清单。删掉该真实依赖的新 pin 会报不覆盖已加载模块。
- 原14项未改再次运行：**14通过、0失败/错误/跳过**，28生产模块+自有测试+新gate/runner绑定前后相同，见 `agents/history/accepted_01/receipt.json`。测试输出在 `history_accepted_01/`。没有将别组结果混入本组计数。
- 新 gate 单独只读验证原2982回执仍通过；原七个验收工件和四轮旧 gate 在前后 SHA 完全相同，见 `readonly_2982_verification.json`。不推断原源码此刻等于已修复的新源码，也不重写旧的已签收结果。

所有组的最终生产源码统一绑定与全量验证由 root 继续执行；本组两项复验并不替代全局回归。
# 发布前源码门禁的交叉修复补验

并发组另用实际执行的未 pin 动态依赖及复制真实原日输入，证实采集结束后的源码核验太晚：完整包已先发布。该项由并发组保存修前反例，本组只负责 collector 接口接入。

本组进一步修改 `capture_ic_im_v1_4_inputs.py`：`capture_daily` 在 `evidence.publication_guard(self._verify_sources)` 作用域内执行，存证模块在发布 manifest 前执行源码核验；`cycle` 的最终 `_verify_sources()` 移至 `finally`，确保一般取数异常被转成 incomplete 后，源码漂移仍抛 `SourcePinError`，并经现有 run 路径退出 2。adapter、旧轮规范、回执与原 14 项测试均未修改。接入前版本另存 `backup/capture_before_publication_guard.py`。

复验：`history_publication_guard_01` 的原 14 项全通过，28 个生产模块及自有验收文件在运行前后绑定稳定。`history_publication_cross_01` 的新增发布 guard 正负路径 7 项、真实字节复制的未 pin 发布反例 1 项及旧 collector 25 项合计 33 项全通过；该交叉运行直接使用新 RUN，完整累计源码绑定由 root 最终验收完成。并发组独立报告未 pin 依赖实际执行后 IC/IM 均无可加载原日包，且最终 SourcePinError 继续外传。

