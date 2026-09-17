# IC / IM v1.4-r1 多智能体对抗审查

日期：2026-09-17。审查结论：**未通过实现完整性验收；存在5项P1和8项P2。建议先修复并复验，再进行外部发布或将新增流程视作完整可用。**

本结论针对程序是否实现已经批准的规则，不撤销用户已接受的IC回撤与IM样本集中度取舍，也不改变正式登记表。当前工作是审查，没有修复、发布、发送或订单操作。

## 审查对象与证据范围

- 对象：`v1.4-20260917-r1-coreput3x-fixedshort95`，schema 4、strategy 1.4、revision r1，生效信号日2026-09-18。
- 实际文件：冻结规格、主线登记、发布记录、policy、bot、state、server、digest、迁移脚本及相关测试；追踪了继承的v1.3调用逻辑。
- 三个独立智能体分别审查策略状态机、账本迁移、正式调用链；首次因额度中断，用户要求继续后成功恢复并完成。主审重跑各分支关键复现并合并去重。
- Git HEAD：`9ebc6f2c227035e0f05e81305782e642d21e1ac5`。v1.4文件在工作区尚未跟踪，不能仅用HEAD标识本次受检实现；逐文件SHA-256保存于`root_probe_results.json`。
- 真实本地v1.4账本：2026-09-16、sequence 0、digest `d512f89c670ba5b9b10226bda01c27f95ddc52bd7460f900c39ef343a0c74b19`。完整链只读校验通过。
- 合成边界输入用于调用真实代码，临时账本用于故障注入；不得解释为9月18日真实行情、成交或收益。没有新的全历史绩效重算，也没有账户执行验收。

## 高优先级发现（P1）

### F01：三倍兑现触发后，正式链缺少兑现重建实现

位置：`poe_ic_im_mainline_v1_4_bot.py:4456`、`ic_im_v1_4_policy.py:275`。

正式adapter只提供当前mark和月度target premium，未生产`v14_profit_reentry_entry_premium`，也没有与该事件对应的新选约、兑现重建确认路径。隔离调用真实adapter，首日为`CORE_PUT_PROFIT3X_REENTER`，次日为`WAIT_CORE_PUT_PROFIT3X_REENTER`。后续普通月维护可能清除pending，但这不等价于按T+1收盘实现三倍兑现。

修复验收：接通带合约、执行日、价格与事件ID的重建证据；从正式信号入口连续跑触发日、T+1执行日及重启后下一日，验证只重建一次且动量Put保持独立。

### F02：卖Put到期、恢复及整轮回本没有正式数据生产者

位置：`ic_im_v1_4_policy.py:184`、`:249`；`poe_ic_im_mainline_v1_4_bot.py:4429`。

结算确认/结果、恢复期货确认、含成本周期净损益等字段只在策略中读取、单元测试中注入，正式adapter没有提供。到期隔离复现返回`WAIT_SHORT_PUT_SETTLEMENT`；不能自动区分价外失效与被行权，不能完成ETF转换/IM恢复和含成本回本退出。等待未知结算是正确的保护，但不能据此宣称完整联合流程已实现。

修复验收：使用可审计结算与价格来源，覆盖价外失效、被行权、缺结算等待、恢复持仓、亏损继续持有、含成本回本退出及重启续接。不得用假定被行权或手改账本填补闭环。

### F03：恢复期已有0.5倍期货被当作零，持续误报加仓

位置：`ic_im_v1_4_policy.py:305`、`:335`。

`recovery_future`也进入`route != future`分支，将当前核心改为0、总仓减0.5；目标却保留恢复期货。IC/IM均复现：输入当前/目标总仓1.0、恢复净损益为负，输出`HOLD_RECOVERY`同时`total_units_change=+0.5`。这会错误表达需要新增仓位；本次没有实际下单。

修复验收：恢复期已有期货应计入当前持仓；连续恢复日变化为0，真正由现金/ETF进入恢复期货时才增加0.5。

### F04：IC新Put的三倍成本基准错误引用旧合约价格

