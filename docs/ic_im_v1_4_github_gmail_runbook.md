# IC/IM 1.4 GitHub/Gmail 与本地 Codex 日报运维手册

## 2026-10-23 多轮修复发布覆盖

本节覆盖下方历史执行 pin 与旧专项清单；策略 BUILD/RULE/DELIVERY 的历史身份、参数、Call 边界与历史已发日报不改。实际运行固定采用当次远端 main 的生产工作流策略 SHA，并核对 `docs/ic_im_daily_release_20261023_manifest.json` 的 **29 个执行文件**。只读采集器的 28 文件 pin 不包含日报 runner，不能代替日报完整执行清单。旧 SHA 只作对应发布的历史记录，不能因 BUILD_ID 仍为 fix11 而认作最新补丁。

本地任务现有最新覆盖条款已要求读取本手册、登记表最新节和当次工作流 pin；保持 ACTIVE、工作日 14:30、模型/项目/cwd 与微盘流程不变。IC/IM 日报门禁改用 `python -B -X utf8 run_ic_im_v1_4_delivery_tests.py --out-dir <本次全新隔离目录>`：原 38 文件加 5 文件共 43，60 秒子进程硬限，测试状态/TEMP 位于输出目录，禁止指向正式 runtime。原已审核 22 项单列清单保持原字节；这 43 文件的交集为 5 项，不得添加新的排除节点。必须核对 XML、实际执行清单、退出状态和执行前后源码/fixture SHA；不能拿旧的 981 计数代替当次门禁结果。

根程序与独立完整 checkout 的当次门禁均已实测 981 项通过、5 项单列；完整 3121 项是此前多轮修复验收，不冒充每天均执行。新原日证据模块、adapter、身份模块和全部实际导入依赖必须随发布；诊断、不完整/未封存、临时日历或网格未确认信号不得推进账本。原日包资格先核 revision、必需角色和同日收盘时钟，再选择最新有效包；坏新包不能遮挡旧好包。采集 source pin 在发布完整包之前校验，失败只留不完整包。

远端正常 20:00、intent/receipt/marker 去重及本地不另起 SMTP 保持。无发送预检走既有 readiness request，不提交 DATA、正式账本或发送标记；未来 20:00 是否真实投递必须另按对应 run 和收件证据验收。2027 本地官方日历仍未补齐，不以估算日期作正式目标。真实原日证据和两端账本合法分支分别保留，不因同步代码覆盖或回写历史。

## 2026-10-15 对抗修复覆盖

恐慌实际采集完成、页面更新、信号生成、账本写入和报告完成必须保留并通过独立真实时钟门禁；盘中采集完成严格早于15:00，当日值与沿用值一致。回放计划不能删除身份规避校验，CFFEX整帧成交量/持仓量必须是整数张数。云端正式账本晋级和三处发送写入均要求完整成功分类；仅续账继续禁止发送。原33文件专项及60秒隔离硬限不变。新执行修复与证据见 `ic_im_fear_delivery_adversarial_20261015.md`，此前执行pin均为历史记录，fix11和已批准T/T-1输入revision不变。

## 2026-10-15 收盘恐慌口径覆盖

用户已明确批准“收盘也沿用上一交易日，并明确标注”。合格T值优先；T未发布或仅有未确认盘中点时，仅用正式日历严格T-1的合格值，保留真实数据日及来源时点；T-1也缺失则关闭。此条覆盖下方旧收盘必须同日恐慌值的限制，其他行情/状态/哈希/执行及发送门禁不改。凌晨补录单列 `retrospective_replay`、实际完成/计划创建时间和参考差异。详见 `ic_im_close_fear_previous_session_20261015_spec.md`。

## 2026-10-14 盘中恐慌口径覆盖

用户已授权盘中优先当日合格恐慌值；当日未发布时沿用正式日历上一 session 的合格值，报告保留真实数据日与 `intraday_previous_session` 状态。收盘确认仍要求原有同日来源及时点证据，不采用盘中沿用值。详见 `ic_im_intraday_fear_previous_session_20261014_spec.md`；本条覆盖旧手册关于盘中恐慌必须同日的表述，其他日期、来源、状态、15:00截止及发送门禁不变。

状态：正式研究信号；只发布策略参考路径，不接交易接口，不代表账户实际成交、行权、结算、交割或持仓。

