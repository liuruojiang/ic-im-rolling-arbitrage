# 并发与发布资格独立复核

2026-10-23。本组不改生产文件、旧测试和正式账本；测试离线运行。线程场景明确使用合成报文和可控时钟；发布资格场景复制已核验的真实输入字节，并显式标注为合成采集，不把它当作新的真实原日包。

## 已实证的两项 P1

1. **四线程采集收尾遗漏同一网络预算检查。** 原逻辑的各 worker 已在预算内完成，但线程池退出后没有再次检查产品 deadline，可在模拟 +46 秒时仍发布 complete 原日包。+44 秒控制组正常。修复在退出线程池、退出同一 `_network_budget` 之前增加 `guard(8)`。本项针对“预算耗尽仍成功”的承诺，未把 45 秒硬墙钟缺失另列问题。
2. **源 pin 校验晚于 complete 发布。** 未纳入 pin 的项目模块在采集后段实际执行 18 次，IC/IM complete 包已经通过 `adapter.load_original`，随后 runner 抛出 `SourcePinError`，两包仍可被 `has_original` 识别。仅事后隔离有读者窗口。修复在会话创建时保留 scoped publication guard，在完整 manifest 发布前验证；失败封为 incomplete，并由 runner finally 再核验、保留 `SourcePinError`。

两项均为实际执行路径的离线反例，不声称已经污染正式仓位。源 pin 场景需要采集过程中出现未 pin 的实际依赖，严重度按 P1。

## 固定反例和修后结果

| 阶段 | 用例 | 实测 | 源文件前后 | 正式 runtime |
|---|---:|---|---|---|
| `before_01` | 原并发 9 项 | 8 过、1 失败 | 29/29 一致 | 156 文件未变 |
| `after_01` | 原并发 9 项 | 全过 | 29/29 一致 | 156 文件未变 |
| `pin_publication_before_01` | 发布资格原反例 1 项 | 失败，两个包仍可读 | 29/29 一致 | 156 文件未变 |
| `pin_publication_before_02` | 兼容更早防线的同一反例 | 失败，18 次执行、两个包仍可读 | 29/29 一致 | 156 文件未变 |
| `after_02` | 原冻结并发 9 项 | 全过、无跳过 | 29/29 一致 | 156 文件未变 |
| `pin_publication_after_01` | 兼容更早防线的同一反例 | 通过、无跳过 | 29/29 一致 | 156 文件未变 |

每次独立绑定 28 个生产模块及本组对应测试文件；阶段目录保留 source_before/source_after、正式 runtime 哈希、pytest XML 和 receipt。原 9 项测试始终不改。

新发布资格反例为允许更早拒绝做了小幅适配：在模块真实 `consume` 时记录调用；只有 complete 时才要求 `load_original` 成功；`has_original` 的 `EvidenceUnavailable` 视为拒绝。攻击条件和“最终不得留下可用原日包”断言保留。修前精确字节存于 `pin_publication_before_01/test_used.py`，SHA-256 为 `8799aee4b78ac0bc3753a307c714b58ef75d06b32e2d069ebd0d092e27bf2069`，原 receipt 未改；说明见同目录 `test_adaptation_note.json`。

## 其他已验证边界

- 四线程同 role/key 只调用一次 provider，各消费者获得独立对象；active fetch 期间不能 finish。
- `KeyboardInterrupt`/`SystemExit` 路径等待正在写入的真实线程结束，不留下会话封存后的晚到写。
- raw 字节预算并发计数原子一致；不完整会话绑定的信号无持久化资格。
- 新浪重试继承父 deadline；预算耗尽后不启动第二轮请求；ContextVar scope 能还原。
- 批次返回顺序不依赖完成顺序；HTTP 回放按 URL 与参数命中，不依赖线程调用顺序。
- 同线程嵌套 role wrapper 不重复写 blob、不重复累计字节。
- `loaded_sources` 先快照 `sys.modules`，采集线程退出后再检查；本组未发现枚举变更引入新的并发回归。

`finish` 发布前 guard 无瞬时 complete 可见窗口的专门测试由 evidence 组新增，本报告不冒领其用例。这里的独立端到端反例验证执行过未 pin 模块后，runner 仍报告源错误，且没有可用原日包。
