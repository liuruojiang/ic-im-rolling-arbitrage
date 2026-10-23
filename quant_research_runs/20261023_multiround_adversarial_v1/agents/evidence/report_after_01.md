# 原日证据格式边界修复后独立复验

2026-10-23，依 root 分配仅修改 `ic_im_signal_evidence_v1.py`，未修改原冻结 28 例或其他生产文件。

## 修复

新增 `_validate_http_metadata`：status_code 必须是 exact int，范围 100..599；encoding 为 None 或 str。合法 HTTP 4xx/5xx 继续允许保存，供失败源审计。

CaptureSession 在 finish 时逐条检查 raw 元数据，格式错误记为未解决的 `raw_http_metadata` 失败，完整性为 False；不会仅因格式错误而向正常查询抛出新异常，已绑定信号在封存结束后变为 incomplete、persistence_eligible=False。正式 loader 和 has_session 也检查，以拒绝旧版 producer 声称 complete 的坏元数据包。reader 的精确 HTTP 和批投影读取复用同一校验，不改变原匹配语义。

未改变 SSE Date 精确特例、跨不同参数单标识/批预取的精确请求优先、哈希规则、价格/数量/日历和策略。

## 复验

- 冻结原测试 `test_icim_ma23_evidence_20261023.py`：28 项全部通过；原始修前 6 失败回执保留。
- `agents/evidence/after_01/receipt.json`：28 生产模块+自有测试共29个绑定前后一致，exit0；root 公共 pytest 明细在 `evidence_after_01/`。
- `verify_metadata_repair.py`：额外手工边界回执（不混入28项计数）证明格式错误可以正常结束录制，但包不完整、绑定信号不可落账；对临时旧版伪完整清单，load 和 has 都拒绝。真实原日 IC/IM 的全 blob 再验仍包含在原28项中。

AST 变更限定：新增 `_validate_http_metadata`；修改 `CaptureSession._finish_locked`、`EvidenceReader.http_response`、`_load_one`、`has_session`。完整补丁 `metadata_repair.patch`。

修前 SHA：`bdf2a4879a64977361bfa550eb0cf5129933b4adf3b4704fef87e12ab9b48b3a`。

修后 SHA：`6afb37ddfc61b67e023214f339cd67d358d16971cae2ea11ba2e3465a8ae18a6`。

严重度保持 P2 格式一致性，未升级为正常上游可生成或正式持仓污染的结论。最终多组统一回归、服务端端到端恢复和部署源码 pin 仍由 root 汇总核验。
