# 归属续核交接

主报告：上一级 record.md；冻结规范：docs/ic_im_production_provenance_20261023_v1_spec.md。逐项 A–H 的原始轮次、源码 SHA、技术风险、具体测试名与 C/G 的实际修复原因均已列明。

本轮实际生产改动只有两个文件：bot（缓存命中重新校验、回放缺证据类型、共用两线程有界收集器）和 evidence_adapter（具名 ic.chain 缓存消费重新校验）。不改策略/配量/日历/H 存储门禁，不增加通用 URL 缓存。

有效验收：accepted_01/receipt.json（2982）、delivery/final_verification.json。原 22 单列保持；旧 2959 已全部包含。新 23 项及旧第五轮 19 项包含，不叠加重复计数。真实包服务端 5 场景、2 个离线预检、事实字段对照是独立端到端证据，不加进单元测试数量。

新采集源清单 delivery/capture_source_pin.json 覆盖 28 模块，启动脚本同目录 start_readonly_collector.ps1。正式状态只读 runtime/ic_im_v1_4_r1，原日证据追加 data/icim_v14_original_inputs。启动时的实际 PID 与状态以 launch_receipt.json、collector_stdout.log 和机器复核为准；10-23 上午只能等待收盘，不能以旧日完整包冒充当日采集成功。

两个工作线程仍有无法安全强杀的已发出请求尾延迟。相同绝对 deadline 包含等待/收尾，过预算不发布成功，但没有强制 45 秒返回保证。不要把这点写成已经有硬截止；不要提前封存仍在写入的任务。

超时频率脚本 delivery/provenance_and_statistics.py 可汇总保存的周期回执。当前只有一个已完成交易日，尚不能评估多日稳定性或判断批大小因果效果。到期日新浪是否收盘前下架当月仍需用以后真实报文观察。

复核命令（PowerShell，在项目根目录）：

```powershell
python -X utf8 -B quant_research_runs/20261023_production_provenance_v1/delivery/compare_recovery.py
python -X utf8 -B quant_research_runs/20261023_production_provenance_v1/delivery/verify_delivery.py
```

verify_delivery 当前检查上午 waiting_for_close 的实测状态；将来收盘后复核需按实际周期状态解释，不能改写本轮首次验收。preflight_checks 和 select_real_bundle 是首次输出工具，不允许在同目录反复覆盖首次结果；再次试验另建阶段。

本机未建立重启自启或远端 Poe 同步保证。2027 日历补齐后仍须对每个完整原日包逐日 historical_replay；无包不承诺恢复。不将研究与副本恢复解释为自动下单授权。
