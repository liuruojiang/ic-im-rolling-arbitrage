# 恐慌来源与账本边界独立终验

结论：本分工识别的 F1、F2、F3 及二阶删除 T 快照绕过已修复；合法数据组合无回归。本结论只覆盖本地源文件及生产/消费函数，不替代云端发布或 SMTP 验收。

## 原反例复验

- CSV 选中值 20→80 而来源快照不变：消费端拒绝，发布端隐藏网格/合计目标。
- 网页-only 盘中选中值 20→80 而 page_value 不变：拒绝，发布端隐藏相关目标。
- 明示 T 已有数值却声称未发布并沿用 T-1：拒绝。
- 采集当日、生成次日但无 retrospective_replay：拒绝。
- 删除观察 map 的 T 键而 latest 仍为 T：拒绝，错误为“CSV最新交易日不可从观察快照删除”。

上述修前证据保留于 results_before.json、repaired_edges_before_second_fix.json，不随终验覆写。

## 覆盖和结果

- 128/128 合法生产→消费矩阵通过，含 close/intraday、CSV/网页任一失败、T/T-1/T-2、0/25/50/100 分界。
- 7/7 二阶边界通过，覆盖正确 T-1 值但 T 已存在、删除 T 观察、缺失 map、latest 与观察冲突、布尔、未来日期，以及合法“当日网页盘中未收盘确认”沿用 T-1。
- 11/11 九圈参考隔离通过，含 0/100/前日值以及负数、超界、布尔、NaN、未来日、重复冲突、错误 history、空响应。九圈异常不改变百分位读数和网格结果。
- 三份相关永久回归文件共 137 项通过、0 失败、0 跳过，耗时 3.12 秒。原测试断言没有修改。

初次执行 pytest 的 tmp_path 因 CPython 3.14 在受限 Windows token 下 mkdir(0700) 产生不可重开目录而 setup 报错。终验使用仅作用于测试进程的 mkdir/chmod 权限兼容运行器和隔离状态目录后，137 项全部通过；没有放宽业务断言。XML/文本及运行参数存于本目录 regression_final.*、regression_final_run.json。

## 可重跑命令

```
python quant_research_runs/20261016_fear_delivery_adversarial_v2/feed_ledger/probe_feed.py
python quant_research_runs/20261016_fear_delivery_adversarial_v2/feed_ledger/probe_repaired_edges.py
python quant_research_runs/20261016_fear_delivery_adversarial_v2/feed_ledger/probe_matrix.py
python quant_research_runs/20261016_fear_delivery_adversarial_v2/feed_ledger/probe_reference.py
python quant_research_runs/20261016_fear_delivery_adversarial_v2/feed_ledger/run_final_regression.py
```

终验源文件 SHA-256 见 final_source_manifest.json。本审计未修改生产代码、未读取/修改正式账本、未发送邮件。
