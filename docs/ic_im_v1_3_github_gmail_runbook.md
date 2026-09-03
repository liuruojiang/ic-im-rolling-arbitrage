# IC/IM 1.3 GitHub/Gmail 盘中实时日报运维手册

状态：研究候选查询面；不生成订单，不代表账户持仓，不替代冻结V2主线。

## 版本隔离

- 策略入口：`run_ic_im_v1_3_github_digest.py`。
- 新工作流应使用独立名称`ic-im-v1-3-daily-digest.yml`和独立工件`ic-im-v1-3-ledger`；不得覆盖v1.2工作流或`ic-im-v1-2-ledger`。
- 首次发布前，用`migrate_ic_im_v1_2_to_v1_3_state.py`重放生成并验证v1.3账本，再将该链作为新工件种子。
- 工作流切换属于独立发布动作；本地1.3验收通过不等于已获部署或实盘授权。

## 调度与门禁

每次运行先恢复最新v1.3 r5工件，验证`strategy_version=1.3`、`strategy_revision=r5`、迁移证明和完整SHA-256链，再按交易日逐日补到最近完成交易日。盘中报告只允许在连续交易时段生成，IC/IM必须同时完整、行情日为北京时间当天、`state_anchor_day`等于最近完成交易日，且生成前后账本摘要、序号和日期完全不变。

失败结果会原子覆盖`result.json`并删除同目录旧成功报告；邮件步骤必须以`result.status=ok`为发送门槛。发送成功去重标记仍由外部邮件工作流负责，键至少包含revision、模式、market_date和账本digest。

IM盘中成交量门禁使用上一已确认交易日占位并明确披露，不能把当日未收盘累计量当成全天量；盘中快照不得入账。任何一次成功后只发送一封邮件并保存交付标记；后续重复触发为空操作。失败邮件不得展示旧目标为当天信号。

成功报告必须显示：构建号、行情日、账本核验日、IC/IM当前仓位与下一交易日目标、动量Score/Abs20、IC基础NAV回撤与6%防守状态、IM成交量比率与极热门禁、网格、Put/Call原因和数据源。IC Call必须为0，IM救援期限必须保持`rescue_next_listed`。

每日巡检最新`ic-im-v1-3-ledger`可下载且下一次运行显示恢复成功。任何缺源、日期不一致、摘要错误或部分品种成功都只保留上一份已验证账本，不发送可被误解为当天调整信号的报告。

## 远端启用记录（2026-09-03）

- 策略仓库PR #6已合并到`main`，提交`f8207fc7d04bca04b296e148c83d6d5a57bdaea0`。
- 自动化仓库`liuruojiang/codex-daily-automation-probe`的PR #36和#38已合并；工作流`IC IM v1.3-r5 Realtime Digest`处于启用状态，原`IC IM v1.2 Realtime Digest`已停用。
- 首次远端闭环run `33708813109`成功：从v1.2工件重放迁移、生成1.3-r5收盘确认信号、构建并发送Gmail、上传`ic-im-v1-3-ledger`、上传交付标记和审计工件均为success。
- 首次远端账本核验日为2026-09-02，sequence=7，digest=`c3d7d80ccf1287cc9dd79591d40fc1014035b08e8a8ceae5ead8e6d299bb6daa`；交付标记为`ic-im-v1-3-r5-close_confirmed-digest-delivered-2026-09-02-c3d7d80ccf12`。
- Codex自动化`ic-im`已改为只通过v1.3-r5正式研究信号路径处理IC/IM；运行频率仍为北京时间每日14:20预检、14:30后发布。
