# FIX11 月度买 Put 次日开盘研究包

[查看结果](report.md) · [净值和回撤图](nav_drawdown_comparison_1y.png) · [逐日对照](nav_comparison_1y.csv)

本研究包包含执行时点对照、两个研究工具修复、复算代码和验证记录。基线与候选共用截至 2026 年 11 月 18 日的冻结行情及 2025 年 11 月 17 日收盘账户状态。账户流水为研究模型输出。

回放需要本地已经验证的 `outputs/fix11_nav_1y_20261118_close/` 冻结目录和其原研究依赖，以及 `repair_source/terminal_market_repro/` 的同日修正报价。原始行情、估值快照、旧研究原始报文和修正报价文件由本地保存。研究入口要求这些文件通过固定哈希校验。仅浏览报告和下载逐日对照无需这些输入。

复算使用 `run_comparison.py --scratch <本次独立临时目录>`。已有通过回执和账户 CSV 时，`plot_comparison.py` 可在具备冻结回执的工作区重新渲染。原始报文和研究工具的反例入口分别为 `repair_source/verify_option_timestamps.py` 和 `repair_guards/test_replay_guards.py`。

交易时点仍为研究候选。生产信号入口和参数以原版本登记为准。
