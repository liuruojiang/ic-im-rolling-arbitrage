# OHLCV 等待语义与分页预算修复

本分支仅修改 bot 的两种 OHLCV 异常、探测 ContextVar、Tencent 分页函数及三源聚合函数，新增 test_icim_expert_ohlcv_20261021.py。未改页面、builder、日历、其他市场函数或账本。

- 全部日线未就绪仍为 DailyOHLCVNotReady，status=waiting_for_completed_daily_ohlcv。
- 混合失败为 DailyOHLCVSourcesUnavailable，status=daily_ohlcv_sources_unavailable。各聚合异常包含 source_failures（来源、异常类型、原因、状态）与 waiting_sources，不把错误报价或传输故障都写成“日线未更新”。
- 全部传输错误保留 SignalTransportError，兼容既有单次传输重试。
- 已有来源提供日线未就绪证据时，Tencent 先请求最多最近30天的一页。该页仍滞后时立即等待，避免下载多年历史；有当日行时继续下载和严格验证完整历史。探测不能代替暖机历史、完整日期索引或成交量校验。
- 探测页检查数值、OHLC价位关系和成交量，畸形数据不会被误标为等待。探测行保留到合并阶段，和完整历史冲突仍拒绝。

验证：targeted_02 的新测试 + 原 Tencent 分页 + 原日线等待共37项通过，1项第三方 Pydantic 弃用提示。包括真实适配器/校验器运行的三源滞后场景：仅抓取一个Tencent近期页面，保留三个未就绪来源并恢复ContextVar；另覆盖有效第三源、完整历史不足、重叠冲突、坏探测值、混合失败及全传输分类。

数据为合成离线输入；未声称取得新的真实未来日线。这里是并行实现期间的针对性验证，不替代根代理最终集成回归和独立对抗验收。函数及测试绑定见 implementation_binding.json。
