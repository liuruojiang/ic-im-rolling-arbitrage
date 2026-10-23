# 修复后独立集成复验

2026-10-21 UTC；原 external_review_v1 和本组首轮 integration 证据未改写。执行产物全部位于本 post_fix 子目录。

当前 bot SHA-256 为 1be3211a2541b996dccfc69dbe67321e78bdda22cb048bf538fa1e07fbb9f708。对应 source-before/source-after 一致的独立测试 8 项通过；原源码 receipt 相等性测试有意排除 1 项，因为新增修复合法改变了 bot，原 receipt 继续保留首次修复源码身份。其余生产源码和测试源码完整 SHA 见 run_receipt.json。

真实 2026-10-21 cutoff、真实 bot observer/coordinator/runner 失败链与 StateStore 拒写复验继续通过：synthetic opening outage 不能因顶层吞异常转成完整 observer 结果、不能写账或形成成功 digest。实际使用独立 temp state；没有网络请求、正式账本操作或邮件动作。

## v2 原冻结证据补核

独立核验 12 个 payload 文件的大小和 SHA-256 全部等于现有 output_manifest.json。第 13 文件是 manifest 自身，它无 self-entry。manifest 当前 SHA 为 0ec7c6dc1079b21734ffa10a1d3a443268acf308c0f8440292922a07409081e1。

当前 checkout 的 git ls-tree HEAD、git ls-files --stage 及 git log --all 对 outputs/ic_im_system_mainlines_v2 均为空，check-ignore 明确该 manifest 被忽略。因此没有可用的已提交输出字节，也没有找到独立历史 root hash；仅能确认与现存冻结 manifest 的内部一致性，不能把它提升为上轮修复全过程未改写的独立证明。原先 P2 审计覆盖缺口仍作为历史证据限制保留；本次并未发现产物实际被覆盖。

建议正式记录使用以下口径：

> 既有 v2 的 12 份 payload 当前均与现存冻结 output_manifest 一致。manifest 自身缺少本 checkout 可核验的独立历史锚。新增全 outputs 基线自其记录的审计采集时点起提供后续保护；它不追溯证明此前修复全过程的字节不变，也不补造原始 baseline。此前 266 条保护范围及其限制保持原记录。

本组没有提前认可尚未生成的全 outputs 补充基线；该基线的实际采集时间、文件总数、完整清单及后续比较应由新增 receipt 单独核验，不得写成原 prepare.py 曾经覆盖。

复现：python quant_research_runs/20261021_external_review_redteam_v1/integration/post_fix/run_recheck.py；python quant_research_runs/20261021_external_review_redteam_v1/integration/post_fix/verify_v2.py。
