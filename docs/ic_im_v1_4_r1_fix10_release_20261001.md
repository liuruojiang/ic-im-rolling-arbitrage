# IC / IM v1.4-r1 fix10 正式研究信号发布记录

日期：2026-10-01。首个新规则信号日：2026-10-08。

- 冻结规格：`ic_im_v1_4_r1_fix10_fear_grid25_50_20261008_spec.md`；SHA-256 见同名 `.sha256` 文件。旧规格和已确认账本未重写。
- 策略：PR [#30](https://github.com/liuruojiang/ic-im-rolling-arbitrage/pull/30) 合并新规则；PR [#31](https://github.com/liuruojiang/ic-im-rolling-arbitrage/pull/31) 更新旧日身份断言；供日报固定读取的提交为 `158428c8381d22fbf8ab43cc2e16649afceab590`。
- 云端交付：PR [#124](https://github.com/liuruojiang/codex-daily-automation-probe/pull/124) 合并为 `001cff6ddae84284c6226be9ac7cd72f0fd07fc5`，工作流固定读取上述策略提交，按信号日选择历史/fix10 构建与交付身份。Actions run [36805360671](https://github.com/liuruojiang/codex-daily-automation-probe/actions/runs/36805360671) 的自动化、IC/IM 策略及共用回归三项通过。
- 本地主工作区快进至策略提交 `158428c8381d22fbf8ab43cc2e16649afceab590`，保留原有未提交研究材料；`ic-im` 14:15 定时研究任务继续沿用原调度、账本和交易边界，并追加 fix10 前向说明。
- 2026-09-30 fix9 收盘日报有成功运行与账本工件（云端 run `36712111895`），不作为 fix10 前瞻成功证据。2026-10-08 新版信号、数据源当日可用性、邮件送达、实际账户成交均尚未发生/未验证。公开历史恐贪序列缺少点时旧版，既有历史改进只属模型回放。
