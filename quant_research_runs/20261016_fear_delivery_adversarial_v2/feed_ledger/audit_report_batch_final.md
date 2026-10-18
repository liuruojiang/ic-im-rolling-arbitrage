# 报告域快照缓存与真实两腿一致性：独立补充终验

结论：新报告域 observation_batch 在本轮覆盖内正确复用、隔离和释放，未发现跨报沿用或两产品样本漂移。没有修改生产代码或正式账本，也没有发送邮件。

## 13 项独立检查全部通过

- 域外两次请求不缓存；同报告及嵌套批次共用一次观察。
- 返回值的嵌套 dict 被调用者修改后，后续产品取得的缓存仍保持原值，deepcopy 隔离有效。
- mode 和 signal_day 均作为百分位缓存键；九圈在报告内按日复用。
- 正常退出和异常退出都释放 ContextVar；下一报告重新取数。
- collection_clock 嵌套进入 observation_batch 时复用原批次，退出后释放。
- 从批次外启动的两个异步报告各持独立 ContextVar，域内稳定、域间不共享。
- 云端实际交接 result.json 的 IC/IM 所有 fear_* 和 jiu_* 字段全部一致，消费校验分别通过。

真实样本来自 run 38856549173 的本地交接工件：百分位两腿同为 51.36、数据日 2026-10-16、网页发布 13:00、采集 13:36:33；CSV 快照均为 10/15=24.71、10/16=51.36。九圈均为 2.28、数据日 10/15、采集 13:36:35。原工件 SHA-256、源文件 SHA-256、全部逐项结果及真实字段保存在 report_batch_results.json。

## 永久回归

现有 fear_grid、source_delivery、新 adversarial 三份专项共 138 项通过、0 失败、0 跳过，耗时 3.09 秒。沿用测试进程内 Windows mkdir 权限兼容运行器，未修改业务断言。新增报告域测试已包含在此次执行内。上轮 137 项 XML/文本另存 regression_pre_cache.*，初次失败证据仍保留。

复跑命令：

```
python quant_research_runs/20261016_fear_delivery_adversarial_v2/feed_ledger/probe_report_batch.py
python quant_research_runs/20261016_fear_delivery_adversarial_v2/feed_ledger/run_final_regression.py
```

本补充只独立确认来源样本及本地消费边界；云端 pin、构建、邮件封装和 SMTP 由主审及其他分工验收。
