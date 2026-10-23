# 持久化资格与共享锚点锁修复

本记录只覆盖当前工作包；代码修复前已有根目录 baseline.json 和 11 文件备份。所有状态测试均使用独立临时目录，未修改生产账本或旧冻结证据。

## 改动

- bot 的 `STRATEGY_QUERY_LOCK` 是唯一线程级策略锁；协调器、公开 builder（由日历工作包接入）、`install_runtime_anchors` 和开盘状态投影入口共用同一对象。保留原 StateStore 文件锁，线程锁不替代跨进程串行化。
- 安装前完整校验 IC `post_put_qty`、核心和动量 Delta 及现有公共数量字段，拒绝 bool、非数值、非有限值及非法负值；IC Delta 超过 1 也拒绝。缺失或不合法字段抛 ValueError。所有产品先准备后一次更新，失败不部分安装。
- 新增 `state.require_persistence_eligible`。新写入入口独立拒绝临时日历、纯诊断、未完成、无持久化资格和 `HOLD_FAIL_CLOSED` 的结果；标记若存在必须是 bool。省略新字段的兼容历史信号仍可按原合同验证。
- coordinator 在写账前独立检查资格；采集回调继续返回盘中结构化结果。资格不满足或真正 append 失败时，保留已经算出的文本和 observed，并显示准确原因。真正持久化异常记录 `last_refresh_error`；catch_up 仍要求实际账本恰好推进一天，不能把显示成功当补账成功。
- 漏过卖 Put 或三倍开盘原执行日时，诊断包含具体原日期和 `historical_replay`，不改计划和执行日。

## 验证

新增 `test_icim_expert_state_20261021.py` 34 项，覆盖 coordinator 与直接 StateStore 入口、非布尔标记绕过、有效双品种提交、历史读取兼容、盘中捕获、append 故障、catch_up 未推进、IC 必填字段与原子安装、直接锚点入口共享锁。

- `targeted_01`：新增文件 + 旧 integration guards，81 PASS。
- `regression_01`：新增文件 + 并发入口、状态对抗和旧 IM 执行回归，85 PASS。
- `final_01`：最终调整后新增文件 + 并发入口和 integration guards，98 PASS，1 个现有 Pydantic 弃用警告。

本工作包未重跑全仓累计测试；根代理会在各工作包集成后另作回归与独立审计。公开 builder 的具体诊断降级语义由日历工作包和根代理整体验证。
