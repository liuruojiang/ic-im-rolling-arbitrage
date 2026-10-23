# 展示与失败快照独立对抗审计

日期：2026-10-21。只读生产源码；新增独立测试与本目录证据。合成输入只用于故障诊断，未部署、发信、运行正式续账或改写正式账本。

已先读取 AGENTS.md、mainline_registry、v1 冻结规范及首次 record；对照本轮 source_delta.patch。未把原 989 项通过当作这些边界已验证。

## 首次失败及扩展结果

| 运行 | 结果 | 用途 |
|---|---|---|
| first.xml / first.txt / first.json | 2 失败、9 通过 | 首次复现核心当前合约错标、历史 bootstrap 身份丢失；不可覆写 |
| expanded_before.xml / .txt / .json | 4 失败、10 通过 | 增加核心缺价后的错腿有限 IV、IM 零数量残留合约 |
| chain_before.xml / .txt / .json | 4 失败、12 通过 | 增加真实 3x 开盘投影后的收盘新目标及持久层阻断验证 |

runner 自有 ROOT=`Path(__file__).resolve().parents[3]`、HERE=`display`，强断言目录，独立随机临时 state。测试不运行正式 runner，HTTP get 一律禁止。runner 拒绝复用已有标签，避免覆盖首次失败与旧 regression 证据。测试进程内 Windows mkdir 权限兼容不作用于生产代码。

## 发现与真实可达性

1. **P1：独立核心缺价后仍把动量合约显示为当前核心，并发布动量腿的有限 IV。**
   已核验 anchor：核心 5 张 `510500P2612M07500`，动量 3 张 `510500P2611M06500`。主信号已正常取得动量链；随后 `_v14_ic_core_overlay` 抓核心链发生 RuntimeError。真实 `_apply_v14_live_policy` 捕获该错误，返回 `HOLD_FAIL_CLOSED`，但 `_refresh_ic_put_text` 因无 `v14_core_put_current_contract` 回退共享 `put_current_contract`，实际输出“核心 5张 `510500P2611M06500`；动量 3张 `510500P2611M06500`”。这两步在 `_build_live_trade_signal` 末尾直接相连。

   同路径 `iv_monitor_evidence` 尚未写入；`build_iv_warning` 因 evidence=None 回退共享合约、动量的旧 `iv_monitor_option_price` 和时间，实际调用 IV 定价并输出 contract=`510500P2611M06500`、iv=0.45。0.45 是受控定价函数结果，证明有定价调用和有限值发布，不是对市场真实 IV 的断言。首轮修复声称“绑定不足 N/A、不借另一腿”未覆盖这里。

   **边界：不是已证明的交易/写账漏洞。** close_confirmed=True 的同反例不建 ordinary plan；anchor 字节对象内容不变。补齐真实 build/rule/strategy/execution 身份后，`validate_v14_new_signal` 在独立核心数量与目标不一致门禁拒绝。盘中展示仍可直接返回错误身份与有限 IV。候选、阈值及风险参数均未改动。

2. **P2：修订 IC 快照丢失明确的历史单核心身份。**
   真实 `state.bootstrap_record()` → `anchors_from_record()` → `_write_last_verified_snapshot()`：历史核心 14 张，动量 0，经济合约明确为 `510500P2612M07500`，但未迁移的 bootstrap 无专属 v14 字段。新快照只读专属字段，输出“核心14张 身份待核验”，完全丢失已知合约。此前快照共享字段会显示这个合约。此为本轮 snapshot delta 的兼容回归；不能据此推断当前正式账本也是 bootstrap。

3. **P3：IM 零核心数量仍展示残留核心合约。**
   `cash_wait`、核心0、动量0.5且核心旧 identity 字段仍有值时，快照输出“核心0张 `MO2612-P-7600`”。状态的核心0由数量明确，尚未证明改变目标或计划。这是旧 IM 分支的展示瑕疵，**不是本轮修复引入的新回归**；不把它与已披露 A 型完整支持、数量归一化、CFFEX 真实归档等未完成事项混淆。

## 已通过的攻击边界

- 分离核心/动量合约时，成功核心 overlay 能保留两腿身份；零目标不显示残留 IC 核心约；纯文本刷新不改结构化字段。
- 非 future 路线的零核心文案不会显示残留 IC 核心约，动量仍展示。
- 真实 `project_profit_open` 先确认新核心5张；再模拟收盘核心目标降为0或改4张，当前仍为新约5张，ordinary plan old=5/new=0或4，不把未来计划写为当前持仓。
- 同日 baifenwei 观察按 mode 隔离，跨日隔离，返回深拷贝；跨 report batch 不复用观察；上下文退出清理。9圈作为独立参考按日期缓存，与策略 mode 无关。
- IC数据失败且IC快照也失败后，IM独立快照仍展示；共享观察能在同一报告复用，兜底不调用 fetch_score/fetch_jiu_reference。

## 消费者边界

静态追踪 `_handle_signal` 仍读取最终 `live['put_current']` / `live['put_target']` 和 `live.get('iv_warning',{}).get('text')`；日报 `render_stored_close_report` 仍读取保存的 iv_warning.text 和结构化普通核心目标。移动 IV 计算位置没有发现字段删除或接口位置消费者，但本次不替代已披露的远端固定 pin/两端交付验收；不声称云端字节已与本地一致。

本目录后续新增复验标签记录修复状态，首次失败与本节原始发现保留。

## 最小修复后独立复验

父任务完成生产最小修改后，运行 `python quant_research_runs/20261021_external_review_redteam_v1/display/run_checks.py post_fix`。

- **16 项通过，0 失败、错误、跳过，1.94 秒。** 5 条 warning 为第三方 Pydantic 弃用1条及 JUnit record_property 格式4条；XML中仍实际保存了断言输出证据。
- 仅将三处 helper 链调用增加真实顶层已使用的 anchor 参数，未放宽合约、IV、数量、计划或门禁断言。
- 缺核心报价现保持已核验核心身份；IV 为 None，定价函数不被调用。bootstrap已知身份保留；IM零核心不附旧约。成功 overlay、3x后零目标/改量、观察隔离及双产品失败隔离均无新增失败。
- 结果：`post_fix.xml`、`post_fix.txt`、`post_fix.json`；源码及自有测试精确哈希：`post_fix_source_hashes.json`。

复验时 bot SHA-256：`1be3211a2541b996dccfc69dbe67321e78bdda22cb048bf538fa1e07fbb9f708`。

限制：本16项是独立函数/真实 helper 调用链攻击，不是对完整 `_build_live_trade_signal` 所有外部市场依赖的逐字段端到端重放；受控定价不证明当日真实IV数值。未运行远端日报、发信或正式账本，未把本地通过宣称为云端验收。未重新计算历史绩效或资金压力；A型完整维护、数量批准语义、官方CFFEX真实月包与固定源码交付消费者等原先披露的未完成事项仍按主记录追踪。
