# 原日证据独立对抗审计：修复前报告

审计者 ma23_evidence，2026-10-23。范围为原日清单/原始HTTP/reader精确与投影路径、哈希与格式、不可变归档、SSE Date 特例的既有边界。未修改生产源码、旧测试、正式 runtime 或旧原日包；未进行外部 HTTP。新增测试中除最后的真实既有包完整性校验外，行情和破坏材料均为明示的离线合成素材。

## 结果

新增 `test_icim_ma23_evidence_20261023.py`，28 项展开用例，修复前 22 通过、6 失败。`run_independent.py before_01` 对 28 个生产模块加自有测试共 29 项 SHA 做前后绑定，全部不变。实际回执位于本目录 before_01/receipt.json；完整 pytest 结果位于本轮目录 evidence_before_01/regression.xml 和 regression.txt。

## P2：完整包加载未验证原始 HTTP 元数据格式

路径：`CaptureSession.raw_response` → `_raw_response_locked` 原样保存 status_code/encoding → `finish` 标记 complete → `_load_one` 验证 blob/hash/时间/冲突但未校验 status_code/encoding → `load_session` 返回有效 reader。

正常 CaptureSession API，无须伪造或重算清单 SHA，即可录入 status_code 为 None、True、200.0、99、600，或者 encoding 为数字 42。六种包均被正式 loader 接受。实际 `http_response` 消费某行时已有 `type(status_code) is int and 100..599` 和 encoding 类型检查；但只消费具名角色的回放可以永不触发该检查，故“完整清单已核验”与“原始HTTP格式未经核验”存在不一致。

严重度严格校准为 P2 格式/诊断完整性问题。真实 requests 通常保证响应状态码是整数；未证明当前正常上游或正式持仓被污染，不能将六个反例解释为六项实盘故障。损坏的 blob/清单字节本身仍被 SHA 与长度严格拦截。

反例测试：`test_raw_http_metadata_must_be_valid_before_complete_loader_acceptance` 六种参数。

最小建议：在正式加载完整包时，复用 `http_response` 的元数据验证；可同时在录制/封存前拒绝这些不合法类型。合法 HTTP 错误响应仍可留存，不能把所有非200状态都当成无法记录，因为失败主源响应需要审计。对照测试 `test_valid_original_http_error_status_remains_recordable_for_fallback_audit` 覆盖 100/200/302/404/503/599。

## 不报告为问题：精确请求优先于不同参数的批预取

初看同一个 MO 标识在单标识请求与包含它的批预取中出现不同值，reader 只选择精确 URL+params 而不拿批值做交叉比较。但原日确实发起过单标识请求时，其精确逻辑请求已经确定应该重现哪份观测；另一个批预取是不同请求，强制相等会过度拦截。测试 `test_exact_request_retains_its_observation_over_different_batch_prefetch` 固定这一边界。

只有没有精确原请求、必须从多批报文投影出一个标识时，不同批的该变量值才真正无法唯一确定。`test_missing_exact_request_requires_all_possible_projections_to_agree` 验证当前会拒绝此类歧义。此项不作为生产发现。

## 已通过的其他反例

- typed blob/raw blob 破坏、清单哈希不符、累计字节伪报、角色签名不符、原始获取时间超出封存范围均拒绝；后几项使用临时副本重算外层 SHA，独立验证结构检查而非只依赖第一层 SHA。
- 参数 list 的类型、额外键、不存在标识及未保存的混合批请求不会被别的包冒充。
- 逆序和重复读取不消耗虚构 FIFO，仍返回原始匹配字节；封存后无法追加 raw 或 annotation，磁盘清单不变；出现冲突后再次读到首包不能解除失败。
- 对此前已验收的实际 IC/IM 原日包，使用生产 `_load_one` 和 `_verified_blob` 再验全部原清单及 blob 的 SHA，未联网、未修改真实包。

SSE Date 忽略的精确范围此前已有 18 项专项，本次读取当前实现后未发现将特例扩到其他URL或绕过正文/Last-Modified/ETag/status/encoding比较的可达反例。并发、状态存储和页面由其他独立组负责；没有据此声称整个系统无缺陷。
