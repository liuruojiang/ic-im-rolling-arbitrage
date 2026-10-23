# 已部署原日包：服务端恢复交接

本轮使用 data/icim_v14_original_inputs 的真实 2026-10-22 IC/IM 包，在正式账本副本运行完整 LedgerCoordinator。没有启动远端服务，没有改写正式账本或策略源码。

## 已通过

- 显式 historical_replay：两品种信号正常核验并一起落账，副本 seq19/10-21 → seq20/10-22；外部 HTTP 尝试 0。
- 自动补录分支：同样恢复一天；仅调度控制时钟设为 10-23 08:00，是模拟触发时钟，不是未来真实市场验证。市场输入全部真实且来自 10-22。
- 重复补录为 0 次推进，副本文件不再改变，health=ok。
- 缺 IC 包、IC 必需 blob 损坏、错误目录，均未部分落账；损坏包不绕过哈希检查。
- 正常路径的 IC 212 / IM 227 个信号字段及完整持仓状态，除三个计算时刻外，两条恢复路径严格一致。
- 正确目录的离线预检退出 0；错误目录退出 2，不联网、不写账。

以上是服务端代码在隔离副本的验收，不代表远端 Poe 已切换配置。此前 2918 项累计回执保持不变；本轮没有生产逻辑修改，未重复执行全量集。

## 实际目录

- 正式账本只读来源：D:\动量策略\IC和IM滚动套利\runtime\ic_im_v1_4_r1
- 原日证据：D:\动量策略\IC和IM滚动套利\data\icim_v14_original_inputs
- 固定源码清单：D:\动量策略\IC和IM滚动套利\quant_research_runs\20261022_free_source_integration_v1\delivery\capture_source_pin.json

服务端 LedgerCoordinator 构造时读取 ICIM_EVIDENCE_DIR。外部采集器目录不会自动覆盖已有服务进程环境；后续正式启动/补录需显式配置该变量。默认 state/input_evidence 与本机采集目录不同，不能只看到某处有包便推断服务能读取。

## 只读就绪检查

以下整行可在 CMD 或 PowerShell 执行，无需 &。只核对下一待补日的两份原日包、完整性、迁移账本和源码 pin，不执行补账。market-day 应填写真实账本的下一未核验交易日，而不是任意今天。

```bat
python -X utf8 -B "D:\动量策略\IC和IM滚动套利\quant_research_runs\20261022_deployed_evidence_recovery_v1\delivery\check_recovery_readiness.py" --state-dir "D:\动量策略\IC和IM滚动套利\runtime\ic_im_v1_4_r1" --evidence-dir "D:\动量策略\IC和IM滚动套利\data\icim_v14_original_inputs" --source-pin "D:\动量策略\IC和IM滚动套利\quant_research_runs\20261022_free_source_integration_v1\delivery\capture_source_pin.json" --market-day 2026-10-22
```

`ready_for_isolated_recovery` 仅表示输入就绪；正式落账仍须两腿完整、非诊断、非 provisional、满足持久化资格。不应把研究恢复生成的 seq20 直接复制回正式目录。

缺包时，既有历史路径可能尝试另取 dated 数据；本次负例将这些请求全部阻断，分别观察到 4/8 次尝试，并确认没有落账。不能把三个负例也称为零请求验证。若包存在但损坏，则直接诊断，未尝试联网。

## 后续边界

2027 日历在本地仍未配置，正式执行继续严格拒绝；官方安排与 oracle 补齐后才可恢复相关日期。未来真实维护日、长期每日包连续性、本机重启自启与远端环境同步仍不能由本次普通持仓日验证替代。采集器需要主机运行。
