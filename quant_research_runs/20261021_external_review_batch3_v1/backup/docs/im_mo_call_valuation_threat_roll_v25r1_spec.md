# IM + MO Call受威胁移仓 v25r1 审计修复复跑规格

冻结日期：2026-08-19（Asia/Shanghai）  
状态：v25审计遥测修复；策略规则不变；未批准实盘。

## 1. 修复原因

v25首次正式运行的交易逻辑、收益、成本、状态读取和执行路径均按冻结规格运行，但`threat_roll`与`threat_stop`信号行没有把循环中已经读取的正式PE状态复制到审计字段，导致22条`formal_state_errors`，机械判定被`audit_gate=False`否决。首次输出`outputs/im_mo_call_valuation_threat_roll_v25/`永久保留，不覆盖、不作为正式结论。

v25r1只修复这项信号遥测：所有受威胁信号写入T日因果PE历史类型、官方PE、十年分位、样本数、估值状态和状态变化字段。不得改变任何交易日期、合约、价格、仓位、成本、收益或风险规则。

## 2. 完整冻结策略

全部策略规则、基准、候选、数据、成本、执行、必报结果和预注册判定原样继承`docs/im_mo_call_valuation_threat_roll_v25_spec.md`及其SHA-256，尤其：

- 不含TP80；
- 每日收盘先检查旧Call虚值率是否`<=5%`；
- 救援取严格下一个挂牌到期日，行权价不低于旧行权价`×1.05`；
- 救援不受IV26限制，正常入场仍须IV26；
- T日冻结、T+1官方收盘执行；
- 连续最多5次，第6次或没有合格新约时止损，并暂停至下一月度评估；
- 正式比较B2对B0，A2对A0只作控制。

## 3. 复跑一致性硬门槛

除正式状态审计字段外，v25r1必须与v25首次输出逐日完全相同：

- 新候选逐日`ret/cash_ret/nav/cash_nav`最大绝对误差为0；
- 交易行的日期、动作、原因、旧/新合约、价格、到期日和救援计数完全相同；
- 信号行除PE审计字段外的日期、动作、原因、合约、阈值、行权价和救援计数完全相同；
- 修复后`formal_state_errors=0`且全部原审计继续通过。

不满足任一项则结论为`telemetry_repair_parity_failed`，不得使用v25或v25r1性能结果。

## 4. 正式路径

- 脚本：`im_mo_call_valuation_threat_roll_v25r1.py`；
- 输出：`outputs/im_mo_call_valuation_threat_roll_v25r1/`，首次不可覆盖；
- 参数工件：`quant_param_scan_runs/20260819_new_strategy_research_im_mo_call_valuation_threat_roll_v25r1_im_mo_call_overwrite_threat_otm5_up5_next_expiry_max5_audit_repair/`；
- 上游：冻结v25规格、v25首次失败审计产物、v25脚本、v23正式数据与状态。

冻结后不得改写。v25r1只用于得到经过完整审计的同策略正式结论，不得借修复改变参数。
