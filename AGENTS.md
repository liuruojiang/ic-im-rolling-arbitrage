# Workspace Rules

- 开始任何研究、组合修改或实盘接入前，先读`notes/mainline_registry.md`、`docs/ic_im_system_mainlines_v1_spec.md`和`outputs/ic_im_system_mainlines_v1/record.md`。
- IC主线明确不含Call；不得把任何历史IC卖Call诊断重新解释为主线。
- IM的5%救援期限是`rescue_next_listed`：相对旧到期日严格更晚的最近实际挂牌到期日，不是固定增加一个日历月。
- 绩效收益使用每1倍期货30%保证金/缓冲；15%只用于用户提供的实盘资金可行性上限，未获经纪商独立验证。
- 报告和“最新信号”是研究审计证据；登记表未标记“获准实盘”时，不得生成或解释为自动/人工下单建议。
- 冻结`*_spec.md`、SHA-256和首次正式输出不可改写；新假设必须新版本预注册。
- 修改数据转换、执行、仓位或风险逻辑前先备份，并按真实数据验证。
- v1.2长期Poe只允许经`poe_ic_im_v1_2_server.py`读取持久账本，并由唯一收盘任务逐日续接；不得用单文件无状态部署、硬编码“最新日期”或手工改写账本替代。
- v1.2 GitHub/Gmail 日报只允许由`run_ic_im_v1_2_github_digest.py`读取并推进哈希链账本；自动化仓库必须恢复最近成功的`ic-im-v1-2-ledger`工件，IC/IM同日完整成功后才允许发成功邮件和上传新账本。失败邮件不得展示陈旧目标为当天信号。
