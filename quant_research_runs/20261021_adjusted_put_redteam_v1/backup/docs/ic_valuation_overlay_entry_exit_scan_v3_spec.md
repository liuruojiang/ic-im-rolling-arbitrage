# IC 固定估值增仓开仓/退出阈值扫描 v3 冻结规格

冻结日期：2026-08-18（Asia/Shanghai）  
状态：预注册研究；未批准实盘。

## 1. 版本关系

本版完整继承 `docs/ic_valuation_overlay_entry_exit_scan_v2_spec.md` 第1—7节的研究问题、正式与诊断样本、96组网格、成本执行、硬门槛、80%宽度要求和必须输出，不改变任何参数、指标或筛选规则。

v2在收益计算前因三个上游SHA-256后半段抄写错误而被预检终止；没有生成正式回测结果。失败记录为 `outputs/ic_valuation_overlay_entry_exit_scan_v2_failed_preflight/record.md`。v2冻结规格及sidecar不改写，本版只纠正输入哈希并改用新的正式输出和参数工件目录。

## 2. 路径

- 实现模块：`ic_valuation_overlay_entry_exit_scan_v2.py`（v3包装入口运行时只覆盖版本、规格、输出、扫描路径与正确冻结哈希，不改变回测函数）。
- 正式入口：`ic_valuation_overlay_entry_exit_scan_v3.py`。
- 正式输出：`outputs/ic_valuation_overlay_entry_exit_scan_v3/`，首次目录不可覆盖。
- 参数工件：`quant_param_scan_runs/20260818_ic_valuation_overlay_entry_exit_scan_v3/`。

## 3. 冻结研究设计摘要

- 开仓阈值0.000—1.500、退出阈值1.250—2.250，均步长0.125；退出减开仓至少0.500，共96组，当前1.000/2.000同批运行。
- 正式组合2015-04-16至2026-08-14：固定1倍滚IC+冻结主线Put底仓；新增1倍IC在T+1活跃合约官方开盘成交，新增仓本层不增加Put。
- 2007年长历史仅用价格指数与固定经济分数审计完整周期，不含上市前IC贴水或Put。
- 选择门槛、收益容忍、回撤峰值日退出、周期数和宽度判定全部以v2规格为准，不得事后调整。

## 4. 正确冻结输入

- `docs/ic_valuation_overlay_entry_exit_scan_v2_spec.md`: `c8f081595f5db1b11e8292cd989cf6cd6c44bc9813a66ef684c6a5b23d25aefa`
- `ic_valuation_overlay_entry_exit_scan_v2.py`: `71e4253a439bfb8e5bc6c5a0d598a0efab4560fa6fe7f6ad4d864ee0c82ef259`
- `ic_valuation_overlay_put_sync_v1.py`: `e9049f750e422d128c0378e4c311270ca32495b1d84c0b41588db0db7f460b36`
- `docs/ic_valuation_overlay_put_sync_v1_spec.md`: `7cf83eea40fb8d4aafb6c05a955be010e8b0ad26898c589033fb87a42b6935c3`
- `outputs/ic_valuation_overlay_put_sync_v1/output_manifest.json`: `2167faf26135d1b48a87b08eaf417433dd6830432fb7a7ee67279a8ed9051476`
- `outputs/ic_valuation_overlay_put_sync_v1/daily_candidates.csv.gz`: `0423f4f7d9abfb6e9b961f380ffdc79a08ba91970988d3de620a9c342be36965`
- `outputs/ic_valuation_overlay_put_sync_v1/integrity_checks.json`: `12c89231e566b4c6d2c846c8b8440fa89723f72f4ef88e201eab5b98ca8bc8d9`
- `outputs/ic_510500_put_mom120_delta_floor_v21/daily_candidates.csv.gz`: `11a15bffe6536b74399372ed928718751f7a4e0c552fd1393150d5c839ce2f2a`
- `data/ic_monthly_discount_roll_v1/cffex_ic_contracts.csv`: `4e02b889747112459125999382c3ff2fe89017aaea30df05e91bb2a7bc1e2104`
- `outputs/ic_monthly_discount_roll_v1/daily_nav.csv`: `bd575ee101b77791bfad3968e0cd221fb189624b8439d9e5dcecddcd944c092d`
- `outputs/ic_fixed_valuation_unbounded_score_v6/daily_unbounded_fixed_scores.csv.gz`: `34109cf7a5dec87c391f37b23cdc56cbb93611fd48ba7ba2929d74ca8a368b77`

任何冻结输入不匹配、正式输出已存在、底仓或当前规则奇偶误差超过 `1e-14`、因果或收益恒等式失败时必须停止。
