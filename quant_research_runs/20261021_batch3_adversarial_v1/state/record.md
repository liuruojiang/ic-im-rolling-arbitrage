# 第三批独立红队：持久边界与恢复一致性

2026-10-21，本地研究审计。本组只写新增测试与隔离证据，未修改生产或旧测试；生产修复由根代理完成，未写正式账本、部署或发送邮件。

最终独立阶段 `state/final_01`：**26项通过，0失败/错误/跳过，3.89秒**。902份源码/规范/夹具运行前后一致；严格收据校验XML用例、汇总、完整收集、零退出码和工件哈希后通过。所有首次失败、成功写入的坏临时账本副本及原测试字节保留。最终累计共同源码对齐另存，不把独立PASS当成累计已通过。

## 已证缺口及修复边界

| 项目 | 首次独立反例 | 根修复后的验证 |
| --- | --- | --- |
| 实际采用的过期多Put目标/开盘计划 | Oct21 prepared合法信号对采用MO2601或MO2609，官方年份均为2026但已到期；真实StateStore.append生成seq1，保存了Oct22买入过期合约的ordinary pending | 按实际正数量目标/新计划腿的执行日核规则到期。月维护当日目标用当前信号日，普通计划用其下一交易日；关闭旧腿不要求旧合约未来到期。不引入最少三个月或等待挂牌研究规则 |
| 旧producer完整journal恢复 | 完整hash-valid journal的signals合法，但products.IM.post_core_contract改为IM2611且重签digest；恢复重新derive后丢弃返回值，错误products被写入latest | 精确比较重新derive并_jsonable后的products；增加恢复记录sequence与目标文件序号一致性。正常完整journal保持原字节恢复；内部一致的数量/parent/equivalent bundle及next-weight漂移也拒绝 |
| 新三倍兑现计划的实际执行日 | Oct21新pending分别指定Oct23跳过实际Oct22、或Oct24周六，真实append均成功seq1 | 新写信号pending.execution须为trigger后的严格下一交易日；未改变旧历史读取 |
| 新pending倒填未承诺触发日 | 旧head无profit承诺，Oct21 prepared新信号注入Oct20 trigger→Oct21 exec；即使满足该trigger的next-day，实际append仍成功seq1 | 按新增pending或相对锚点更改的计划字段，要求trigger属于当前信号日；完全相同既有计划不当作新计划任意重写日期 |

以上为公开持久API/恢复防御反例，当前正常selector本来会筛除到期合约；没有证据表明当前正常producer已产生这些坏目标或journal。恢复反例显式模拟旧producer完整、自洽摘要的记录，不是声称普通半写文件会自行获得有效SHA，也不是绕过恶意签名的安全证明。

Unicode月份绕过作为跨组复验：本组暂停新运行期间只做静态报告，真正“Unicode/decimal成功写入2027计划”的修复前证据由calendar组独立保存；本组26项包含两Unicode实际append拒绝对照，不能将它冒称本组初始成功写入。

## 合法路径与拒绝对照

- 未改同约持仓的合法prepared IC/IM信号对可写并重启读取。
- 未到期的新核心Put目标可形成真实ordinary pending；旧过期多Put仅平仓的计划不被未来到期门禁误拦。
- Oct16月维护同日可保存合法未到期目标及当日维护记录，不顺延到次日开盘。
- 旧short Put到期时PUBLISH_SHORT_PUT_EXPIRY_BRANCHES通过新信号验证；不把旧到期分支误判成新卖Put，不恢复新Call。
- 年内周末信号日或错误next_trade_date由完整append拒绝；未知2027实际目标拒绝；未使用的未知维护诊断字段不阻断当前有效信号。
- 卖Put代码与expiry同年不同月份的身份错配原有新信号验证已拒，未把已有门禁报告成新漏洞。
- 无效信号对的两个产品原head/日志载荷保持，不以部分写入推进；正常完整journal恢复不重写旧journal。

## 迭代证据与夹具差异

