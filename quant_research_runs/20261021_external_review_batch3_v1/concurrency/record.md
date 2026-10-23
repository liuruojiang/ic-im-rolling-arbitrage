# 第三批：并发入口对抗审计

审计日期：2026-10-21。职责范围为真实协调器 API、共享 strategy 上下文、StateStore 线程及跨进程读写。审计代理只新增独立测试与本目录证据，生产修复由根代理完成。所有运行使用本轮 run_checks.py；StateStore、模块默认 store 及子进程目录全部临时隔离，未读取或写入正式账本，未联网、部署或发送消息。

## 已证缺陷与修复边界

| 项目 | 首失败证据 | 实际影响和边界 | 本轮修复 |
| --- | --- | --- | --- |
| 多协调器共享全局上下文 | fixture_corrected_1 的同 store / 异 store 两个 coordinator 场景失败。A 的报告与观察结果被 B 捕获，A 可读到 B 的 anchors。 | 真实 execute_query 公共 API，业务体为明确标注的合成替身。证明输出、observer 和 anchors 串扰；未证明生产正式仓位污染。当前单 server 的请求与 refresh 共用单例，其原实例锁有效。 | 模块级共享 RLock 保护所有 coordinator 的 strategy 上下文。 |
| 首次创建锁文件竞争 | fixture_corrected_1 的两个独立进程同时 append，在加锁前的 NUL 写入 flush 处出现 PermissionError。随后未取得锁仍尝试解锁，异常被覆盖。 | 恢复 journal / latest 后尚无锁文件的实际可调用 StateStore 路径，属于可用性缺陷。已有锁文件被另一进程持有时，受控等待正常，不能泛称所有并发写入均失效。 | 删除锁前写入；以 acquired 标记仅解锁已成功获得的锁。 |
| 加锁失败后的清理 | lock_controlled_before：注入 acquisition OSError 后，旧实现的无条件 unlock 触发 AssertionError 并覆盖原异常。 | 确定性的错误清理反例；不把注入异常解释成真实市场或生产事故。 | 原 acquisition 异常保留，不进入受保护工作，也不释放未取得的锁。 |

Windows 原生双进程探针 empty_native_lock_probe 证明：零字节文件可以直接锁住 byte 0，第二进程等待并在释放后取得锁，文件仍为零字节。无需预写 NUL。

## 入口调用链与不处理项

poe_ic_im_v1_4_server 的 PersistentICIMBot、refresh 和 health 使用同一个模块 coordinator。run_ic_im_v1_4_github_digest.build_artifacts 每次创建 LedgerCoordinator；这是可被同进程并发调用的公共 API，但通常 CLI 分进程运行，未据此推断现有生产并发故障。模块共享锁覆盖已证的同进程多实例 API。

bot 的 __main__ 直接调用 ICIMMainlinesBot.run，不经过协调器锁。该文件不导入 StateStore 或 append_confirmed_signals；其 observer 注册由 server 提供。直接 run 与 coordinator 混用仍属不支持的单文件无状态入口，已有工作区规则禁止将其替代长期 server。本轮不扩大到该入口的架构重写，也不把直接 bot 的报告视为获准实盘输出。

StateStore.initialize 与 append_confirmed_signals 使用同一文件锁；append 在锁内重新核对头摘要，再写 journal 和原子替换 latest。load_latest 无写锁，读取已提交的 latest 并验证其对应的完整哈希链。已复现 journal 写完、latest 尚未替换期间读者继续读到有效旧头，以及中断后从既存 journal 恢复。没有发现需要额外生产修改的读写覆盖缺口。本测试不声称任意外部程序手工复制活跃账本安全。

## 证据演进

各阶段均单独保存 regression.xml、regression.txt 和 test_run.json，未覆盖旧阶段。

| 阶段 | 结果 | 解释 |
| --- | --- | --- |
| initial_1 | 3 通过、6 失败 | 两项全局串扰；另四项因合成信号缺少 fear 时间证据等 fixture 不完整而失败，不列为生产缺陷。 |
| fixture_corrected_1 | 6 通过、3 失败 | 修正测试证据后，两项全局串扰和一项真实双进程锁前写入失败成立。 |
| lock_controlled_before | 1 通过、1 失败 | 已有锁正常等待；未取得锁仍解锁的错误清理反例成立。 |
| empty_native_lock_probe | 1 通过 | 零字节文件原生锁可用。 |
| targeted_after_1 | 14 通过 | 两项生产修复后定向复验，含三个全新目录的首次双进程 append。 |
| targeted_after_2 | 17 通过 | 增补并发 initialize、缺 head 恢复拒绝和双进程中断恢复覆盖。 |

17 项最终覆盖：3 种 coordinator 关系、业务异常后全局恢复、非持久查询不能写入、两个 StateStore 线程竞争、3 次首建锁文件跨进程竞争、1 次跨进程既存 journal 恢复、已有锁等待、取得锁失败清理、零字节原生锁、journal / head 窗口读取、单进程中断恢复、并发 initialize 只创建一次、只有 journal 无 latest 时拒绝重建。

## 最终独立复验

最终阶段为 final_20261021_1，由本目录 final_reverify.py 创建全新目录，绑定九个生产及支撑模块、独立测试、两个 fixture 辅助模块、冻结 spec、runner、strict gate 和复验脚本，共 16 个源码文件。脚本要求源码运行前后完全一致、进程退出零、XML 实际 testcase 计数吻合且无失败 / 错误 / 跳过；receipt 绑定五个必要输出的 SHA-256，并经 validate_receipt 独立复核。历史生产字节仅与本轮 baseline 的 backup 比较，未要求旧 receipt 等于修改后源码。

final_20261021_1：**17 通过，0 失败、0 错误、0 跳过，27.51 秒**。16 个绑定文件运行前后哈希完全相等；strict receipt 验收通过。九个生产及支撑模块的原始 backup 字节也与 baseline 清单逐项一致。仅有 fastapi_poe 的既有 Pydantic 弃用警告。

累计最终阶段 accepted_batch3_2 已通过 strict receipt：1652 通过、3 项明确单列，0 失败 / 错误 / 跳过，897 个源码绑定前后相等。旧 accepted_batch3 已拒绝，不采用。

独立逐项核对结果保存在 shared_bytes_accepted_batch3_2.json：本组 16 个映射中，累计清单收录的 15 个全部与本组最终源码一致，也全部与当前字节一致。剩余一个为本目录独立复验包装脚本 final_reverify.py，累计清单未收录；其自身独立 before / after / 当前三份哈希一致，不将其表述为已纳入累计绑定。两份 strict receipt 均再次独立验收通过；该共同字节证据同时记录两份 receipt 与 source_after 的 SHA-256。

并发组审计及共同字节核对已完成。此前阶段仅作为初始反例和修复过程证据。
