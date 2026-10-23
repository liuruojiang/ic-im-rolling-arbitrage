# 交接与复核入口

本轮解决精确 SSE 510500_MM 月链的 HTTP Date 误冲突，生产仅改 ic_im_signal_evidence_v1.py。行情正文及原有新鲜度验证不变，不增加通用请求缓存，不使旧坏包复活。详见上一级 record.md 与冻结规范 docs/ic_im_evidence_http_date_20261022_v1_spec.md。

最终签收入口：accepted_02/receipt.json、delivery/final_verification.json、delivery/recovery_facts_comparison.json。accepted_01 虽然全部测试通过，但源码文件集合检查未通过，不可用于签收。原 22 单列保持，原第五轮 19 项包含，新 Date 专项 18 项包含。

真实原日包与隔离恢复见 real_recovery_01；真实重复 SSE 观测见 live_probe_01。副本恢复不等于正式续账，探针不是正式原日包。

本机采集启动文件为 delivery/start_readonly_collector.ps1，源固定清单 capture_source_pin.json（28 模块），进程/日志/启动回执在同目录。只读正式状态 runtime/ic_im_v1_4_r1，追加证据 data/icim_v14_original_inputs。最后尝试与日归档可用性须分别检查；早前完整包可读不表示最新尝试成功。采集器依赖机器保持开机，不声称配置了重启自启。

PowerShell 中可从项目根目录运行只读复核：

```powershell
python -X utf8 -B quant_research_runs/20261022_evidence_http_date_v1/delivery/compare_recovery.py
python -X utf8 -B quant_research_runs/20261022_evidence_http_date_v1/delivery/verify_delivery.py
```

代理仍间歇超时，脚本 network_probe.py 只走当前已有代理并输出诊断；不修改网络配置。不要停止所有 python 或代理所属 Codex 进程。需要回滚本轮代码时，应先停止本轮指定采集 PID，用 backup/ic_im_signal_evidence_v1.py 恢复源码，再使用上一轮固定清单重新启动其只读采集器；不删除失败包、原日证据或改写正式账本。

2027 官方日历尚未纳入本地配置；后续只能依据交易所公告补齐。远端 Poe 配置、未来维护日取证、多日网络稳定性均未在本轮验收中冒称完成。
