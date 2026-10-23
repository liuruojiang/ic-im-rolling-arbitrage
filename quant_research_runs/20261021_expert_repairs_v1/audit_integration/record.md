# 独立集成对抗审计（进行中）

范围：只读生产源码，可新增独立测试；离线、隔离状态，无部署或正式账本写入。

发现并交根代理修复：

1. 漏日三倍兑现计划未产生 RETRY event：execution.profit_plan 只返回原执行日计划，后一天 project 无事件，require_due_openings 再抛错，整品种仍失败。实际公开 builder 复现，禁止抓晚日执行价也没有被触发。节点 test_real_overdue_profit_builder_waits_without_later_day_quotes。
2. 普通动量计划与三倍核心计划同时逾期时，early ordinary RETRY 直接返回，只披露一条原计划；锚点未丢计划，但诊断不完整。测试输入通过 policy.validate_extension；节点 test_real_multiple_due_plans_are_all_disclosed_without_projecting。
3. install_runtime_anchors 没有要求和校验后续消费的 verified_total_put_delta；缺失、None、bool、string、NaN、Inf 都能安装，留下后续 KeyError/错误 Delta 风险。另补负值、超范围和分袖总和不一致反例。

first_01：8 failed / 20 passed。其中一条多计划最初测试夹具身份不匹配，属于测试夹具问题，已经修正；不将该次夹具异常算作产品漏洞证据。
first_02：修正夹具后，两个真实调用链节点均按预期揭露未修复问题（2 failed / 26 deselected）。六个 total Delta 类型/缺失反例同样揭露真实门禁缺口。保留原回执，不覆盖。

已通过的独立反例包括：新资格标记为 None/list/dict 被持久层拒绝且磁盘不变；两个协调器在一次存储异常后准确推进而不假报成功；SSE通用时间校验拒绝空、bool、09:15前值；混合日线故障页面保留数据坏值原因，没有导出确认目标。

这些测试的执行期间其他代理仍可能修改生产代码；因此本轮初始结果是漏洞定位证据，最终验收必须在源文件固定后复跑并绑定前后哈希。尚未给出最终通过结论。

## 修复后独立复验

根代理修复后，原31项测试原样复跑：final_01，31 PASS、0 failure/error/skip、1个依赖弃用warning，2.10秒。8个源码/测试/运行器绑定在测试前后SHA-256相同。未改生产代码或原独立断言。

已确认修复：漏日3x有明确等待与原日historical_replay；ordinary与3x同时逾期时原计划完整披露且没有取晚日报价；IC总Delta缺失/类型/范围/分袖总和不符在安装前ValueError，原全局锚点不变。额外通过：资格畸形标记、双协调器存储异常恢复、SSE早时刻和混合日线页面。

限制：此项是31个针对性独立整合反例，不替代完整连续56日账本回放或真实接口联网验证。