位置：`poe_ic_im_mainline_v1_4_bot.py:4413`、`:4724`、`:4937`；`ic_im_v1_4_policy.py:293`。

`put_quote`对应锚点的旧持仓合约；`iv_monitor_option_price`保存旧合约last。月维护时`_v14_option_mark`直接将此mark用作新入场价格，并开启三倍资格。新旧合约价格不同时，之后三倍阈值被污染。主审通过真实函数复现“旧价0.08被返回为新entry”，并沿实际赋值链确认其来源。

修复验收：entry绑定新实际执行合约、价格、执行日期与数量；目标无仓或新价缺失时不得启用资格。用新旧不同价格的重置样本验证。

### F05：IC卖Put候选接受陈旧报价，历史补账还会调用当前链

位置：`poe_ic_im_mainline_v1_4_bot.py:4328`、`:4358`。

新增候选函数把报价时间写入返回值，却未校验其与信号日一致，也不分历史重放。独立复现：信号日9月18日、链报价9月1日，真实IV函数计算约45%，仍得到`tradable=true`和`ENTER_SHORT_PUT`。重放时该路径调用实时上交所链，存在混入未来报价的风险；后者是源代码推断，本次未执行真实历史补账。

修复验收：候选、标的和估值时点一致，日内还需检查时间有效性；历史补账必须使用对应历史链，无法取得时明确阻断该日相关准入。复测陈旧、未来和跨日链。

## 中优先级发现（P2）

| ID | 问题与已核验证据 | 位置 | 修复验收重点 |
|---|---|---|---|
| F06 | 路由关闭核心Put数量后仍保留旧`HOLD`动作。IM当前0.5→目标0，但`core_put_action/put_action`均HOLD；研究指令内部矛盾。 | policy:319；bot:5351 | 同步动作、合约、数量与T+1开盘时点；动量Put不受影响。 |
| F07 | 已有3x pending未服从月维护优先。注入重建价12/月度价20时仍报执行3x，最后成本却写20；未给重建价时也可能报等待但pending已被月维护清除。 | policy:275–296 | 月维护统一优先，不重复兑现，不产生动作/成本矛盾。 |
| F08 | 3x分支未强制收盘和T+1时点。`close_confirmed=false`仍形成pending；同触发日给价格可直接执行。账本另有收盘拒绝，因此不声称盘中已写账本。 | policy:265–289 | 触发日、执行日、收盘确认三者验证；盘中只展示未确认预警。 |
| F09 | IC/IM NaN动量均通过卖方门控；NaN入场权利金也通过纯策略状态验证。未证明NaN能通过完整正式写账本。 | policy:89、125–136 | 有限值、正价、离散档位及严格bool验证。 |
| F10 | 新写入不检查1.4生产器身份。具扩展字段的1.3/r7、obsolete build/rule可写入schema4外壳。生效后边界复验也接受。 | state:402、613 | 新写入与崩溃恢复验证version/revision/build/rule；保留旧历史只读兼容。 |
| F11 | IC short身份与互斥校验缺失。无效合约、无expiry/cycle/event且保留核心买Put Delta 0.25的临时记录可append/reload。生效后交付边界亦接受。 | policy:89；state:457–499 | 校验真实合约身份、到期日、周期及事件；short/恢复状态核心买Put归零。 |
| F12 | 迁移丢失IC双袖精确张数。旧signals含7+7张且可恢复，迁移清空signals后返回成功，新锚点恢复失败。真实当前12+0未触发。 | migrate:51–66；state:357–369 | 将验证过的分袖数量写入创世产品状态，并验证迁移后可安装锚点。 |
| F13 | 1.4日报标题、成功和失败结果strategy仍写1.3；参数页面还保留旧网格1倍/旧IC阈值表述，和当前0.5倍规则不一致。 | digest:174、298、342；bot:5838–5874 | 报告、参数、成功/失败身份统一，测试用户可见内容。 |

表中简称：policy=`ic_im_v1_4_policy.py`；state=`poe_ic_im_v1_4_state.py`；bot=`poe_ic_im_mainline_v1_4_bot.py`；digest=`run_ic_im_v1_4_github_digest.py`；migrate=`migrate_ic_im_v1_3_r7_to_v1_4_r1_state.py`。行号以本次SHA快照为准。

