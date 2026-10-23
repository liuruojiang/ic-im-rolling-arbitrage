# 第三批再次对抗审计：日历消费与目标语法

本组只写新测试和本轮证据，未修改生产、旧测试、冻结规范/收益或正式账本。最终独立阶段 calendar/final_03：**24项通过、0失败/错误/跳过，2.32秒；902份绑定运行前后相同，strict receipt核验通过**。已与真正最终累计 accepted_adversarial_2 的902份共同源码逐项同字节对齐；中间阶段均保留且不冒称最终累计。

## 已证缺陷与修复

P2，prepared-signal持久边界。require_signal_calendar 原来只用ASCII[0-9]对整个合约字符串fullmatch，不能匹配时默默跳过；IM下游目标/计划验证却接受Unicode\d或小数行权价。四类被实际消费的未知年目标因此绕过2027门禁：

- MO２７０３-P-8200（全角年份）；
- MO٢٧٠٣-P-8200（阿拉伯数字年份）；
- MO2703-P-８２００（全角行权价）；
- MO2703-P-8200.0（ASCII小数行权价）。

首轮 calendar/initial_01 为8失败/16通过，before==after。每类各有低层calendar拒绝断言和完整StateStore append拒绝断言失败。ASCII MO2703正确被拒；同样schema的MO2612真实append、重启读取正例通过。因此不是失效夹具掩盖的失败。

完整反例使用已有确认schema构造合成研究record、seed._signal生成动量新目标及ordinary计划，再调用未经mock的StateStore.append_confirmed_signals。四例均在隔离临时目录把seq0推进到1，将未知2027合约写入v14_ordinary_put_pending的动量新腿；latest及两份journal已复制initial_append_evidence，原测试源码在snapshots/initial_01.py。已持久化的是下一交易日开盘研究计划及信号目标，不能称为实际成交或券商仓位。没有证明正常行情provider会生成这些字符。

根修复实际消费目标：语法不识别必须明确拒绝，不能fullmatch失败即忽略；未知年份仍要求官方覆盖。未采用的candidate_diagnostics和next_core远月继续不触发门禁。旧冻结历史读取不回写。根同时按state组证据增加已过期新目标的执行日比较，本组不将该项冒称自己的首次发现。

## 24项覆盖与独立性

| 对照 | 数量 | 结果 |
| --- | ---: | --- |
| 5种未知年目标语法的calendar拒绝 | 5 | 全部拒绝 |
| 5种目标的真实临时StateStore append拒绝、head/journal不变 | 5 | 全部拒绝 |
| 已覆盖2612目标append、重启读取完整正例 | 1 | 通过 |
| 5种未采用远月诊断不阻断已覆盖目标 | 5 | 通过 |
| 2026元旦、春节、端午、国庆已载入官方休市顺延 | 4 | 通过 |
| 真实假期及开市当日盘中/收盘后的已完成日因果 | 4 | 通过 |

calendar/repaired_02 原样24项已通过（1.85秒），但当时state还有后续修复，未作为最终字节验收。final_01曾在调度循环最后修复前以3.60秒通过相同原24项，随后按最后冻结重新执行final_02。测试源码SHA与initial_01快照相同，未松动断言、未新增排除、未修改生效日期。

## 未报为漏洞的边界

低层_validate_market_stamp单独不判断同年休市，但真实builder仍通过_validate_signal_market_date和next_unverified_session桥接检查拒绝假日新行情；不能把低层宽松直接宣称为正式假日写账。_third_friday等诊断推算仍可展示无关远月，真正采用者必须另核官方覆盖，不能以所有远月统一拒绝替代消费者审计。

与state、scope组交叉核对保持：普通计划T+1开盘、月维护当天收盘、profit计划指定执行日不变；关闭旧过期腿不会被新目标到期检查误拦；不引入等待ETF挂牌或不少于3个月的新研究规则。IC无Call、IM不新卖Call及现有数量、保证金口径均保持。

## 最后调度修复后的独立复验

根在并发组最后反例后修改server刷新循环的前置异常兜底，保持任务重试及取消语义。calendar/final_01 原证据保留，不冒称其绑定新server。按统一冻结通知重新执行相同24项：**calendar/final_02，24通过、0失败/错误/跳过，1.93秒，902份绑定前后及当前一致**。测试源码仍与initial_01快照相同。最终共同字节对齐采用final_02，待根累计有效收据。

## 累计首轮未通过后的范围复核

根首轮累计存在45个旧低层producer/state用例被额外的期货过期比较提前遮挡；这不是本组已证MO目标语法反例。根移除未经本组证明的core_target期货过期比较，保留其原语法/年度覆盖；实际新Put正目标、ordinary新腿、profit执行日的expiry检查和本组语法拒绝不变。该改动回到本轮已证修复范围，不是放开未知年Put门禁。原累计失败保持，不靠新增排除通过。

按最后bot字节重跑 **calendar/final_03：24通过，0失败/错误/跳过，2.32秒，902份绑定前后及当前相同**。本组测试仍逐字节等于首败快照，未改断言或已测规则。后续最终累计对齐以final_03为准；final_01/02及其收据不覆盖。

## 最终累计对齐

有效累计为 **accepted_adversarial_2：1767通过、0失败/错误/跳过，3项既有单列，906份源码绑定，71.44秒**。本组final_03的902份映射均被累计清单收录，逐项完全相等，当前源码也相同；两份strict receipt再次验证通过。本组24项包含在1767中，不重复相加。新证明为final_03/accepted_adversarial_2_reconciliation.json，绑定独立与累计receipt哈希、每一共同源码及当前核对。首轮accepted_adversarial的45失败阶段不采纳，旧收据/失败记录不改写。
