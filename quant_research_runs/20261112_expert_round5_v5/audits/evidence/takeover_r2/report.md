# 第五轮最终 r2 证据独立验收

执行者：`/root/evidence_takeover`。机器回执 `summary.json`，只读验收脚本 `verify_final_evidence.py`。本角色没有重复执行完整回归或行情/绩效；直接检验根协调者已完成的原始工件。

## 最终测试证据通过

- `takeover_full_candidate_r2` 原始请求与上一全量相同：56 个唯一文件，原 5 项明确排除未增减。实际 collection 的 collected、selected、deselected、executed 无重复且集合关系一致。
- 1571 个实际 XML 用例身份逐一对应 executed 节点，汇总与节点统计均为 1571 通过，0 失败、错误、跳过。第五轮根文件实际选中 39 个持久用例；不是仅凭 pin 声明或总数推断。
- candidate_result、test_run、network_guard 三层退出码均为 0；真实非本地连接与外部尝试均为 0。213 项 source_before/source_after 与 r2 pin 完全一致，当前源码逐项复算也一致。r2 pin SHA 为 `05ae4d5a238ae619b90cef7755085a1ec55043da32bba8571ad95601944dc0b6`。
- runner 的实际 AST 仍为 `child.wait(timeout=60)`，未降低原 watchdog。原 r1/r2 两次超时与其它旧失败工件保持，旧证据不被改写为新通过。
- 本次核验前后，初始 2347 保护文件与正式 runtime 完整 38 文件均字节一致，latest 仍为 seq34 / 2026-11-11。根后续追加登记表应单列文档哈希变化，不应声称追加后仍全部2347原样。

## 真实历史证据的精确复用

对 r1 修补前备份与最终 adapter 做全 AST 比较，唯一变化位于 `_keyed.adapted` 的嵌套 `collect_listing_result`。只把该函数体规范为空后，整个模块 AST 精确一致。此函数仅在 `role == 'ic.months'`、日期不早于 2026-11-13、存在新政策能力标记、且走 capture 的分支中定义；reader 回放分支与旧日期通路没有变化。

因此 2026-10-22 的既有真实回放和独立选约证据可按该精确闭包复用，不能称为 r2 重新执行真实行情。`real_validation/verification.json` 明确注明 `new_market_computation=false`，431 个非计算时钟字段一致，原四个计算时钟值完整保留；所绑定原始信号工件均逐项复算哈希。原过严时钟断言造成的失败报告未覆盖。

`real_validation/selection.json` 的真实选约与第四轮冻结结果完全相等：510500P2612M06250 / 10012282，行权价6.25、18张、IV 0.3288257548963115。market_day 为 2026-10-22，合成行情标记为false，外部请求和未归档输入均为空，没有正式账本写入。本角色仅检查该真实选约产物与参考哈希，没有重复执行该阶段。

本目录首轮脚本将 reader_if 的 body 与 else 一起遍历，导致自身 AST 分支检查失败；已保留脚本快照和 `attempt_1_failure.txt`。修正遍历范围后退出0，只有审计脚本变化，不涉及产品代码、既有证据或测试断言。

## 交付边界

本角色确认最终 r2 测试及上述真实历史证据复用范围；总报告、差异、最终保护及 `final_verification` 由根协调者收尾。没有据此认证当日数据可用、15:00双源收盘定稿、独立CFFEX逐合约月度公告或2027交易所年度公告，也没有部署、发信、推进正式账本或交易授权。