## 测试与完整性

- 本次回归：**148 passed，1个第三方弃用警告**。其中v1.4策略8项、迁移2项；其余138项为已有v1.3/季度换仓/状态/日报测试。不能把旧模块测试数量当作1.4正式调用链覆盖。
- 主审8个异常探针全部复现；策略分支8个边界输入全部复现；调用链和账本分支另有故障注入。多个探针重叠，不能累加当作独立缺陷数量；本报告按13项归并。
- 真实账本哈希链通过，存量Put三倍资格未被虚构启用，IC当前Call为0；严格IV阈值等号不准入、生效前沿用旧规则均通过。
- 同日持旧digest重复append和未重新签名的hash篡改被正确拒绝。
- 冻结规格SHA匹配。主审快照中的24个受检代码/规格/账本文件在诊断前后完全一致，见`final_integrity.json`。保存了审查证据，没有修改生产实现、正式账本或冻结研究输出。
- latest落后于已写journal的情况与已有崩溃恢复设计重叠，仅作恢复语义观察，不列独立缺陷。

可复现命令（从仓库根目录运行）：

```powershell
python -X utf8 -m pytest -q test_ic_im_v1_4_policy.py test_migrate_ic_im_v1_3_r7_to_v1_4_r1_state.py test_poe_ic_im_mainline_v1_3_bot.py test_poe_ic_im_v1_3_state.py test_run_ic_im_v1_3_github_digest.py test_ic_im_quarter_roll_v1_3.py --disable-warnings
python -X utf8 outputs/v1_4_adversarial_audit_20260917/root_probes.py
python -X utf8 outputs/v1_4_adversarial_audit_20260917/policy/reproduce_policy.py
python -X utf8 outputs/v1_4_adversarial_audit_20260917/integration/reproduce.py
python -X utf8 outputs/v1_4_adversarial_audit_20260917/ledger/reproduce.py
python -X utf8 outputs/v1_4_adversarial_audit_20260917/ledger/reproduce_migration.py
python -X utf8 outputs/v1_4_adversarial_audit_20260917/ledger/reproduce_post_effective.py
```

## 既有研究证据复核与局限

核对两组9月17日既有`scan_meta.json`、IC `integrity_checks.csv/promotion_gates.csv`和IM `cycle_concentration.csv`，没有重算收益：

- IC真实挂牌研究窗口为2022-09-19至2026-08-14；联合方案最大回撤较基线增加约0.8709个百分点，与已接受披露一致。已有完整基线parity误差约9.97e-17、重复路由/3x日为0；这些是研究输出自报校验，本次未重跑研究引擎，不是本次正式bot的parity证明。
- IM真实研究窗口为2022-07-22至2026-08-14；3个周期、最大正向贡献占比67.7089%，与冻结规格一致。理论段必须与真实挂牌段区分。
- 上述历史结果来自独立研究状态机，不能替代正式bot的端到端验收，也没有覆盖8月14日之后的新样本。当前审查不重新择参，不把旧历史表现冒称本次1.4信号运行绩效。
- 本次没有验证真实成交容量、滑点、涨跌停可成交性、经纪商保证金或账户整数化；没有任何账户或订单授权变化。绩效30%缓冲及用户15%可行性假设仍按冻结规格保留。
- 9月18日尚未到来，没有首个正式生效日实际运行证据。后续验收须在真实日期形成，不能用改日期的边界输入代替。

## 后续修复顺序

先接通F01/F02的可审计生命周期证据，再修F03/F04/F05的仓位、成本和时点错误；同时补动作一致性、账本身份/互斥及迁移保护。完成后从正式入口在隔离账本验证完整周期、重启与重复调用，并在首个真实生效收盘做只读对照验收。无需改变已批准的策略参数即可修复这些实现缺陷。

独立报告与机器证据保存在本目录的`policy/`、`ledger/`、`integration/`；主审结果为`root_probe_results.json`，回归日志为`regression.txt`。
