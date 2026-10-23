# 最终源码独立集成复验

2026-10-21T06:03:42Z。最终 bot SHA-256：6e56f723bfdd32649911c6d7980b41e46611b9620b5776233109d177b51ffdd3。

原 8 个行为项全部通过；1 项旧 receipt 对当前源码相等的断言按要求排除，原 9 项测试与旧证据继续保留。源码及测试 before/after SHA 均相同，详见 run_receipt.json。1 项第三方 Pydantic 弃用警告。

未扩大范围：真实 2026-10-21 生效日、StateStore 拒绝遗漏/closed/retry 的到期开盘计划、真实 bot→coordinator→runner 在合成暂时开盘取证失败时不写账、不形成成功产物。独立临时 state；没有网络、正式账本或邮件操作。external_review_v1、integration 首轮及 post_fix 的已有证据测试前后未改。

outputs_baseline.json 元数据独立核对为 6783 文件、916698104 字节，采集时间 2026-10-21T06:02:12.587593+00:00；scope 明确为 prospective only; cannot reconstruct original unrecorded history。本行为复验核对该基线文件的 before/after SHA 相同，没有追加声称重新比较全部 916MB 输出字节。既有 v2 manifest 内部一致性与缺少历史独立锚的口径，仍见 post_fix/record.md。

复现：python quant_research_runs/20261021_external_review_redteam_v1/integration/final/run_recheck.py。
