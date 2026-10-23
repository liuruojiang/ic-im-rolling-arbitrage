# 本地 seq20 的历史远端来源核实

结论：**已定位为本地日报自动任务从成功远端正式账本工件恢复的 seq20，完整链和恢复命令均核实一致。** 不能继续将它归为来源不明的数据记录，也不能将其称为本轮 v3 修复生成的信号。

- 写入任务为 `D:/Codex/home/automations/ic-im/runs/20261023/daily_intraday_1428/icim`。其 `adoption_apply_command.json` 记录 `adopt_official_pinned.py --apply` 于北京时间 14:54:41.945–14:54:53.886 执行，退出码 0；`ledger_adoption/adoption_result.json` 记录 seq19→20、`ADOPTED`、历史记录未改写、原字节保留，时间与正式文件吻合。
- 直接来源是成功正式 run `39774840955` 的 artifact `12423604023`（`ic-im-v1-4-r1-ledger`），工件 ZIP 为 137231 字节，SHA-256 `16726b01e4a587e2a31ea6f92c471269cfd2804050a554f29d5630a42939322f`，与保存的 GitHub API 元数据、采纳回执及实际 ZIP 一致。该正式工件的全部 23 JSON 与当前正式账本及下述预检 handoff 一致。
- `production_chain_verification.json` 在 14:54:59 记录全链与迁移核验通过，其中 23 JSON 的 SHA-256 本次全部重新核对成功。相关原回执已逐字节复制至本目录 `local_automation_receipts/` 留档；未访问或更改原备份。

- 已保存的 GitHub run `39892433587`、job `120215258822` 成功预检记录对应策略提交 `8038980de86251b2c5133da04e788dc2c49c84ff`，自动化提交 `f2b77dd4b710101adb897b0d9e40b193faaa10c5`。该预检未发送邮件。
- `readiness_39892433587.zip` 的 SHA-256 为 `04baf5d42d657095a1770a38f57037926de2ffed8211662739407b1633af942a`，与已保存远端日志一致。ZIP 和 run 摘要均与修复前 Git 提交 `58986fda2c17df93eb3925bf09a1cebd55a7869e` 中的原字节一致，非本轮临时补造。
- ZIP manifest 标示来源 artifact `12423604023`，seq20、日期 2026-10-22、digest `49fb159fbadf894b6c64e8ca886faef49d62c5e8e31f0415690209f834317cc0`。26 个成员逐项 SHA-256 全部通过，成员集合无重复或增减。
- 当前正式目录与 ZIP 中 23 个账本文件（latest、migration_record、seq0–20）逐字节完全一致；seq0–19 亦全部与修复前基线一致。完整规范化 SHA 链及 latest 指向验证通过。旧 `local_handoff_preview` 账本也与其一致。
- seq20/latest 文件 SHA-256 均为 `2773323661e43f4cc92e72c743069d9cd93ba23b1051bbe8a714bfc89c0e2fd0`；内容生成时间仍是 2026-10-22 20:02:41，当前新增文件时间为 2026-10-23 14:54:51。

据此可将该链识别为已有远端正式分支的原样恢复，并由自动任务保存的执行命令与完成回执归属本地写入行为。此结论不撤销上轮“本地 runtime 全未变”的错误声明。历史 seq20 应作为既存链只读保留，新版本只能向前续接，不以隔离回放的不同结果改写它。

本次先在两个相关目录有限检索 1177 个文件/归档成员，再按另一审计者定位读取本地自动任务的明确回执及正式 ZIP；无网络请求、无发送、无生产写入。核验前后正式账本文件集合和字节不变。可重复脚本：`verify_seq20_origin.py`；机器证据：`seq20_origin_verification.json`。
