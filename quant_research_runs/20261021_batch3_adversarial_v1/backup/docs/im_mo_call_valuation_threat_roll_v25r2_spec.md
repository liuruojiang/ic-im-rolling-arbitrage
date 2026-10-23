# IM + MO Call受威胁移仓 v25r2 比较器修复复跑规格

冻结日期：2026-08-19（Asia/Shanghai）  
状态：v25r1奇偶比较器修复；策略规则不变；未批准实盘。

## 1. 修复原因

v25r1已经把全部受威胁信号的正式PE状态写入日志，`formal_state_errors`由22降为0。但v25r1的奇偶比较器直接比较内存时间戳/布尔值与CSV重新读入后的字符串，并对CSV浮点往返使用`1e-14`阈值，造成伪文本差异及最大`1.819e-12`的序列化差异。

落盘产物的独立比较已经确认：

- v25与v25r1的`call_trades.csv`完全相同；
- 两版`signals.csv`剔除预先允许修复的`history_kind/official_rolling_pe/pe_percentile_10y/pe_history_rows/valuation_state/state_changed`六列后完全相同；
- 两版`daily_candidates.csv.gz`解压后SHA-256均为`7e86f6f6cd5f38af42a743582ec3040a3135a8c961c25d48e1d054badbb9d4c6`。

v25r2只把奇偶比较改为相同CSV序列化后的规范化比较。不得改变任何交易、信号、收益、成本、估值状态、风险或判定门槛。

## 2. 完整冻结策略

全部策略规则与预注册判定原样继承：

- `docs/im_mo_call_valuation_threat_roll_v25_spec.md`；
- `docs/im_mo_call_valuation_threat_roll_v25r1_spec.md`。

尤其不含TP80；每日以5%剩余虚值触发；行权价向上至少5%、到期日取严格下一个挂牌月；救援豁免IV26；连续最多5次；T收盘冻结、T+1官方收盘执行。

## 3. 最终奇偶硬门槛

- v25r2逐日表CSV规范化后须与v25逐日表完全相同；
- v25r2交易表CSV规范化后须与v25交易表完全相同；
- v25r2信号表剔除六个PE遥测字段后，CSV规范化须与v25完全相同；
- `formal_state_errors=0`，其他全部审计继续通过。

任一项失败则结论为`telemetry_repair_parity_failed`。

## 4. 正式路径

- 脚本：`im_mo_call_valuation_threat_roll_v25r2.py`；
- 输出：`outputs/im_mo_call_valuation_threat_roll_v25r2/`，首次不可覆盖；
- 参数工件：`quant_param_scan_runs/20260819_new_strategy_research_im_mo_call_valuation_threat_roll_v25r2_im_mo_call_overwrite_threat_otm5_up5_next_expiry_max5_parity_repair/`。

v25、v25r1失败产物永久保留为审计证据，不覆盖。v25r2仍不获准实盘。
