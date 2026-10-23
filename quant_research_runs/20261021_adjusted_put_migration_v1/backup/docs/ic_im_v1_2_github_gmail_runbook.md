# IC/IM 1.2 GitHub/Gmail 盘中实时日报运维手册

状态：研究候选查询面；不生成订单，不代表账户持仓，不替代冻结V2主线。

## 架构

- 策略仓库：`liuruojiang/ic-im-rolling-arbitrage`，入口`run_ic_im_v1_2_github_digest.py`。
- 自动化仓库：`liuruojiang/codex-daily-automation-probe`，工作流`ic-im-v1-2-daily-digest.yml`。
- 状态：自动化仓库Actions Artifact `ic-im-v1-2-ledger`，包含原子`latest.json`和完整SHA-256日志链，保留90天。
- 邮件：复用自动化仓库已有`MAIL_*` GitHub Secrets；盘中摘要和完整逐腿Markdown作为附件。
- 正文：IC、IM各自先列当前值与下一交易日目标，再解释动量Score/Abs20、估值网格阈值、Put估值档与MOM120、Call D10/IV26或5%救援、月度展期原因；动态文字必须来自本次完整信号JSON。

GitHub Runner每次都是新环境，因此必须先恢复最新账本工件。没有工件时只允许从源码内2026-08-24已审计检查点首次启动；不得手工改日期、改JSON或跳过交易日。

## 一次性设置

策略仓库与自动化仓库均按微盘股方案设为公开仓库，工作流直接检出公开策略仓库，不需要`ICIM_REPO_TOKEN`。

自动化仓库原有以下Gmail密钥继续保存在GitHub Actions Secrets中并直接复用，无需复制：`MAIL_SERVER`、`MAIL_PORT`、`MAIL_USERNAME`、`MAIL_PASSWORD`、`MAIL_FROM`、`MAIL_TO`、`MAIL_USE_SSL`。仓库公开不会公开这些Secret值，但Actions日志和工件本身可被公开查看，因此不得把密钥或私人信息写入报告与日志。

## 调度与重试

- 与微盘股相同，北京时间13:03、13:18、13:33设置三个计划触发；GitHub近期约延迟65–75分钟，实际通常约14:20–14:40执行。
- 每次先检查A股交易日和当天交付标记。
- 每次先把哈希链账本补到最近已完成交易日，再从该状态生成当日盘中预估；当日盘中快照不入账。
- 前两次数据未齐时不发旧信号，只上传诊断并等待下次触发。任何一次成功后发送一封“盘中实时”邮件，上传续接账本和当天交付标记；后续触发为空操作。
- 最后一次仍失败时发送一封异常邮件并保留旧账本；邮件明确要求不要依据旧邮件调整。
- 手工`workflow_dispatch`默认`realtime`；只有明确补发收盘纠正版时才选`close_confirmed`并设置`correction=true`。

## 成功门禁

1. 账本健康状态为`ok`，且`verified_day`等于最近完成交易日；盘中通常是上一交易日。
2. IC与IM必须同时存在，且`market_date`等于北京时间当天。
3. 两者均为`close_confirmed=false`、`market_phase=盘中`，并从同一`verified_day`账本续接。
4. 生成前后的账本序号、日期和摘要完全不变；盘中快照不得入账。
5. `latest.json`、全部日志序号、前序摘要和SHA-256必须通过校验。
6. IC Call必须保持为0；IM救援期限继续使用`rescue_next_listed`。

## 每日巡检

- GitHub工作流结论成功，Gmail步骤成功。
- 邮件主题带`[盘中实时]`，日期为当天行情日；正文明确写明须等待收盘确认。
- IC、IM卡片均存在“为什么是这个结果”，并且Put/Call原因与表格目标一致；缺少原因字段时不得凭经验补写当天数值。
- 邮件中的`verified_day`是上一已完成交易日，`sequence`只递增不回退；当日盘中快照不增加序号。
- 当天只有一个未过期交付标记；重复计划任务没有第二封邮件。
- Actions中最新`ic-im-v1-2-ledger`工件可下载，且下一次运行显示`restored=true`。

任何失败都只保留上一份已验证账本。不得把失败报告、旧目标或部分成功品种解释为当天调整信号。
