# IC/IM 与微盘日报修复远端同步验收（2026-10-09）

## 结论与正式提交

用户授权：多智能体对抗检查、最小修复、同步远端并核验远端日报。本次未改策略参数、冻结规格、旧信号身份、历史账本或正常发送时点。

| 对象 | 发布与验收 |
|---|---|
| IC/IM 执行代码 | [策略PR #41](https://github.com/liuruojiang/ic-im-rolling-arbitrage/pull/41)已合并；正式pin `408c9badea5351ae7c0021f9b1e9adfdb804d554` |
| 云端生产、合同与readiness | [云端PR #137](https://github.com/liuruojiang/codex-daily-automation-probe/pull/137)已合并，提交`06767dbb1d61cdc6f25f0cbededef52db155eae4` |
| 零跳过门禁、固定Put表 | [云端PR #138](https://github.com/liuruojiang/codex-daily-automation-probe/pull/138)已合并；当前云端main `2dc8b2c6394b926277ac85a428140040f5769669` |
| 本地自动化 | `ic-im`累计IC/IM专项23文件；生产pin更新408c9b，工作日14:15和其余配置保持；已通过工具更新并读回 |

IC/IM build=`v1.4-20261008-r1-ic-csi500-abs40-fix11`，rule=`ic_im_v1_4_ic_csi500_ma105_w16_abs40_static_20261008_v1`，delivery=`20261008-v14-ic-csi500-abs40-fix11`。微盘v2.0/v2.3/v2.5身份、策略固定pin `de6ce1a9f05bf9032d3423b63121f778723db3d0` 保持。

## 修复及独立验收

1. CFFEX异常类型/有限重试、共享下载预算、response关闭和本轮失败诊断，见`docs/ic_im_v1_4_cffex_transport_release_20261009.md`。原10/09本地失败首先发生在10/08历史回放，不能误称已获得10/09完整行情。
2. 云端独立正式共同交易日历、数量/分项/差分公式及普通/3倍Put待执行计划生命周期二次校验；独立marker CLI也执行完整合同。两工作流断言打包器日历与固定策略一致。已有22项、15项与5项异常基线分别保存；修复后独立反例和真实产物通过。
3. 两族CI均施加60秒专项子进程限时、无字节码/pytest缓存与零skip XML门禁。微盘原CI 482 passed/1 skipped，实际因disposable checkout缺v2.3正式CSV跳过17文件中的必需项。现使用完整真实CSV、whole manifest及SHA来源证明，仅装载测试checkout，补齐漏掉的v2.5 realtime artifact invalidation文件。IC/IM使用已独立全链核验的正式10/08工件作为测试夹具；测试环境仍指向临时状态、require migration=0，绝不指向生产账本。
4. IC/IM从10/09新日报起补齐正文与HTML固定Put表：估值档、最终档、MOM120、袖数量、记录比例及合约证据。IM普通核心保护1.5张与独立生命周期0张明确分开。IC记录20张/IC不冒称当日动态重算；缺Delta及独立逐合约报价日/时点/来源保留N/A，MO95% IV参考合约不回填102%交易选约。原附件保留正式producer记录，新正文明确解释不同数量字段；不改原产物。

最终[云端CI run37913153242](https://github.com/liuruojiang/codex-daily-automation-probe/actions/runs/37913153242)：automation **764 passed**、IC/IM **531 passed**、微盘 **486 passed**，全部零失败/错误/跳过。各组与本地专项重叠，不相加为独立测试数量。

## 真实IC/IM无发送readiness

[main run37911491710](https://github.com/liuruojiang/codex-daily-automation-probe/actions/runs/37911491710)于北京时间17:28:26启动，17:30:37完成；确实恢复隔离账本、运行正式close producer、正常打包器及marker CLI、SMTP探针，非绿色空跑。

| 证据 | 实际值 |
|---|---|
| 源策略 / 自动化 | 408c9b… / 06767db…，与当次工作流一致 |
| 正式恢复来源 | 实际发送的main成功run37773833605，ledger artifact11549730442；13文件、11条journal、迁移证明及完整SHA链通过 |
| 恢复锚点 | 2026-10-08，seq10，digest `8b6e07f348fa372668fe312f83830b8c2e766934dfcc26023b5cd44485b3d4bb` |
| 当次结果 | close_confirmed，行情/核验日2026-10-09，下一实际交易日2026-10-12，advanced_sessions=1 |
| 隔离producer序号 / digest | seq11 / `b1568e9bd5905a185c9f085c60e3dc6dbc20a6ccb8719da0f9a9a7851a2a98fc` |
| IC / IM | 双腿0.5→0.5，HOLD；IC无Call，IM无新卖Call；恐慌31.12 / 10/09 / same_day_post_close |
| 数据限制 | 当时PE/PB与中债源只有10/08值，正式明示代理/冻结回退；IV因超过20分钟容忍为N/A；未把缺值说成无预警 |
| SMTP | TLS/auth/MAIL/RCPT/RSET通过，`message_sent=false`；未发DATA |
| 工件 | 11607311463，26,142 bytes，SHA256 `2cc4878d015083ee9b96f05253727f4a833b4cbe990e0ef5470ed77c30d3fa3e` |

76项独立读回通过。readiness只上传报告/邮件预览和回归工件，没有正式ledger、send intent或delivered marker；新journal未上传，因此不宣称已独立下载重算seq11新链或已更新生产链。最终Put展示补丁复用同一已成功快照重打包并通过独立15项验收；未重复抓行情/续账。最终远端CI用冻结真实10/09结果执行新展示；生产、readiness、SMTP、恢复器与微盘日报源码在PR138中未改变。

## 实际邮件与去重分别验收

- 微盘今日真实发送为run **37902309346**：三版10/09 close-confirmed、正式revision、HOLD/scale1通过；SMTP receipt于16:06:55接受，delivered marker随后完成。run37902414161是去重空跑。当前工作流文件为workflow_dispatch，PLANNED_BJ=16:00，未修改其时点。小工件给100唯一成员及100/100价格缓存刷新，whole pack通过；未下载大型状态包，因此不额外宣称独立逐票重算云端ST零交集或CSV缺失的quote_coverage字段。
- 主协调只读Gmail确认微盘邮件 **1a11fb36b24b61a2** 在INBOX，16:06:53，主题`[收盘确认][下个交易日无需操作] 微盘股 v2.0/v2.3/v2.5 日报 - 2026-10-09`，正文run、日期及de6ce1a策略pin一致。
- IC/IM最近一次实际正常邮件为run **37773833605**、10/08 20:00；ledger/intent/delivered一致，Gmail **1a11b651c7e600db** 已在INBOX（20:02:51）。未知intent检查在所取10/08以来范围内为空。10/08主题、正文、HTML在最终补丁下逐字相同。
- 今日IC/IM17:30无发送readiness通过，不等于今晚20:00正常邮件已发送或投递；仍需按该实际run及INBOX另验收。未调用SMTP补发、删除intent、重置marker或触发正常日报发送。

## 命令时间与证据

| 命令/步骤 | 北京时间 | 耗时 | 退出 |
|---|---|---:|---:|
| 本地IC/IM累计23文件 | 16:54:24.819–16:54:36.140 | 11.319s外层 | 0 |
| 发布408c9b隔离30文件 | 17:24:18.498–17:24:34.033 | 15.535s外层，531 passed | 0 |
| readiness官方producer步骤（含中债准备） | 17:29:31–17:30:35 | 64s步骤合计 | 0 |
| readiness正常digest/marker | 17:30:35 | <1s步骤 | 0 |
| readiness SMTP无DATA | 17:30:35–17:30:36 | 1s步骤 | 0 |
| 最终10/09正式CLI仅重打包 | 17:41:21.849–17:41:22.046 | 0.198s | 0 |
| 最终远端automation / ICIM / 微盘pytest | run37913153242，17:43:59–17:45:01 | 23.25s / 9.20s / 20.67s pytest | 0 / 0 / 0 |

完整证据根：`D:\Codex\home\automations\ic-im\runs\20261009_remote_sync\`。主要文件：`strategy_fixture_publish_checks.json/.log`、`automation_final_display_publish_checks.json/.log`、`final_repack_summary.json`、`final_repack_20261009/`、`remote_audit/readiness_37911491710/review/report.md`、`remote_audit/cloud_completion_ci_log.out`、`microcap_final_audit/audit_summary.md`、`gmail_confirmation_microcap_20261009.json`、`gmail_confirmation_20261008.json`。GitHub命令均有`.command.json`、原stdout/stderr与实际退出码。

本地canonical账本未恢复或续写，仍为schema4/r1、seq9/09-30/f279667c…，12个业务JSON保留原SHA；用户已有研究目录未动。代码回滚不得降级或覆盖业务链；冻结spec、旧审计和已发送邮件不回写。