| 阶段 | 结果 | 解释 |
| --- | --- | --- |
| state/initial_01 | 2失败/7通过 | 两个过期IM核心目标实际append成功；坏临时ledger副本在accepted_bad_ledgers。原9项测试快照SHA与source_before完全一致 |
| state/recovery_02 | 3失败/8通过 | 增加旧producer products与signals不一致的恢复首败；原11项测试快照SHA与source_before一致 |
| state/expanded_03 | 4失败/21通过 | 两个profit执行日期真正首败，保存于accepted_bad_profit_ledgers；另两败为我方新增夹具不完整，见下，不计生产漏洞 |
| state/profit_trigger_04 | 1失败/25通过 | 前两fixture补齐后全部转绿；剩余一败为倒填无承诺触发日真实append成功，副本accepted_uncommitted_trigger_ledger.json |
| state/final_01 | 26通过 | 根修复新计划触发日绑定后的原测试复验；未新增排除、未修改业务断言或在测试内替换生产校验器 |

expanded_03两个夹具问题分别是：数量漂移只改core qty、原总量/parent约束已拒，后补齐内部合法的total/legacy-total/parent/equivalent bundle以专门攻击跨signals一致性；月维护阳性漏了已有必需core_put_action=RESIZE_OR_ROLL，补齐合法action。旧失败XML、测试源码快照和退出码完整保留，不把它们计为新生产缺陷。root runner后来新增自动保存own-test源码，之前两阶段本组补存快照时逐字节核对原before SHA。

## 真实数据与影响界限

`state/local_real_anchor_observation.json`只读核新审计基线备份：真实本地研究head为seq18/2026-10-20，IC持IC2612和510500P2612M06500(15张)，IM持IM2612和MO2612-P-6700(1.5归一张)，没有profit/short/ordinary pending。这不是券商账户仓位证明。本组坏目标、价格/时钟、成本和恢复载荷为明确合成输入，未重算绩效或声称真实持仓已污染。

IC无Call、IM停止新卖Call、rescue_next_listed、保证金口径、已授权proxy回退及Put候选研究均未因本组修复改变。未证明的外部仓位、完整A型维护/迁移与2027官方资料仍属各自未闭环范围。

## 最终源码阶段更新：state/final_02

根代理在并发组发现唯一收盘任务前置异常会终止循环后，仅加固server刷新循环的失败重试/取消传播。按新的最终生产字节，本组原26项在`state/final_02`再次严格通过：26 PASS、0失败/错误/跳过，2.44秒，902份绑定前后及当前字节一致。`final_01`为先前阶段收据，原字节与报告前文均保留；当前采用`final_02`，最终累计共同映射仍待累计阶段实际通过后核对。本组未改测试断言或生产源码。

## 最终源码阶段更新：state/final_03

根代理报告前一次累计45项旧state/producer单测因其额外引入的期货core_target到期比较提前拒绝；该比较不是本组已证明的Put缺陷范围。根仅收回这项额外期货expiry条件，期货语法/年度覆盖、已证明的Put到期/计划时序及journal恢复检查全部保留。累计失败不计通过，不修改原业务断言消除失败。

按收窄后的bot最终字节，本组原26项在`state/final_03`再次严格通过：26 PASS、0失败/错误/跳过，2.46秒，902份绑定前后及当前字节一致。`final_01`、`final_02`收据和原报告前缀保留；目前采用`final_03`。最终累计共同映射仍只在实际成功后新增，不冒用已被拒绝的累计阶段。

## 最终累计共同字节验收

最终只采用`accepted_adversarial_2`，经本组再次独立validate_receipt严格复核为1767 PASS、0失败/错误/跳过；原3项明确单列未新增。`state/final_03`的全部902份绑定均被最终906份累计清单收录，逐项与累计及当前源码完全相等，无独立未收录项。核对证明在`state/final_03/accepted_adversarial_2_binding_match.json`，含两份收据哈希及逐项映射。

本组26项已含于1767项，不重复相加。首轮有45失败的累计阶段不采用，之前各独立阶段作为历史复验凭据保留。持久边界/恢复一致性在本组已证明范围完成修复及复验；未声称任意来源的全部prepared信号都已获得实际挂牌/成交证明，或真实仓位已受本组合成反例污染。
