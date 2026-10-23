# 独立账本与原日恢复审计（2026-10-23 15:52 北京时间）

结论：本范围未发现新增产品缺陷。旧 seq20 来源已闭合，当前源码真实原日包恢复及拒绝门禁通过；今日行情仍不可用，不可声称今日续账或邮件送达。

- 独立重跑来源核验：正式 23 个 JSON 与已保存成功 run39774840955/artifact12423604023 ZIP、已保存 GitHub 元数据和本地任务采纳回执一致；seq0–19 与修复前基线一致，全链有效。正式 seq20 是历史云端合法链，不是本轮回放结果。
- 复制既有恢复驱动至本目录，唯一路径适配是 ROOT 指向工作区及起点改用显式 10-21/seq19 fixture（当前正式已为 seq20，不能再当 seq19）。未修改原测试、正式账本或产品源码。
- 真实 10-22 原日包五场景重跑通过：显式回放、自动恢复、缺 IC 包、IC 必需 blob 损坏、错误目录。两正例零外部 HTTP、幂等续接；三负例均原子拒绝，不出现单腿落账。所有 HTTP 被审计驱动拦截，负例回退尝试记录于回执。
- 当前回放对比既有 v3 real_recovery_02：IC 212 / IM 227 字段仅 signal_calculation_started_at 与 signal_generated_at 不同，完整产品状态相同。不要据此改写历史云端 seq20 的不同信号。
- 对原基线中的 7506 个 outputs 文件及冻结 spec/hash 文件逐字节检查，无变化。五场景测试前后完整 runtime、原日选定包及绑定源码均不变。
- 截至本轮读取，最新实际采集回执为 15:49:07：IC/IM 各 10 次收盘尝试、完整包均为 0，网络报 WinError10013；正式账本仍 10-22/seq20。使用生产 load_original 读取今日 IC、IM 均抛 EvidenceUnavailable，无 HTTP 兜底。

证据：seq20_origin_verification.json、real_recovery_01/receipt.json、verification.json。可重复来源/恢复脚本：independent_seq20_origin.py、independent_recovery.py（恢复输出目录参数必须新命名，保留旧回执）。本轮没有发送消息、外部网络请求、生产写入或重算历史 NAV。
