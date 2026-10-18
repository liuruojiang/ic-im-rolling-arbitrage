# 恐慌数据与账本边界独立对抗审计：修复前

对象：策略 be4173440daace20ee698103d05bc75838316a30 对应本地文件。被审计文件 SHA-256 存于 results_before.json。未访问或修改正式账本，未发送邮件，未修改生产代码。

## 结论

正常数据通路已实现用户授权的 CSV/网页任一可用、冲突披露、严格 T/T-1 和 9 圈参考隔离。但新增消费校验没有完整保留证据之间的一致性约束，尚不宜宣称所有边界处理完成。

### F1 / P1：模型使用读数未绑定选中来源的观察值

CSV-only 真实形状证据记录 T=20。只改 fear_greed_index 为 80 并依策略重算网格，其 CSV latest date/value 和 SHA 均保留不变。validate_fear_grid_signal 接受，publication_view 显示网格 0；原信号显示网格 0.5。网页-only 盘中场景同样接受模型值 80 而 page_value 仍为 20。

期望：消费侧独立要求选中来源的相应数据日读数与模型值一致。网页始终对 page_value；CSV 建议保存 T/T-1 观察值快照并按 data_day 对照，且与 latest 元数据一致。允许单侧和冲突使用不意味着允许使用与选中来源自身不一致的数值。

### F2 / P2：T 值已存在仍可伪造未发布沿用

CSV 明示 T 的合格值存在时，可把状态改成 close_previous_session、data_date=T-1、reason=same_day_not_published 而通过消费校验和发布。期望：沿用原因须对照来源观察值，存在 T 时不能谎称未发布；保留已授权的网页 T 未收盘确认且 CSV 有 T-1 的特殊沿用。

### F3 / P2：生成跨日可以丢失回放标记

采集 10/16 18:30，生成 10/17 00:00:01，仍标 close_confirmed；新来源校验接受且发布。无当日新建 ordinary Put pending 时，validate_replay_plan_evidence 立即返回，未形成其他补救。期望：采集或生成任一北京时间跨越 signal_day，都必须 retrospective_replay；源值的单侧授权不放宽真实生成时点标识。

## 通过及不判为故障的范围

- 现有 fear_grid 与新 source_delivery 两专项共 130 项通过。
- 新增生产→消费合法组合矩阵 128/128 通过，覆盖 close/intraday、CSV/网页缺失、T/T-1/T-2，以及 0/25/50/100 分界。
- 9 圈独立注入 11/11 通过：0、100、前日值正确陈列；负数、超界、布尔、NaN、未来日、同日冲突、错误 history 和空响应均降级，百分位和网格结果不变。
- 15:00 后盘中取数被清成缺值、publication_view 显示 partial；state 拒绝盘中窗口外证据属于既有时点保护，本轮不列为回归故障。
- T-1 两侧一致但其网页更新时间在 T-1 盘中仍能使用，属于放宽发布终值证明的授权范围待主审解释，本轮不据此宣判失败。

## 复现

从仓库根目录运行：

```
python quant_research_runs/20261016_fear_delivery_adversarial_v2/feed_ledger/probe_feed.py
python quant_research_runs/20261016_fear_delivery_adversarial_v2/feed_ledger/probe_matrix.py
python quant_research_runs/20261016_fear_delivery_adversarial_v2/feed_ledger/probe_reference.py
python -m pytest test_ic_im_fear_grid_v1.py test_ic_im_fear_source_delivery_20261016.py -q -p no:cacheprovider
```

前三个脚本仅写本目录 JSON。results_before.json 与 matrix_results_before.json 保留修复前证据。

## 缺口

此分工不验证 SMTP、远端 workflow pin/实际运行、邮件 HTML 全布局或正式账本恢复；这些由其他审计角色独立验收。上述反例证明校验函数接受矛盾证据，并不说明今天正常 producer 已产生这种错误读数。
