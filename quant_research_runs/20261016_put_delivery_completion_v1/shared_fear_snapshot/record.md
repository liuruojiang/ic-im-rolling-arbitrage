# 同请求IC/IM恐慌来源快照一致性修复

范围：仅修改经过Git树校验的 `formal_runtime/poe_ic_im_mainline_v1_4_bot.py` 及既有 `formal_runtime/test_ic_im_fear_source_delivery_20261016.py`。未修改共享root、策略阈值、来源选择规则、消费者一致性门禁或正式账本。修改前完整备份位于 `../backups/shared_fear_snapshot/`。

每次 `_handle_signal` 请求创建独立ContextVar作用域，按实际信号日/模式缓存第一次成功取得的真实恐慌证据。两产品以及失败时的独立观察使用同一请求访问器；每腿取出深拷贝，JIU引用及其他逐腿富集无法修改缓存。Poe与LedgerCoordinator收盘捕获均走同一入口。作用域在异常、取消及正常退出时finally reset，后续请求重新抓取；不使用进程全局lru缓存。直接单腿访问且无作用域时每次重新抓取。

新增8项测试：同请求同hash/深拷贝与JIU update隔离，跨请求重新抓取，不同日/模式隔离，无scope不缓存，异常及CancelledError重置，fetch失败后新请求，嵌套请求隔离与恢复，以及原生_handle_signal的盘中/收盘连续双请求。恐慌专项25项加月度展示36项，共61项通过、0失败/错误/跳过，2.74秒。日志为本目录regression.txt/xml及test_run.json；未覆盖此前门禁证据。

最终SHA256：bot `1eb13a383a678a5b5b39354b9fc1aa70f1feb4732bf557bba7c92f3c941ca093`；测试 `1ab84cafa5459ac749cfebb28325cebb1b08b9a51461b1d9960cb6ee853d7019`。交主审发布与重跑真实无发送就绪流程；本角色未发布或发送。
