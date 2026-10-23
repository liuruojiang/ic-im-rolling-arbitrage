# 第三批再审计：并发与唯一后台任务生命周期

2026-10-21。本组独立审计第三批并发修复，生产只读，根代理集中修改。最终 `concurrency/final_01/receipt.json`：**20项通过，0失败/错误/跳过，8.24秒，902份源码/规范/夹具前后同字节**。其中**14项是支持路径/已修缺陷验证，6项是明确未支持能力的诊断**；不能将20项全部解释为20个生产缺陷已修。

## 本轮确认并修复的真实可调用缺陷

唯一后台 `_refresh_loop` 原本直接await `coordinator.catch_up_once`。该方法的读取head和官方日历判定在其内部replay try之外；异常会逃出后台task，未到第一次300秒sleep，之后不再自动重试。

- `refresh_before_04`：实际 `_refresh_loop → asyncio.to_thread → 实际catch_up_once → _latest_completed_exchange_day`，有效临时账本、合成2027-01-04 15:20时钟触发未覆盖官方年历。首次输出1 FAIL、2.74秒，源绑定前后一致。未mock业务body或日历门禁，仅以受控sleep决定测试何时退出；错误在第一次sleep之前逃出。
- `refresh_before_05`：2026正常已完成日，第一次实际StateStore head读取注入临时OSError，第二轮head本来已追平，无需任何报价provider。首次输出1 FAIL、2.50秒，源绑定前后一致；同样提前终止任务。

根代理最小修复只在 `_refresh_loop` 增加 `except Exception`，记录 `last_refresh_error`，继续既有300秒节奏；没有把未知2027当作有效交易日，也没有改catch_up_once的查询/写账语义。最终两反例均转绿。正例通过真正的 `asyncio.Task.cancel()` 在受保护的to_thread await中取消，确认 `CancelledError`传播，任务不继续sleep、不把取消记成刷新错误。该测试不声称Python工作线程可被强制取消；同步worker由测试Event正常释放。

这是正常单server唯一后台任务的可达可用性缺陷，未证明现网曾因该错误退出，也未证明错误导致仓位污染或日历门禁被绕过。

## 其余支持路径

- 一条查询body异常后，另一coordinator线程能取得共享锁，Poe捕获/observer和持仓锚点按新请求隔离。
- append验证失败、首次文件锁获取失败后，head和journal不被部分写入；后续真实重试可成功。
- 独立子进程读者在“journal已fsync、head未替换”窗口读取有效旧head，原子替换后读取有效新head。
- 子进程真实持锁时强杀，以及写好journal、尚未换head时强杀，操作系统释放锁，重启只推进一次；原journal字节保留，再用旧head重写必须拒绝。
- 冲突pending journal重试拒绝且不覆盖；运行时钟及回放ContextVar在正常返回/异常路径均恢复。

所有信号、标签和时钟是有明确标注的合成测试输入；所有StateStore、子进程及server模块默认初始化均在隔离临时目录，未访问正式账户、发信、部署或续写正式账本。并发性质测试没有借合成价格冒充真实市场复验。

## 六项不适用的初始能力期待

`initial_01`原始输出为6 FAIL / 5 PASS（4.45秒）。六个失败要求同线程递归execute_query恢复外层observer/anchors或恢复任意预安装observer。通过mock run主动递归可观察外层observer被清空、异store锚点切换；但当前server、digest及strategy引用点未发现支持这种递归回调的生产拓扑。现有coordinator拥有其observer，未声明任意外部observer栈或递归query组合能力。

根代理确认该假设不适用，不将其扩成已支持新功能；本轮不为它增加复杂重入栈。**这六项不是修复后转绿，而是原先把未注册能力当契约的期待撤回**。`initial_01/test_source.py`保留完整原测试源码及原XML/hash；`classification_02`以明确名称检查既有限制和不落账，6份诊断观察分阶段保留。没有新增pytest排除项，未将这六项称为已修生产缺陷。

17项原测试的精确源码保存在 `pre_refresh_17_test_source.py`，其SHA与当时source_before逐项一致。引用点和分类边界见 `scope_classification_v2.json`。

## 阶段和验收

- `classification_02`：11 PASS，严格收据有效；当时仅用于分类完成状态，不是最终20项验收。
- `process_death_03`：17业务断言通过，但运行期间根代理修改state源码，严格门禁正确拒绝；该阶段不计为最终同字节通过。
- `refresh_before_04`、`refresh_before_05`：正常后台路径首败，完整保留。
- `final_01`：20 PASS，严格XML、退出码、source before/after和工件哈希验收通过；根最终累计完成后另增共同源码核对证据，不覆盖本次收据。

本组未改策略参数、数量、月度时点、Put候选、IC无Call/IM不新卖Call、未知年份拒绝和旧账本只读语义。

注入边界补记：临时head读故障的注入点是该临时实例load_latest方法的首次调用，其后恢复实际方法；未损坏文件，亦未声称真实磁盘或生产文件系统曾发生故障。

## 根调整后的最终复验 final_02

根仅撤回额外期货core_target到期比较后，原20项断言和测试源码不改，于concurrency/final_02再次通过：20 PASS、0失败/错误/跳过、8.26秒，902份绑定前后相等，strict receipt核验通过。final_01保留为此前源码阶段，最新采用final_02；唯一后台任务两项反例及真实取消正例继续通过。根累计验收成功后另增共同字节核对。

## 最终累计共同字节对齐

已独立严格重验accepted_adversarial_2：1767 PASS、0失败/错误/跳过；本组final_02的20项亦严格重验通过。独立902份映射与累计906份清单共有902份，逐项独立/累计/当前SHA完全相同；独立清单之外的额外脚本为0份。证明见shared_bytes_accepted_adversarial_2.json。首累计有45失败的阶段不采用，旧文件不改写；本组20项含于1767，不重复相加。该对齐不把6项未支持能力诊断称为已修生产缺陷。
