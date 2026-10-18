# 月度价差交付层独立对抗审计

审计基线：2026-10-18 月差发布代码；只读代码/行情/旧工件，仅本审计目录写证据。

## D1：衍生数值溢出使可选月差阻断整份日报（P2，可复现）

- 输入四个挂牌合约的有限正价，例如当月 1.0、次月 1e308、当季 6000、下季 5900，报价日期正常。月差函数仅检查两腿原始价格，未检查 ratio / annualized 的有限性。
- 实际：`monthly_spreads` 返回 `status=ok`、`annualized=-inf`，`state._jsonable` 转为 `None`；`state.validate_delivery_values` 及云端 `validate_success_payload` 均通过；本地收盘重渲染及云端纯文本/HTML渲染抛出 `TypeError`。
- 预期：异常单组显示 N/A，保留正常季度价差、另一组月差及其余日报。真实市场正常价格不会触发，需异常行情数据；不是指已实际发生生产中断。
- 最小修复：构造月差时拒绝布尔值并验证所有衍生数字有限；状态只在全部校验成功后改为 ok。消费者对异常可选字段逐组降级 N/A，防止坏历史/跨版本字段让整体日报失效。
- 证据：`evidence_before_fix.json`。真实已保存 run 38853603565 payload 基线先通过，注入上述函数实际生成的结果后，完整消费者验证通过但两个邮件渲染入口均失败；原 payload 文件 SHA 保持。
- 持久层：`StateStore._append_locked` 原样将整个信号经 `_jsonable` 存入新 hash 记录；可选字段不是账本外元数据。月差不被 anchor derivation 使用，因此不直接改变持仓逻辑。但 `status=ok` 的 null 可持久存在，收盘重渲染仍失败。未操作正式账本。

## D2：消费者不校验可选价差语义（防御缺口，未证明独立真实上游通路）

真实基线 payload 的 IC 信号放入 IM 合约、过期 source_date、与双腿不符的 999 点数后，云端成功校验及月差渲染仍接受。这是边界校验缺口，不能单独表述为生产已产生错误数字。建议共享小型展示校验器核对产品、日期、必需有限数值、双腿算术及同合约情况；旧归档无新字段时保留原显示规则。

## 独立通过项与限制

- 遍历已保存 CFFEX 历史共 3742 个产品交易日，本地月差与云端纯文本月差逐字一致、两端均不修改信号。
- 9 个源文件与本次发布 manifest 的 LF SHA-256、Git blob 全部匹配；固定 workflow 包含新策略 pin，CI 范围含新增月差测试。
- HTML 通过 escaped(reason) 输出，未发现月差绕过 HTML 转义。
- Oct09 原 payload 已独立重渲染：subject、plain、HTML 三个 SHA-256 全部与原始归档一致，见 `evidence.json` 的 `oct09_archive_independent_hashes`。旧字段缺失归档保留原分支。
- 当前 shell GitHub API 被 session proxy 拒绝，因此远端 main 实时状态需由主审计者的 connector 核验；本审计不把本地 release_receipt 当远端独立验收。

## 修复后独立复验

新云端源码读取 `../cloud/scripts`，旧快照保持原字节。见 `evidence_after_fix.json` 与 `consumer_boundary_after_fix.json`。

- IC/IM 溢出组均为 N/A；真实 payload 的完整邮件纯文本、HTML 正常生成。
- 基线已序列化的 status=ok/null 坏元数据重新带入真实 payload，收盘完整报告、纯文本邮件、HTML 三入口均成功降级，未再抛出异常。
- 14 项消费者矩阵（1 正常 + 13 异常）两端全部无异常，13 异常首组均为 N/A，正常另一组仍显示。
- 3742 个真实产品交易日继续零模板差异、输入信号不变；Oct09 三个完整 SHA-256 继续保持。
- 新修复源码相对旧 release manifest 发生预期变化，故新结果的 manifest_match=false 不作为缺陷；修复前旧 9 文件的哈希一致结论保存在基线证据。
- 修复过程一度捕获新云端脚本 line1098 的 IndentationError（局部重复 import 被置于顶格）；修正后重新完成上述验证。这是未发布草稿过程问题，不是原正式版本问题。
