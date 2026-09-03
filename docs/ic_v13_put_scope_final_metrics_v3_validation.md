# IC v1.3 Put 覆盖范围 v3 验证记录

日期：2026-09-03（Asia/Shanghai）  
状态：研究验证；未批准实盘。

## 结果完整性

- 现行 `current_core_momentum_combined` 与 `ic_im_mainline_v1_3_fixed_performance_v5` 的日收益、CAGR、波动、Sharpe、MaxDD 最大误差：`9.104e-15`。
- 四条候选各 2,756 行，日期为 2015-04-16 至 2026-08-14；收益全部有限。
- 核心、动量、网格 Put 目标恒等式误差均为 0；现行日收益奇偶误差 `9.975e-17`，现金奇偶误差 `1.110e-16`。
- 真实 510500 Put 原始历史 53,745 行、869 个证券代码，日期 2022-09-19 至 2026-08-14；真实交易最大顺延 0 个交易日。
- 三个新扫描目录的 complete strict 检查均通过。

## 聚焦回归

执行：

```powershell
python -X utf8 -m pytest -q test_ic_roll_momentum_stage2_put_v2.py test_ic_mainline_v1_3.py test_ic_im_mainline_v1_3.py test_ic_valuation_overlay_selected_put_sync_v5.py
```

结果：`25 passed, 1 warning`。警告来自 `fastapi_poe` 的 Pydantic v2 弃用提示，与本研究收益、信号或期权执行无关。

## 权限边界

没有修改 IC v1.3、冻结 IC/IM V2、Poe、日报或订单路径；全部新增内容为研究规格、重放脚本、输出和验证记录。