2026-10-13当前执行提交为 `427cf45fd78710583c8028f7eca1ed88968036ee`，包含本次五处脚本修复；必须同时分发 `ic_im_quote_snapshot_guard.py`。本地任务与云端工作流使用该提交或经逐文件哈希证明执行代码相同的后续正式提交。后文10/09、10/12的pin均为历史发布记录，当前发布与投递证据见[同步记录](ic_im_v1_4_script_delivery_sync_20261013.md)。恢复期保险研究仍不进入生产。

## 正式入口

- 2026-10-08起的正式研究信号构建为`v1.4-20261008-r1-ic-csi500-abs40-fix11`，规则为`ic_im_v1_4_ic_csi500_ma105_w16_abs40_static_20261008_v1`，承接同日恐慌网格。2026-09-29至09-30信号保留fix9；以`notes/mainline_registry.md`逐信号日核验生产者身份。
- 生产者身份只前向生效：2026-09-18至09-23保留fix3，09-24起承接fix4，09-29起按fix9及其前向规则；不得重写旧账本生产者身份。
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

本地Codex工作日14:15任务与GitHub Gmail分别验收：本地输出不等于邮件送达，不另行SMTP补发。两者必须显示相同信号日对应的1.4构建、策略revision、信号边界和到期条件分支。程序最新构建与历史信号身份分开：例如2026-10-01至10-07休市时最近已完成日为2026-09-30，不把该历史日报改成10月8日fix11。

2026-10-09用户批准的CFFEX传输/失败诊断修复已由策略PR #41合并，当前正式执行提交为`408c9badea5351ae7c0021f9b1e9adfdb804d554`。云端每日工作流应固定读取该提交；本地可使用其后只改文档的提交，执行文件必须一致。fix11构建、规则与信号日边界不变，旧`739071a201506dd869b9a156e490c3bafd95a334`仅为历史发布记录。修复及回归见`docs/ic_im_v1_4_cffex_transport_release_20261009.md`；10/07历史发送准备证据保留在`docs/ic_im_v1_4_delivery_sync_20261007.md`。

云端PR #137/#138均已合并，10/09同步时main为`2dc8b2c6394b926277ac85a428140040f5769669`，三组远端CI零跳过。真实10/09收盘无发送readiness为run37911491710，隔离seq11；Put固定表按同快照仅重打包验收。今日正常20:00邮件与实际INBOX仍须分开核验；完整证据及边界见`docs/ic_im_v1_4_delivery_sync_20261009.md`。

手动工作流`ic-im-v1-4-delivery-readiness.yml`用于无发送预检：从正式工作流解析策略pin，恢复最近成功的正式账本到隔离目录，经迁移/全链/日期验证，运行官方runner及正常日报打包器，再做TLS、SMTP认证与MAIL/RCPT/RSET检查。它不提交DATA，不上传正式账本、发送intent或已交付marker。预检通过只证明当次生成与传输准备，未来交易日行情和实际邮件投递仍分别验收。

## 本地累计离线专项与已知边界

同日后续 Put 摘要修复的当前正式执行代码提交为 `5c80544bb3ffd21a1bddb31e751d567b459927a3`，包含 Tencent 修复，覆盖下面 `3b692774` 的当前 pin 表述。独立生命周期与普通保护核心 Put 的合约及数量分列；24 文件 416 项、零失败/错误/跳过、12.152 秒。保存的当日 result.json 只重生成报告，canonical seq12 不变，不重复 producer 或发送。详见 `docs/ic_im_v1_4_put_summary_repair_20261012.md`。

2026-10-12 腾讯历史备用源修复已由策略 PR #43 合并。当前正式执行代码提交为 `3b6927743850f76443f20734c2c3835da5473ba7`，本地可用执行文件相同的后续正式提交；此段覆盖此前 408c9b 的当前 pin 表述。fix11 身份和历史链不变，详见 `docs/ic_im_v1_4_tencent_history_repair_20261012.md`。累计专项增加 `test_tencent_ohlcv_pagination_20261012.py`，现为 24 文件；本次实测 415 项、零失败/错误/跳过、13.494 秒，不固定将来测试数量。

