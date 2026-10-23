# IC固定估值增仓退出边界扩展扫描 v4 冻结规格

冻结日期：2026-08-18（Asia/Shanghai）  
状态：预注册研究；未批准实盘。

## 1. 研究问题

v3确认`1.000/2.000`退出过晚，防御区域集中在退出1.250，但1.250位于网格下边界。本版只扩展更低退出阈值，检验1.250是稳定平台、过晚边界还是偶然点；不改变底仓、Put、仓位、成本、执行时间或估值定义。

## 2. 路径和冻结组合

- 正式入口：`ic_valuation_overlay_exit_boundary_scan_v4.py`。
- 正式输出：`outputs/ic_valuation_overlay_exit_boundary_scan_v4/`，首次目录不可覆盖。
- 参数工件：`quant_param_scan_runs/20260818_ic_valuation_overlay_exit_boundary_scan_v4/`。
- 底仓：固定1倍滚IC，加冻结的`model_l190_mom25`主线Put。
- 新增仓：低估时增加1倍IC，高估退出；本层新增仓不增加Put，以隔离估值退出边界。
- 估值T日收盘确认，T+1活跃IC官方开盘成交；持有期实际月滚。
- 每1倍IC占30%保证金/缓冲，余额年化3%；期货每边1bp、持有滚动双边2bp。

## 3. 正式和诊断样本

- 正式组合：2015-04-16至2026-08-14，CFFEX IC真实活跃合约官方开盘/结算与v21冻结模型Put。
- 长历史诊断：2007-01-15至2026-08-17，中证500价格指数收盘与固定经济单位二取三分数，T信号/T+1指数收盘生效。
- 2007诊断没有上市前IC贴水、真实合约、Put或保证金路径，只用于周期数和状态宽度，不得当作正式组合收益。
- 强制报告full、last_10y、last_5y、last_3y、last_1y的CAGR、波动、Sharpe、MaxDD和相对基线差异。

## 4. 预注册网格和同批对照

- 开仓：0.000、0.125、0.250、0.375、0.500、0.625。
- 退出：0.875、1.000、1.125、1.250、1.375。
- 只保留`退出-开仓>=0.500`，共27组正式候选。
- 同批额外运行旧规则`1.000/2.000`作为风险寻求对照；底仓`base_core_put`也同批重跑。
- v3观察线`0.125/1.250`、`0.375/1.250`、`0.500/1.250`均包含在27组内。
- 不得在看到结果后补点；若最佳点仍在边界，只能新版本继续。

## 5. 风险优先的预注册门槛

本版不再要求防御组合追平旧2倍暴露路径的最近一年收益；旧规则仅用于显示降低风险的代价。候选必须同时满足：

1. 正式与2007诊断完成周期分别至少2个和3个。
2. 在底仓全样本最大回撤峰值日，新增仓已经退出。
3. full、10Y、5Y CAGR分别至少高于固定1倍底仓2个百分点；3Y、1Y CAGR不得低于底仓1个百分点。
4. full、10Y、5Y MaxDD均不深于-35%。
5. 五个窗口的MaxDD均优于旧`1.000/2.000`，且full MaxDD至少改善20个百分点。
6. full Calmar（CAGR/|MaxDD|）高于固定1倍底仓。
7. 因果、官方开盘、滚动成本、收益恒等式、冻结基线和v3重合候选奇偶校验全部通过。

在通过者中，机械选择full Calmar最高者。该选择最多进入研究观察，不直接批准实盘；相对旧规则超出标准收益容忍的窗口必须明确展示。

## 6. 宽度标准

- 宽度指标：full Calmar。
- 机械最高点在开仓轴和退出轴的上下两侧均须存在相邻0.125点；四个邻点均须通过第5节门槛，且full Calmar至少保留中心点80%。
- 必须报告四向邻点、保留率、平台/脊线、是否位于边界。
- 最高点通过门槛但缺少宽度时，状态只能是`watchlist_edge_or_peak`；不得改选收益略低但未按预注册顺序选出的点作为正式赢家，但可以列为宽平台观察线。

## 7. 必须输出

- `record.md`、`metrics_by_window.csv`、`window_metrics_wide.csv`
- `scan_surface.csv`、`candidate_decisions.csv`、`ridge_width.csv`
- `daily_candidates.csv.gz`、`overlay_trade_audit.csv`、`overlay_cycle_summary.csv`
- `index_proxy_metrics_by_window.csv`、`index_proxy_daily.csv.gz`
- `drawdown_overlap_audit.csv`、`annual_metrics.csv`
- `decision_summary.json`、`integrity_checks.json`、`data_manifest.json`、`command_log.txt`、`output_manifest.json`
- 参数工件标准五件套并通过complete strict。

## 8. 冻结输入

- `ic_valuation_overlay_entry_exit_scan_v2.py`: `71e4253a439bfb8e5bc6c5a0d598a0efab4560fa6fe7f6ad4d864ee0c82ef259`
- `ic_valuation_overlay_entry_exit_scan_v3.py`: `6d66613b0c250992d8da870308737aec399eb2bdd521f66b4a48d270846590c6`
- `docs/ic_valuation_overlay_entry_exit_scan_v3_spec.md`: `01106713f0d347dda18a44714bf2c828ea9e5de7ebb5821f21605bd80efe74d7`
- `outputs/ic_valuation_overlay_entry_exit_scan_v3/output_manifest.json`: `0ac668adca879f06502b9822209438a1b10625435e18fbba2838ac985ab1a7a9`
- `outputs/ic_valuation_overlay_entry_exit_scan_v3/daily_candidates.csv.gz`: `00c6fb2dd7f1344e74aa276d84f31c230d4ed579d943f4069e3b3f54f7e4a78b`
- `outputs/ic_valuation_overlay_entry_exit_scan_v3/integrity_checks.json`: `db162d48e10977120fdf5d7ede3da5aae38e1d3b749e3bf2fc863f109f2fea97`
- `ic_valuation_overlay_put_sync_v1.py`: `e9049f750e422d128c0378e4c311270ca32495b1d84c0b41588db0db7f460b36`
- `outputs/ic_510500_put_mom120_delta_floor_v21/daily_candidates.csv.gz`: `11a15bffe6536b74399372ed928718751f7a4e0c552fd1393150d5c839ce2f2a`
- `data/ic_monthly_discount_roll_v1/cffex_ic_contracts.csv`: `4e02b889747112459125999382c3ff2fe89017aaea30df05e91bb2a7bc1e2104`
- `outputs/ic_monthly_discount_roll_v1/daily_nav.csv`: `bd575ee101b77791bfad3968e0cd221fb189624b8439d9e5dcecddcd944c092d`
- `outputs/ic_fixed_valuation_unbounded_score_v6/daily_unbounded_fixed_scores.csv.gz`: `34109cf7a5dec87c391f37b23cdc56cbb93611fd48ba7ba2929d74ca8a368b77`

任何冻结输入不匹配、正式目录已存在、候选数不是27+旧规则+底仓、基线或v3重合候选奇偶误差超过`1e-14`、因果或收益恒等式失败时必须停止。
