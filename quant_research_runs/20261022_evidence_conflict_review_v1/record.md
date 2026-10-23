# 同址报文冲突：专家四点复核与修复

本轮接受已审核的历史日历归属和2301 oracle，不重做这些结论。将日历首行注释更正为 covered years，2022–2026 年日期集合和计算 AST 不变。

**本轮确实新增生产逻辑修复**：即时冲突标记、封存前后持久化门禁、失败包路由、页面警示和运行统计。不能将本轮再次称为“未改生产逻辑”；没有改变策略参数、数量/BS公式或日历日期。

## 四点结论

1. 原 raw_response 只是追加；旧冲突标记在 adapter 采集结束。本轮在 CaptureSession._raw_response_locked 即时检测并记录 raw_http_conflict，冲突本身不抛异常，HTTP Response 仍按正常路径验证/展示。真实 I/O、大小上限等录制错误仍抛出，不做一概吞错。
2. 显式存证会话内的公共信号封存前 persistence_eligible=False；完整包成功发布后，只恢复原本合格信号。冲突/失败保持 False，给出参考且不落账的说明。StateStore 二次拦 active capture、recording/inconsistent/incomplete；封存前的深拷贝也不能落账。普通无 capture 查询保持独立。
3. 坏包永久留存、不可在原会话消除冲突；下一轮新 CaptureSession。仅失败包时 has_original 明确报不可用，既不将它视为可回放原日证据，也不返回 False 让当前数据兜底。后来的完整新包可以恢复。旧错标 complete 的歧义包也拒绝。
4. 单轮失败是 incomplete，不推进 last_verified_day；后台每五分钟另开会话重试，没有“次数到达便放行”。当日仍无完整包则页面明确“存证缺失，需另行补证或人工锚定，不能承诺自动恢复”。按实际封存尝试记录各品种冲突率，已加入采集器每轮回执；不把轮询当采集尝试。

实现位置和逐个测试名见 delivery/handover.md。本轮不加入通用 URL 响应缓存；上轮已有的具名输入/月链复用继续保留。

## 检查中新增的边界

同一参数字典只换键顺序，原比较会漏掉冲突；新反例 order_before_01 已复现。现在录制端、整包校验与 HTTP reader 共用逻辑参数正规化，保持值类型和列表顺序，不更改已存原始参数/响应，更不是网络缓存。

## 实测：HTTP Date 头确会变化

20:45:52 和20:46:03收到的两个真实 SSE 12月 tstyle 响应，正文 SHA 完全相同，行情 time=162906 也相同，但 HTTP Date 头不同。既有严格响应等同性包含该头，因此诊断探针被标 incomplete；无正式信号或账户写入。此处是响应元数据差异，不能说价格矛盾，也不能宣称收盘后 time 永不刷新。

证据 delivery/time_probe_02/receipt.json。第一次探针引用了不存在的 HEADERS 常量，没发出请求；原失败保留，不能当稳定性样本。本轮不为了让重试成功而忽略 Date，亦不新增通用请求复用。连续失败的最终状态和人工/补证边界如上。

初始真实统计仅覆盖10-22一个交易日：IC 3次封存尝试、0冲突；IM 2次、0冲突，其中1次因传输失败不完整。IC没有同址重复观测机会，因此零冲突不能证明稳定。实际源码重启后的新增尝试由最新 capture_receipt 的 consistency_statistics 另记，不能与这份初始快照混称。delivery/statistics_report.py 可汇总真实观测日期；首五交易日只能等实际运行后积累，不伪造五日数据。

## 验收与旧测试契约

最终 accepted_03：102测试文件、2941通过、原22单列未增加，1005绑定文件前后一致。新增23项，包括即时冲突四类、真实HTTP包装返回、普通查询无capture、公共builder封存、迟到冲突/深拷贝/未封存/写盘失败/上下文失败、新会话恢复、旧歧义包拒绝、参数顺序、统计和页面。

修前备份实际运行 before_02：20项中17失败、3通过，模块实际路径在 actual_backup_modules.json。before_01 曾混用当前server，只作诊断，不能作为完整旧版本验收。compatibility_01 的4个旧 reader 测试先被更早的整包门禁拒绝；原断言保留，并先新增正式loader拒绝断言后，只为该单元测试构造坏的内存reader。accepted_01 剩余1项旧 complete=False分支曾期待失败包返回True，现更新为明确拒绝；没有增加排除项。accepted_02 曾2940通过，但参数顺序反例随后揭示缺口，因此不作为最终版本。所有旧失败/通过回执保留。

在最终同一源码上 real_recovery_02 仍使用旧真实部署IC/IM包，5个服务端场景通过：显式与自动恢复、缺IC、损坏blob、错目录。正例零外部HTTP，隔离账本推进到10-22/seq20；负例不部分落账。自动分支仍是明确模拟10-23开盘前的调度控制时钟，市场输入全部真实10-22。离线就绪检查也通过。没有重写正式seq19。

## 源码、保护与部署

本轮改6个生产文件：bot（注释及信号绑定/回放标记）、signal_evidence（即时冲突及封存）、adapter（状态/统计/兼容末尾检查）、state（第二门禁）、server（页面警示）、capture入口（每轮统计）。2个旧测试有上述契约/fixture更新，新测试单独新增。修改前备份、精确差异和保护结果在本目录。

源码改动前停止旧本机采集器；最终使用 delivery/capture_source_pin.json 固定28模块并重启。最终启动PID及最新运行状态以 delivery/launch_receipt.json、final_verification.json 为准；首轮传输失败仍保留为 incomplete，不能冒称当轮成功，已有完整原日包保持可读。

正式runtime156文件路径/字节、原有原日证据、旧规范和旧输出不变。登记表仅追加本轮说明。没有正式续账、邮件/远端Poe部署或历史NAV重算；2027本地日历及未来真实维护日继续独立。

最终机器核验：新PID 45512 存活，28文件pin核对通过；该轮 attempt=incomplete，当日 archive=captured。已有完整IC/IM包均再次经生产loader验证全部blob；本次新增尝试的代理超时不是报文冲突，未冒称本轮重采成功。最终正式runtime仍156文件/10-21/seq19，原有64个原日证据文件字节不变。

跨更早免费源基线的补充观察：runtime/ic_im_v1_3_r7/.ledger.lock 和 runtime/legulegu/latest.json 在19:35已变化，早于本轮起始快照；当前字节与本轮起始完全相同，未回滚、未认定写入者。不能把本轮runtime未变扩大为自更早轮次起始始终未变。最初跨轮观察和核对后的归属分别保留在 delivery/prior_artifact_protection*.json。历史冻结产物无新增差异。
