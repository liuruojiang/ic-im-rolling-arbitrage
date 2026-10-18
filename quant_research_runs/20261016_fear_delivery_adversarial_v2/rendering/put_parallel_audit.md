# 并行月度Put展示修复独立核验

实际输入：`20261016_put_delivery_completion_v1/handoff-38853603565/report/result.json`。SHA-256：`c54e9048432cdce19de84645c71022a5f7bfeebe23b8f15461013224bade2434`。本核验只重渲染该真实13:02快照，不取新行情、不续账、不发送，也不修改原输入。

实际IC旧持仓为10张 `510500P2612M07500`；普通月度候选为17张 `510500P2612M06500`；Put维护时点为2026-10-16收盘、盘尾复核；期货目标交易日为2026-10-19。旧腿与候选字段不可互相覆盖。

## 结论

- 根目录当前Poe bot：真实快照重渲染通过。17张/06500候选不变，旧07500保留；分列期货下一交易日及Put今日收盘，未出现笼统“下一交易日张数”等错误标签。
- completion/cloud消费者：真实快照重渲染通过，正文及HTML均保留当前07500，目标展示06500/17张、今日收盘维护候选及盘尾复核。
- single_source/cloud消费者（合并Put补丁前）：独立反例失败。其 `ic_contract(...target,core)` 仍选07500，纯文本/HTML遗漏今日收盘候选时点及期货/Put分列。不可用之前的readiness成功掩盖该语义缺口。

检查脚本为 `verify_put_snapshot.py`，分别用参数 `root`、`completion`、`single_source` 运行。输出为 `put_*_actual_snapshot.*` 与 `put_*_checks.json`。

## 已重跑的相关门禁

- completion云端 `test_ic_im_v1_4_maintenance_display_20261016.py`：5项通过，含真实输入/provenance、当前旧腿与候选、正文/HTML、合成3倍计划隔离、错误月度节点、旧10月9日标题/正文/HTML哈希。
- 根目录 `test_ic_im_option_maintenance_notice_20261016.py`：36项通过，包含4个新增raw Poe维护日/非维护日及IC/IM场景。

对应XML为 `put_completion_results.xml`、`put_root_results.xml`。

## 应整体纳入的展示变更

1. 根目录 `poe_ic_im_mainline_v1_4_bot.py` 现有月度Put文案修复及其现有 `test_ic_im_option_maintenance_notice_20261016.py`。
2. completion/cloud中的 `build_ic_im_v1_4_digest.py`（当前差异仅put_text添加维护候选前缀）及 `ic_im_v1_4_put_display.py`（普通月度候选身份与分时点数量/合约表）。
3. 云端 `test_ic_im_v1_4_maintenance_display_20261016.py`，以及 `tests/fixtures/icim/maintenance_20261016/result.json`、`provenance.json`。

completion/cloud的 `ic_im_v1_4_delivery_contract.py` 和 `prepare_ic_im_v1_4_marker.py` 是本轮恐慌门禁修复前基线，不能跟随目录整体覆盖；应保留single_source本轮新版。其它同名依赖当前一致。
