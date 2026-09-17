# IC / IM v1.4-r1 日报同步记录（2026-09-17）

## 发布身份

- 正式构建：`v1.4-20260917-r1-coreput3x-fixedshort95-fix2`
- 规则版本：`ic_im_v1_4_coreput3x_fixed_short95_20260917_v1`
- 交付版本：`20260917-v14-coreput3x-fixedshort95-fix2`
- 生效信号日：`2026-09-18`
- 策略代码提交：`3c6384f5696c773d254415d18bf6c615f2a66b35`
- GitHub 自动化提交：`782dec13be27946d3b1923931d1f7ea2d5497371`

## 本地 Codex

任务 `ic-im` 保持北京时间每日 14:30，IC/IM 当前入口改为 `poe_ic_im_v1_4_server.py` 与
`run_ic_im_v1_4_github_digest.py`。生产状态固定为 `runtime/ic_im_v1_4_r1`，要求
`ICIM_REQUIRE_MIGRATION=1`。首次状态只能从完整核验的 v1.3-r7 账本经
`migrate_ic_im_v1_3_r7_to_v1_4_r1_state.py` 单向迁移，空状态不允许启动。

## GitHub Gmail

活动工作流为 `.github/workflows/ic-im-v1-4-daily-digest.yml`，固定读取上述策略代码提交，
使用独立 `ic-im-v1-4-r1-ledger`、摘要和发送标记命名空间。首次没有 v1.4 工件时，只允许
恢复最近成功的 v1.3-r7 账本到隔离目录并执行同一单向迁移。旧 v1.3 工作流不再作为活动日报。

工作流保持北京时间每日 20:00 的收盘后发送时点。代码同步不等于邮件已发送；当天真实发送
仍须以对应 GitHub run、Gmail、完成 marker 与 v1.4 账本工件共同核验。

## 验证

- 策略仓库相关回归：265 项通过。
- GitHub 自动化仓库全量回归：374 项通过，119 个子测试通过。
- 自动化仓库推送触发的 `Delivery regression gate`：通过。
- 本地 v1.4 状态：schema 4、revision r1、sequence 0、核验至 2026-09-16，完整哈希链加载通过。

本次发布只产生研究信号与日报。实际成交、行权、结算、交割及账户持仓由用户手动处理。
