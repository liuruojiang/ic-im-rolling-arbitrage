# IC/IM 1.4 GitHub/Gmail 与本地 Codex 日报运维手册

状态：正式研究信号；只发布策略参考路径，不接交易接口，不代表账户实际成交、行权、结算、交割或持仓。

## 正式入口

- 策略构建：`v1.4-20260918-r1-coreput3x-fixedshort95-fix3-iciv30-qdelta05`。
- 本地和远端 runner：`run_ic_im_v1_4_github_digest.py`。
- 持久状态：schema 4、revision r1，独立目录/工件 `ic-im-v1-4-r1-ledger`。
- GitHub 工作流：`ic-im-v1-4-daily-digest.yml`，北京时间20:00收盘确认发布。
- 邮件交付标记使用独立 `ic-im-v1-4-r1-*` 命名空间；旧v1.3标记不得抑制v1.4首封日报。

## 首次迁移与恢复

1. 优先恢复最近成功的v1.4正式账本工件并验证完整链与迁移证明。
2. 只有从未生成过v1.4工件时，才恢复最近成功的v1.3-r7正式账本到隔离目录，再调用`migrate_ic_im_v1_3_r7_to_v1_4_r1_state.py`单向生成schema 4链首。
3. 迁移后所有正式进程必须在导入server/digest前设置`ICIM_REQUIRE_MIGRATION=1`和`ICIM_STATE_DIR`。空目录、schema错误、迁移证明缺失或哈希断链均失败关闭；不得创建测试bootstrap。
4. 诊断运行使用隔离账本，不能上传为正式工件。旧账本只读，不重写历史记录。

## 发布与去重

定时发布只使用`close_confirmed`。工作流先运行交付回归、恢复/迁移账本、核验固定策略提交与BUILD_ID，再生成报告、邮件正文、发送intent、Gmail结果、正式账本和交付marker。发送intent存在但完成marker缺失时，禁止自动重发，须先核对原run和Gmail。

本地Codex每日14:30任务与GitHub Gmail分别验收：本地输出不等于邮件送达，不另行SMTP补发。两者必须显示相同1.4构建、策略revision、信号边界和到期条件分支。

## 回滚

代码回滚只切回上一已验证提交；账本不得降级或覆盖。若v1.4运行失败，保留最近成功v1.4工件和失败审计，不自动恢复发布v1.3日报，也不把旧信号标成当天结果。
