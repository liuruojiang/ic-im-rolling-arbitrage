# IC 和 IM 滚动套利主线工作区

本工作区保存2026-08-20冻结的两套 V2 研究主线、可复核代码、真实数据、正式输出和审计证据。它是从“新策略研究”中提炼出的主线工作区，不包含IC卖Call主线；V1仅保留为历史基线。

## 当前主线

- IC：1倍滚IC + 四档买Put（25%/50%/75%/100% Delta，MOM120<0时最低50%）+ `0.375/1.000`固定估值网格；网格最多新增1倍且不加Put；不卖Call。
- IM：1倍滚IM + 四档买Put（上限4张，MOM120<0时最低4张）+ `0.85/1.25`网格 + 每日D10/IV26/5%救援Call；Put和Call不随网格仓放大。

权威规则见[主线冻结规格](docs/ic_im_system_mainlines_v2_spec.md)，冻结结果见[主线记录](outputs/ic_im_system_mainlines_v2/record.md)，期权日期定义见[日期审计](outputs/option_expiry_semantics_audit_v1/record.md)。

## 目录

- `docs/`：冻结规格、运行后审计和研究流程。
- `outputs/`：首次正式输出及主线依赖证据。
- `data/`：迁移时保留的IC/IM原始数据。
- `quant_param_scan_runs/`：与保留主线依赖相匹配的参数扫描工件。
- 根目录Python文件：主线及其递归本地依赖。
- `migration/`：迁移清单、哈希和源工作区清理记录。
- `archive/`：迁移时的原研究登记表快照，仅供追溯。

## 核验

```powershell
python verify_workspace.py
python -m pytest -q test_freeze_ic_im_system_mainlines_v2.py test_option_expiry_semantics_audit_v1.py test_poe_ic_im_mainlines_v2_bot.py
```

## POE 上传入口

POE 只能上传或粘贴根目录的 `poe_ic_im_mainlines_v2_bot.py`。不要上传
`test_freeze_ic_im_system_mainlines_v2.py` 或 `freeze_ic_im_system_mainlines_v2.py`：
它们依赖本地工作区路径和冻结产物，不是 POE 运行入口。POE 主脚本不依赖
`__file__`，测试会在刻意不提供该变量的 `<poepython>` 环境中完整执行源码。

POE V2 的发布测试把 `data/ic_monthly_discount_roll_v1/cffex_raw/202608.zip`
作为必须随工作区分发的实证归档，缺失即失败而不是跳过。该文件已登记在
`migration/workspace_manifest.json`，SHA-256 为
`4b75710904a158d3c016f2ceb54681370c69d407a570139977ff7f1f8669f9ef`。

## 远端边界

Git远端保存源码、测试、规格、审计文档和迁移清单。约628MB的本地真实数据、
正式输出与参数扫描工件由`.gitignore`排除，仍以本工作区及迁移清单中的哈希为准；
因此依赖这些工件的冻结测试需要在完整研究工作区运行，不能把纯Git克隆误称为完整数据副本。

本工作区当前状态仍是“研究主线、未批准实盘”。实时行情、合约映射、盘口容量、冲击成本、异常成交和经纪商保证金尚未完成生产审计。

## 2026-08-23 动量分袖研究候选

冻结 V2 主线保持不变；另有独立的 IC/IM 动量分袖 v1.2 研究候选：

- IC：50%裸滚核心袖使用完整V2 Put，50%中证500动量袖只使用估值Put并删除重复的MOM120下限；IC不卖Call。
- IM：50%裸滚核心袖继承IM v1.1 Put/Call，50%中证1000动量袖不配置Put/Call。
- 两腿网格均独立于动量；现有网格周期过少，不据此升级参数。
- 统一入口为`ic_im_mainline_v1_2.py`，状态为研究候选、未批准实盘，不定义IC与IM之间的资本配比。

完整研究过程、收益/回撤、数据边界和否证结果见[滚IC滚IM叠加动量完整研究记录](2026-08-23_滚IC滚IM叠加动量完整研究记录.md)，候选规则见[IC/IM v1.2规格](docs/ic_im_mainline_v1_2_spec.md)。

### POE 1.2 研究入口

- 长期入口为持久化Server Bot：`poe_ic_im_v1_2_server.py` + `poe_ic_im_v1_2_state.py`，容器入口为`Dockerfile.poe-v1.2`，当前构建号为`v1.2-20260826-r17`；头像素材为`assets/poe_ic_im_v1_2_avatar.png`。
- 官方推荐的Modal一键部署入口为`modal_poe_ic_im_v1_2.py`；持久Volume与工作日收盘多次重试已内置，部署、巡检和故障处置见[运维手册](docs/poe_ic_im_v1_2_operator_runbook.md)。
- `poe_ic_im_mainline_v1_2_bot.py`仍可作单文件本地/代码编辑器测试，但无状态复制版不会跨进程保存审计账本，不再作为长期Poe部署方式。
- 历史绩效查询使用1.2重组曲线，并以真实行情续接；2026-08-21月换日已经按旧腿盯市、新腿收盘接续及交易成本完成逐腿核验。
- 2026-08-24仅是首次启动检查点；此后每个收盘日自动把IC/IM全部腿整体推进，写入持久卷上的原子`latest.json`和前序SHA-256日志。重启、新会话和次日查询均从最新核验状态续接，不需要修改源码日期。
- r17修复漏跑后的逐日重放：补账显式读取目标交易日的指数历史收盘、中金所官方日行情和既有510500 Put历史收盘，禁止把查询日实时价带回前一交易日；一次定时任务最多顺序补4个交易日。
- 托管必须挂载持久`/data`并以密钥方式设置`POE_ACCESS_KEY`；`/healthz`提供核验日期、序号和摘要前缀。任一品种缺源、账本损坏、跳日或并发冲突都停止写入，不产生半日状态。
- 本入口仍是研究候选，不替代V2冻结主线，也不构成自动或人工下单建议。实现及数据闸门见[POE 1.2设计说明](docs/poe_ic_im_mainline_v1_2_design.md)。

### GitHub / Gmail 1.2 盘中实时日报

- 不再依赖Poe的日常投递入口由独立自动化仓库`liuruojiang/codex-daily-automation-probe`承载；与微盘股相同，在北京时间13:03、13:18、13:33设置三次计划触发。近期GitHub延迟下，实际邮件通常约14:20–14:40送出；成功一次后由交付标记阻止重复邮件。
- 策略侧入口为`run_ic_im_v1_2_github_digest.py --mode realtime`。它从最近成功的GitHub账本工件恢复状态，先按交易日顺序补到上一已完成交易日，再用当日实时/延时行情生成盘中预估JSON和Markdown。
- 成功账本以`ic-im-v1-2-ledger`工件保存90天。盘中快照必须同时得到IC/IM完整信号、行情日期为当天且处于连续交易时段；快照不得写入收盘账本。前两次失败只等待重试，最后一次仍失败才发送异常邮件。
- 策略仓库与自动化仓库均为公开仓库，策略检出无需额外访问令牌；Gmail继续复用自动化仓库中受保护的现有`MAIL_*` GitHub Secrets。部署与巡检见[GitHub/Gmail运维手册](docs/ic_im_v1_2_github_gmail_runbook.md)。
- 邮件是盘中研究预估，必须等待T日收盘确认；不是账户持仓，不自动下单，也不改变冻结V2主线。手工`close_confirmed`仅用于明确的收盘纠正版。
