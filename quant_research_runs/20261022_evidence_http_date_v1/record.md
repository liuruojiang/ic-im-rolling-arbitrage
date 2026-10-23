# SSE HTTP Date 误冲突修订

2026-10-22。独立接续上一轮专家反馈，生产变动限于 ic_im_signal_evidence_v1.py 的响应一致性比较。上一轮规范、失败记录和首次交付保持原样。

## 为什么需要改

上一轮真实 SSE 510500_12 探针已经显示：行情正文 SHA-256、正文 date/time 均相同，HTTP Date 相差 11 秒，但原规则将其视为不同输入。RFC 9110 §6.6.1 将 Date 定义为消息生成时间；§8.8.2 的 Last-Modified 表示资源的修改时间。策略代码不读取 HTTP Date，行情新鲜度仍依赖原有的行情时间与 Last-Modified 校验。

本轮只对完整匹配 `https://yunhq.sse.com.cn:32042/v1/sho/list/tstyle/510500_MM`、月份 01–12 的地址，在一致性比较时去掉 HTTP Date。录制、回放、统计复用同一规则。Date 原文逐次完整保留于哈希保护的 raw 清单；读取继续采用最后一个等价观测，并保留 matching_recordings，不伪造某次调用与某条原始观测之间的对应关系。

正文任何改变，包括行情 date/time、价格或空格，仍然冲突。Last-Modified、ETag、Content-Type、状态、编码及其他地址的 Date 不放宽。旧不完整包仍不完整；不能通过本轮升级将失败包复活。不增加通用网络缓存，不改变选约、数量、新鲜度、执行日或持久化资格门禁。

## 验证记录

- `before_01`：真实双响应测试在旧代码下失败，另外 17 项通过。保留旧源码备份、实际模块绑定和失败回执。
- `focused_01`：18 项通过，含原始真实双响应的重新封存、读取和响应头保留；8 类实质差异、7 类非适用地址、旧失败包和伪造完整包反例。
- `accepted_01`：2959 项通过、原 22 项单列不变，但并行诊断在绑定目录新增了 network_probe_213441.json，文件集合检查拒绝验收。无已有文件字节变化；不把这次回执列为最终成功。
- 将后续诊断回执改写 delivery 后，`accepted_02/receipt.json` 最终 2959 项通过、22 项单列；1010 个绑定文件集合与字节前后一致，103 个测试文件，原第五轮 19 项包含。`delivery/verify_delivery.py` 随后通过，正式 runtime 156 文件、原有归档 107 文件字节不变，28 模块 pin 与实际进程一致。
- `live_probe_01`：21:35:41 与 21:35:56 两次真实 SSE 响应正文 SHA 均为 1585a5f2655f9572693945f21e973a8bb0729ff2d4a29f4b379220b4937015df，正文 date=20261022、time=162906，HTTP Date 不同。新会话成功封存并回放，匹配记录 2 条、失败 0；探针 original_close=False，不生成策略信号或污染正式原日包。
- `real_recovery_01`：真实部署目录中的 IC/IM 包经 LedgerCoordinator 显式 historical_replay 与自动恢复均在账本副本推进一日，正常路径外部 HTTP 请求 0。缺 IC 包、损坏 IC 必需 blob、错误证据目录均原子拒绝，不允许单腿落账。自动分支仅调度时钟模拟次日 08:00，行情始终是真实 10-22 数据。
- `delivery/recovery_facts_comparison.json`：与上一轮相同原日 manifest 对照，显式与自动两路径的 IC 212、IM 227 个字段及完整 products 状态严格 JSON 类型一致。仅排除既定 fetch_time、signal_calculation_started_at、signal_generated_at 三个计算时刻，其余无差异。

## 代理和采集状态

本机代理由当前 Codex 进程提供。最早存在连接拒绝、403 与超时；本轮初次较短诊断超时，后续 RFC、中金所和 SSE 分别成功，真实 SSE 双响应也成功，但并发采集仍有间歇代理超时。不把“端口监听”或单个请求成功当作网络已经稳定；没有停止代理进程、绕过代理、关闭 TLS 或调整生产请求时限来掩盖问题。

已停止旧 PID 45512，重建 28 模块源码 pin，启动新版隐藏只读采集进程（PID 21280）。首轮 21:41:03 回执为 incomplete；当日归档状态为 captured，来自早前已经完整的 IC/IM 包。两者必须分别报告。采集每五分钟以新会话重试；不完整包隔离保留，不手工推进正式 last_verified_day，不保证持续网络失败能靠重试收敛。最新进程、回执、统计和包 SHA 以 delivery/final_verification.json 的核验时点为准。

## 历史与部署边界

本轮未改策略、日历、数量或回测计算代码，没有重算历史 NAV。正式 runtime 156 文件、原有证据字节、上一轮绑定历史输入及旧规范须由最终机器检查确认保持。此前历史日历修复对 IC 回测的影响结论仍然有效，不能把本轮的局部不变扩大成五轮累计完全无影响。

正式账本仍为 2026-10-21 / seq19；恢复验证只操作副本。未声称远端 Poe 已部署、服务端环境已同步、正式账本已续接或机器重启自动启动。服务端仍须明确指定 ICIM_EVIDENCE_DIR，2027 官方日历及后续真实维护日包的完整性继续独立核验。只观察到一个交易日，不能推断多日稳定性。

协议依据：https://www.rfc-editor.org/rfc/rfc9110.html#name-date ，https://www.rfc-editor.org/rfc/rfc9110.html#name-last-modified 。