本地 `ic-im` 提示词的 IC/IM 专项统一收集以下 33 文件，相同文件只运行一次。使用 `python -B -X utf8 -m pytest -q -p no:cacheprovider`，由测试子进程施加 60 秒硬超时。只在子进程环境副本中将 `ICIM_STATE_DIR` 指向新建临时目录并设置 `ICIM_REQUIRE_MIGRATION=0`；正式 runner 仍使用原状态路径和 `ICIM_REQUIRE_MIGRATION=1`。缺文件、失败或超时只阻塞本策略，保存实际日志；不能用部分清单代替通过。`test_poe_ic_im_v1_4_state.py`会导入`test_ic_im_v1_4_state_guards.py`，后者须随checkout保留，但无需在本地重复列出。

```text
test_delivery_transport_retry.py
test_ohlcv_provider_validation.py
test_ic_im_v1_4_policy.py
test_poe_ic_im_v1_4_state.py
test_migrate_ic_im_v1_3_r7_to_v1_4_r1_state.py
test_ic_im_v1_4_integration_guards.py
test_run_ic_im_v1_4_github_digest.py
test_adversarial_icim_delivery.py
test_im_put_policy.py
test_adversarial_im_put_execution_20260908.py
test_adversarial_im_ledger_20260908.py
test_adversarial_im_report_iv_20260908.py
test_im_put_price_basis.py
test_live_mo_put_midpoint_20260908.py
test_grid_half_release_20260913.py
test_im_grid160_half_release_20260914.py
test_ic_im_v1_4_ic_csi500_forward.py
test_adversarial_market_20261007.py
test_adversarial_state_20261007.py
test_adversarial_delivery_20261007.py
test_adversarial_policy_20261007.py
test_adversarial_cffex_transport_20261009.py
test_adversarial_cffex_close_runner_20261009.py
test_tencent_ohlcv_pagination_20261012.py
test_ic_im_v1_4_repeat_roll_20260925.py
test_ic_im_v1_4_market_maker_quotes_20260925.py
test_ic_im_fear_grid_v1.py
test_ic_im_daily_valuation.py
test_ic_im_chinabond.py
test_ic_im_quarter_roll_v1_3.py
test_ic_im_ordinary_put_route_fix_20261013.py
test_ic_im_quote_snapshot_guard_20261013.py
test_ic_im_split_put_summary_20261013.py
```

10/09修复后实际为400项全通过，零失败/错误/跳过，外层11.319秒；此数是该次收集结果，不作为将来固定门槛。10/07的369项对应原21文件，233项仅对应13文件，不能冒称当前完整累计专项。

10/07记录的云端打包器独立交易日历、数量/待执行计划两类P2在10/09远端同步中补修并独立验收，最新发布和readiness结果见`docs/ic_im_v1_4_delivery_sync_20261009.md`；原始反例及历史结论保留。生产流程仍须保留正式账本续接和收盘产物校验，不得仅以打包成功判定发送准备完成。历史审计 `verify.py` 绑定恢复前seq8且覆盖旧输出，不在当前canonical工作区直接重跑。

## 行情源与开盘纸面价格

- 指数盘中价代码顺序为腾讯优先、东财`f86`时间戳回退。2026-09-30 14:10左右的**本地诊断**中，IC/IM均由腾讯返回当日时间戳，东财指数接口返回HTTP 502；当时腾讯是本地唯一通过核验的盘中指数源，不推断云端或未来也如此。腾讯失败且东财仍不可用时，指数盘中信号失败关闭，不使用无时间戳价格。
- IC/IM期货按中金所官方HTTPS、新浪、东财依次尝试；MO完整期权链按中金所官方HTTPS、东财回退。东财回退下，选中MO合约的最新价由新浪详报价交叉核验；涉及普通买Put与核心Put三倍兑现的纸面开盘价，还必须由新浪对旧/新指定合约逐一核对同日正价。新浪整月T型表缺少可用于本系统完整链准入的逐合约时间、成交量和开盘价，不能冒充第三条完整MO链。来源缺失或冲突时失败关闭。
- 期权行情的`openprice`/`o`作为供应商当日开盘字段，供研究模型使用。按中金所开盘价定义，集合竞价未形成成交价时取其后首笔成交价；低流动性合约首笔可能晚于上午。它不保证是09:30价格或账户可成交价，更不是账户成交回执。

## 回滚

代码回滚只切回上一已验证提交；账本不得降级或覆盖。若v1.4运行失败，保留最近成功v1.4工件和失败审计，不自动恢复发布v1.3日报，也不把旧信号标成当天结果。
