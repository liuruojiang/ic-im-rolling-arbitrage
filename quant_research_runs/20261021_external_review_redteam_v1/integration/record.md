# 2026-10-21 外部评审修复：独立集成与证据边界审计

仅新增本目录与根目录 test_icim_redteam_integration_20261021.py。没有改生产代码、旧测试、旧证据、正式账本或部署；没有发信或访问实盘。自有 run_audit.py 使用独立临时状态与自有 JUnit/receipt 路径，未调用旧 run_checks.py。

## 结果

独立 9 项测试通过；1 项第三方 Pydantic 弃用警告。验证过真实 2026-10-21 生效日，未降低 OPEN_EVIDENCE_EFFECTIVE_DATE。实际链路为 bot 的 signal handler → build_live_trade_signal → 真实 ordinary-opening evidence/retry gate → LedgerCoordinator.execute_query/catch_up_once → digest.build_artifacts。行情成功数据没有伪称为真实，本组注入的只有合成失败条件；没有完成真实当天 IC/IM 双品种成功写账验收。

对开盘暂时取证失败：真实普通 Put evidence 生成 awaiting_retry_open_evidence，顶层 bot 吞异常后仍缺少 IC/IM 完整 observer 结果，coordinator 不写账，runner catch-up 失败并拒绝成功产物。未发现异常被吞后继续写账的反例。StateStore 在真实生效日独立拒绝遗漏状态、旧 closed_missing_open_price 和 retry 状态；测试精确要求 unresolved opening evidence 错误并核对全部 JSON 字节不变。

historical/forward 边界按 signal day 判定。10 月 20 日旧 closed 状态不受新的 due-plan gate 限制，10 月 21 日起相同状态被拒绝。这与新增 spec 的前向边界一致，不构成将历史结果重算的修复。

## 确认的验收证据范围缺口（P2；非执行逻辑缺陷）

prepare.py 对 immutable 的选择条件是 outputs 内 *_spec.md、.sha256 或路径包含 ic_im_system_mainlines_v1。v2 的 13 个正式输出均不在 backed_up 或 immutable 集合，包括 mainline_metrics.csv、mainline_state.json、record.md 和 output_manifest.json。verify_final.py 仅循环这两个集合，并未检查 v2 manifest。因此 immutable_unchanged=266 证明的是被选择的 266 条路径，不能证明“全部首次正式输出未被改写”。

可复核证据见 evidence_verification.json 的 v2_files_outside_baseline_protection。本审计未修改这些文件，也未发现实际被覆盖的证据。建议最终结论限定为列举路径保护通过；若要证明所有首次正式输出，应另做完整清单/原有冻结 manifest 内容核验，保留本次既有证据。

## 与旧证据的独立勾稽

- 1113 份备份字节全部与 baseline 对应 SHA 相符；当前变化恰为 final_verification 声明的 7 文件；266 条 immutable 无变化；receipt 中源代码 SHA 一致。
- source_delta.patch 可由备份和当前源码独立重建为相同内容；两份新增 spec 的 SHA 均通过。
- 旧 regression.xml 确有 989 个 testcase，41 文件，无 failure/error/skipped；这只支持旧测试所执行场景。verify_final.py 并没有把测试执行源码快照、XML、run_checks/run_all 纳入同一个不可变摘要，因此不能单靠 receipt 证明任意后续源码仍已跑过这套测试。本次源码 SHA 独立对上，未发现测试后源码漂移。
- 本审计测试前后对 external_review_v1 下全部现存文件比对 SHA 一致，见 run_receipt.json。

## 必须维持的公开边界

record.md 已明确披露 CFFEX 真实归档抓取超时、A 合约未正式支持、IC 卖 Put 归一化文字冲突、未部署/未发信/未重算绩效，以及完整优先级计划未完成。这些不得改写为已经完成，但也不应冒充本次新发现的执行缺陷。

真实数据证据支持：保存的 Sina CON_OP 价格字段、SSE A 交易代码与独立实际 strike 区别、独立保存的 SSE 行与 monitor evidence 绑定；不支持真实完整当天 IC/IM 成功信号、完整生产 ledger/runner 回放验收、全历史收益影响、真实 CFFEX 缺日恢复成功或任何下单建议。

## 复现

python quant_research_runs/20261021_external_review_redteam_v1/integration/run_audit.py

python quant_research_runs/20261021_external_review_redteam_v1/integration/verify_evidence.py

初次运行暴露两项合成夹具问题（新日期缺网格来源、恢复到旧 bootstrap 调用新 validator），均在本组新测试中修正；没有把这些测试夹具失败列作生产缺陷。
