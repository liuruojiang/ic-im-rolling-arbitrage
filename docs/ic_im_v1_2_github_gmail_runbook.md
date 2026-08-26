# IC/IM 1.2 GitHub/Gmail 收盘日报运维手册

状态：研究候选查询面；不生成订单，不代表账户持仓，不替代冻结V2主线。

## 架构

- 策略仓库：`liuruojiang/ic-im-rolling-arbitrage`，入口`run_ic_im_v1_2_github_digest.py`。
- 自动化仓库：`liuruojiang/codex-daily-automation-probe`，工作流`ic-im-v1-2-daily-digest.yml`。
- 状态：自动化仓库Actions Artifact `ic-im-v1-2-ledger`，包含原子`latest.json`和完整SHA-256日志链，保留90天。
- 邮件：复用自动化仓库已有`MAIL_*` GitHub Secrets；完整逐腿Markdown作为附件。

GitHub Runner每次都是新环境，因此必须先恢复最新账本工件。没有工件时只允许从源码内2026-08-24已审计检查点首次启动；不得手工改日期、改JSON或跳过交易日。

## 一次性设置

策略仓库与自动化仓库均按微盘股方案设为公开仓库，工作流直接检出公开策略仓库，不需要`ICIM_REPO_TOKEN`。

自动化仓库原有以下Gmail密钥继续保存在GitHub Actions Secrets中并直接复用，无需复制：`MAIL_SERVER`、`MAIL_PORT`、`MAIL_USERNAME`、`MAIL_PASSWORD`、`MAIL_FROM`、`MAIL_TO`、`MAIL_USE_SSL`。仓库公开不会公开这些Secret值，但Actions日志和工件本身可被公开查看，因此不得把密钥或私人信息写入报告与日志。

## 调度与重试

- 工作日北京时间16:30、17:00、17:30触发。
- 每次先检查A股交易日和当天交付标记。
- 前两次数据未齐时不发旧信号、不写新账本，只上传诊断并等待下次触发。
- 任何一次成功后发送一封收盘确认邮件，上传新账本和当天交付标记；后续触发为空操作。
- 17:30仍失败时发送一封异常邮件并保留旧账本；邮件明确要求不要依据旧邮件调整。
- 手工`workflow_dispatch`可用于补发；`correction=true`会绕过当天交付标记并添加“纠正版”主题前缀。

## 成功门禁

1. IC与IM必须同时存在，且`market_date`等于最近完成交易日。
2. 两者均为`close_confirmed=true`。
3. 账本只能逐交易日推进，不能部分推进或跳日。
4. 新生成邮件信号必须与刚写入账本的`signals`逐字段一致。
5. `latest.json`、全部日志序号、前序摘要和SHA-256必须通过校验。
6. IC Call必须保持为0；IM救援期限继续使用`rescue_next_listed`。

## 每日巡检

- GitHub工作流结论成功，Gmail步骤成功。
- 邮件主题日期为信号收盘日，而不是旧账本日。
- 邮件中的`verified_day`等于信号日，`sequence`只递增不回退。
- 当天只有一个未过期交付标记；重复计划任务没有第二封邮件。
- Actions中最新`ic-im-v1-2-ledger`工件可下载，且下一次运行显示`restored=true`。

任何失败都只保留上一份已验证账本。不得把失败报告、旧目标或部分成功品种解释为当天调整信号。
