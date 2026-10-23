# A3独立集成与证据审计

2026-10-21，北京时间。只读审计生产源码及既有证据；实际StateStore用例均使用测试进程的临时目录，没有打开、初始化或写入正式StateStore。

## 发现

**P2，policy接口的已消费A型候选未重复检查。** `apply_policy`对输入signal与anchor执行A型门禁，但没有对最终形成的目标/待执行计划重复检查。手工prepared候选可以生成三个不受支持的A型计划：新开卖Put、衰减滚动卖Put、三倍兑现后重新入场。

范围：这是直接policy消费接口的防御缺口。正常builder仍筛选标准M；独立StateStore拒绝A身份。没有证明上述A型计划能绕过全部正式信号/写账边界，更没有观察账户仓位污染。附带`engine_supported=false`的诊断预览不会自动修复这一消费缺口。

建议对最终out再次执行既有`require_supported_ic_contracts`。未被实际消费的A型candidate诊断字典不应阻断独立到期清零；IC-only门禁不应影响IM的合法候选。

## 首次测试与保护

- `initial_1/`：29通过、1失败，首次保存ENTER_SHORT_PUT反例。
- `expanded_1/`：32通过、3失败，新增ROLL_SHORT_PUT及三倍兑现新计划反例；标准IC、IM和未消费A诊断不影响已核验生命周期的正例通过。
- 上述初次运行未逐项取得生产源码运行前后绑定；`observed_after_initial_runs.json`如实记录采样时字节。其他审计者/主线程已开始修复共用模块，不能把该采样称为全部初次运行的静态源码证明。最终独立复验必须等待主线程冻结并另存源码前后绑定。

## 已独立验证

1. 既有`final_verified/receipt.json`的1412项与XML实际用例、汇总和每份工件哈希一致，3个明确单列项与runner命令相符。
2. 旧885份绑定对本轮审计开始备份核对，不要求新修复后的源码仍等于旧版本。
3. 既有6795份outputs、465份此前审计证据哈希保持；旧两个历史能力探针原断言和文件保留，明确单列，没有冒称已补齐历史主表。
4. 迁移预览不论附带true/false权限标识，均不能放开已知A字段；builder、policy输入、新信号校验及临时实际StateStore均拒绝A，临时账本字节不变。
5. 新模块和原身份模块的依赖哈希在旧最终源码绑定中；发布/完整A支持/全历史覆盖均明确为false，没有把未交付旧pin当作含新模块。
6. 官方主表的canonical semantic hash与原始文件SHA不是同一个摘要：JSON键顺序变化不改变前者；真实原始字节哈希另外保留。适配器返回的是前者，不把它误证成原始文件SHA。

## 最终独立复验

主线程在`apply_policy`最终out形成和`validate_extension`之后补充IC-only的`require_supported_ic_contracts`。新开、滚动和三倍重新入场的三个已消费A计划均被直接拒绝；标准IC、IM、未消费A诊断字典下的合法独立生命周期继续通过。

`final_1/`独立复验 **36项通过，0失败/错误/跳过，5.22秒**。生产bot/server/policy/migration/identity/execution/state、独立测试及所用helper/runner共13份文件运行前后哈希一致，严格XML与完整receipt门禁复核通过，详见`final_1/receipt.json`、`final_1/independent_receipt.json`。1条既有Pydantic弃用警告保留。

另补`store_reachability_1/`实际临时StateStore贯通证据：从完整schema持仓消费prepared A候选，到append接口仍被拒绝，journal/latest字节保持。这项与此前4个边界直接门禁测试一起，支持“policy防御缺口而非已证正式仓位污染”的范围判断。

累计`accepted_audit/`经本组再次独立`validate_receipt`核对，**1544项通过，0失败/错误/跳过**；其source_after与本组13份独立绑定及当前文件全部相同。核对时间及两份receipt SHA保存在`cumulative_crosscheck.json`。本组36项已包含于1544中，不另行相加。

本组签字限上述最终字节；不替代A型完整生产接线、全部历史身份、真实CFFEX源或远端交付验收。没有生产写账、部署或发送。
