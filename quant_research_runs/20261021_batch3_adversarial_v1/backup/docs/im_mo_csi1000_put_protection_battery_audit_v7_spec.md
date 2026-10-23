# 中证1000 Put保护全类别复测 v7 审计修复预注册规格

- 版本：`im_mo_csi1000_put_protection_battery_audit_v7`
- 预注册日期：2026-08-17，Asia/Shanghai
- 状态：对冻结v6经济路径的审计修复；未获准实盘
- 冻结输入：`outputs/im_mo_csi1000_put_protection_battery_v6/`，不重算、不改写任何逐日经济收益

## 1. 修复原因

v6运行后审计发现：`sig_dynamic075/080/085_front_m95`在真实MO样本中都是0保护日、0交易，v6冻结规格第6.5条要求“保护线必须有非零持仓、成本和交易”。v6程序只将这三条的`activity_pass`置为False，未把整体完整性标为失败。

因此：

- v6的逐日收益、合约、成本和指标作为冻结审计证据保留；
- v6的`mixed_not_confirmed`不作为合规的最终判定，改标`implementation_audit_failed`；
- v7只修正完整性语义、重叠验证样本和冻结邻点判定，不允许更改价格、信号、成本、样本或候选。

## 2. 零触发候选的正确语义

- 若某层候选的冻结目标从未大于0，同时实际持仓、成本和交易都为0，记为`inactive_sample`。它表示“样本中规则没有触发”，不是价格引擎失败，但也不得用于支持或反驳Put效果。
- 若冻结目标曾大于0，但持仓/成本/交易为0，才是`execution_integrity_failed`，整体不得解释。
- 两层绩效通过要求模型和真实层各至少20个保护日；`inactive_sample`必须为`not_testable`。

v7使用v6冻结的逐日`put_fraction`与交易表审计活动性；由于v6没有单独输出每条原始目标，对“0保护日+0成本+0交易”的候选保守标为`inactive_sample_or_unexecuted`，并核对该路径逐日与no-Put完全一致。不将其解释为价格引擎通过。

## 3. 重叠验证修正

- 日Put PnL相关只纳入模型/真实两层各至少20个保护日且Put PnL标准差均>0的候选；
- 对每条纳入候选重算日PnL相关、中位绝对误差及重叠段MaxDD改善方向；
- 中位相关<0.50或MaxDD改善方向一致率<60%时仍为`model_sensitive`。

## 4. 严格执行v6邻点规则

v6的`two_layer_base_pass`只是单线门槛，v7另行执行稳定性：

- 动量扫描：H必须单线通过，且H-10或H+10至少一个也单线通过；
- Put工具网格：候选必须单线通过，且同期限的至少一个相邻行权价比例也通过，同行权价比例的至少一个相邻期限也通过。期限顺序冻结为`front_exit ↔ 2m_monthly_exit ↔ 3m_monthly_exit ↔ 3cycle_hold_expiry`；行权价顺序为85↔90↔95。
- 估值网格和预先指定的单线归因候选只报单线门槛，不从同场最优绩效反推新阈值。

## 5. 判定与输出

- 存在`execution_integrity_failed`：`rerun_required`。
- 无执行完整性失败，但没有经邻点支持的两层候选：`not_confirmed`。
- 存在邻点支持的两层候选，但`model_sensitive=True`：`mixed_not_confirmed`。
- 存在邻点支持的两层候选且模型验证通过：最多`research_watchlist`。

强制输出：v6逐日/指标SHA校验、活动性审计、修正的交叉验证、单线与邻点判定、全/10/5/3/1年主表、逐年主表、不可改写的清单和结果报告。

- 入口：`im_mo_csi1000_put_protection_battery_audit_v7.py`
- 测试：`test_im_mo_csi1000_put_protection_battery_audit_v7.py`
- 正式输出：`outputs/im_mo_csi1000_put_protection_battery_audit_v7/`，首次不可覆盖

任何经济规则变更必须新建版本，不得在v7中调参。
