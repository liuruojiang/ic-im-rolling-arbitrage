# 独立并发与预算对抗审计

2026-10-23。范围：两线程 C/D 批处理、独立四线程采集、ContextVar、CaptureSession 同角色合并、封存/写入、异常和字节预算。没有修改生产源码或旧测试，没有联网、写正式 runtime 或发送消息。

## 已确认 P1：外层四线程采集器的 shutdown 后漏检预算

触发路径：`capture_daily` 为品种建立绝对 deadline → 四线程的 `run_job` 在 fetch 前后检查 → 全部 future 完成 → `ThreadPoolExecutor.__exit__` 等待线程收尾 → 退出 `_network_budget` 清除 ContextVar → 检查角色/封存。`__exit__` 后没有再次检查 deadline。

真实工作线程配合可控虚拟单调时钟复现：工作任务均在 deadline 前返回，shutdown 耗时将时钟移至本品种起点+46秒。IC/IM 仍返回 captured，manifest 完整标记为 True；起点+44秒对照正常。不是主张45秒必须强杀请求，而是已承诺的“等待计入同一预算、超预算不能成功”在外层采集器没有守住。结果只证明原日输入包完整状态与预算承诺不符，不声称该包已造成正式仓位污染或已在真实市场发生。

复现：`test_independent_four_worker_collector_cannot_seal_success_after_shutdown_deadline[46.0]`。建议在四线程池的 with 完全退出后、退出同一个 `_network_budget` 之前再次执行 `guard(8)`。这会让捕获上下文将当次包封为 incomplete；不能把检查放在 deadline 已恢复的 scope 外。

## 其他对抗结果

- 四线程竞争同一 role/key，仅一次 fetch，每个消费者获得私有值；活动 fetch 期间 finish 拒绝，释放后正常封存。
- 工作线程抛 KeyboardInterrupt/SystemExit，父线程等待另一个活跃 raw writer 完成，再由 capture_context 封为不完整，未提交后续批次。
- 四线程同时写 raw 接近总字节上限，成功/失败条数和累计字节原子一致，不完整信号不被提升资格。
- 新浪批次重试继承较短父 deadline，时限耗尽后不会发起第二次请求或额外批次。
- 真实线程逆序完成不改变返回批次顺序；HTTP 回放逆序请求仍按 URL+参数精确匹配。
- 同线程嵌套的旧/新 role wrapper 只记一个 typed blob，累计字节没有孤立计数。

全部市场内容均为明示的离线合成内容。这里没有把45秒硬墙钟缺失当作新问题；Python不可安全强杀正在运行的线程这一已披露限制仍然成立。

## 修前独立回执

`before_01/receipt.json`：实际9项，8通过、1失败，0错误/跳过；28生产模块加自有测试共29绑定前后一致，156正式runtime文件集合/字节不变。完整 XML、收集集合、标准输出及源码哈希保留于该阶段。此前给父智能体的“10项”预估已按实际收集数更正。

本 agent 未直接修复生产，等待 root 对确认问题处理后再做固定源码独立复验。
