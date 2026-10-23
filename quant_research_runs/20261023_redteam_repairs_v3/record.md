# IC/IM v1.4 修补及多智能体对抗审计 · 最终 r3

原 7 项缺陷（3 P1 / 4 P2）及追加发现的同源开盘价身份、服务端排队时钟、独立采集器预检时钟问题均已修复，并独立复验。**本轮无遗留已确认问题。**

本报告审计对象是基线 `58986fda2c17df93eb3925bf09a1cebd55a7869e` 上的本地工作区补丁，精确源码 SHA-256 由 `docs/ic_im_daily_release_20261023_repairs_v3_r3_manifest.json`、`final_verification.json` 固定。当前环境不能创建 `.git/index.lock`，因此尚未创建修补提交；不将这些结果宣称为一个新的已提交版本。`commit_repairs.ps1` 会先验证 HEAD、每个文件及暂存区字节，再创建本地提交，不推送。

| 最终验收 | 结果 | 回执 |
|---|---:|---|
| 累计回归 | 3333 通过；原 22 项单列不变 | cumulative_02/receipt.json |
| 60 秒日报门禁 | 1193 通过；原 5 项单列不变 | daily_gate_03/receipt.json |
| 独立对抗审计 | 148 通过 | independent_rollup.json |
| 保存的真实原日证据恢复 | 5 场景通过 | real_recovery_02/receipt.json |

上述最终测试均为 0 失败、0 错误、0 跳过。累计回归绑定 1178 个文件，日报绑定 175 个文件，运行前后相同；独立审计的源码也逐一核对到最终版本，见 `independent_final_source_check.json`。原排除登记表未改变，没有通过增加排除项让测试通过。

| 缺陷 | 已实施修补 |
|---|---|
| STRAT-01 | 实际需要衰减判断时，已有卖 Put 必须有当日、同合约、有限正值价格；主源落后尝试备用源。纯策略与按持久锚点投影的 StateStore 再检查；保留到期、已 pending、未确认成本及历史合法分支。 |
| MKT-01 | 腾讯完整唯一变量及内嵌代码校验，东财返回代码校验；异常主源允许合格备用源。 |
| MKT-02 | 供应商与消费边界核验完整日期、时段、推进中的观察时钟；拒绝未来及午夜报价，保留午休及跨收盘完成的正常查询。 |
| EV2-01 | 采集、旧归档投影、直接收盘/开盘验证共用完整严格变量解析，不再从伪前缀中截出合法身份。 |
| S-V2-1 | 服务端采集时钟覆盖锁等待；后台工作线程实际启动时取时。独立采集器同样覆盖账本读取、哈希核验等预检耗时。 |
| S-V2-2 | 同一锁内安装自身最新持久账本锚点，成功、异常和取消均恢复先前全局锚点。 |
| DELIVERY-V2-01 | 展示使用最新核验 head，同日重算继续保留前序计算锚点；失败快照不再少显示一天。 |

4 组实现后，由独立复现组和其他作者交叉审查；完整独立报告见 `independent/report.md`、`audit_strategy_state/record.md`、`audit_market_evidence/report.json`、`audit_server/report.md` 和 `audit_server/collector_timing/final_report.md`。

真实数据验证使用保存的 2026-10-22 原日包及真实账本只读副本。显式恢复和自动恢复均从序号 19 推至 20，仅写隔离副本；正常恢复无外部 HTTP，缺 IC 包、破坏 blob、错误证据目录均原子拒绝。IC 209 项、IM 224 项信号字段及完整产品状态与修复前已验收结果完全一致，仅排除 fetch_time / signal_calculation_started_at / signal_generated_at 三个计算时钟。详见 `recovery_comparison.json`。

失败证据均保留：首次日报门禁 35 个失败揭示目标新腿误判及旧合成素材缺字段；修复误判并显式补足素材契约后通过，未删断言或随机样本。同源开盘解析及两类时钟遗漏均有修前失败、修后对照。`cumulative_01` 的 3331 通过属于上一轮源码，最终验收使用新增采集器修补后的 `cumulative_02`，没有混用回执。

正式 runtime、历史 outputs、冻结旧规格及上一轮审计证据保持不变，核对详情见 `final_verification.json`。IC 无 Call、IM 当前版本规则、30% 绩效分母均未修改。IM 缺旧行的验证覆盖真实 adapter/策略边界，完整 public builder→StateStore 缺当日 mark 拒绝路径实测为 IC，不宣称已经验证真实 IM 供应商端到端故障。

原有本地只读采集器已用新的 29 文件不可变 pin 恢复，启动回执为 waiting_for_close，未发消息、未请求续账；见 `delivery/collector_restart.json` 和 `collector_first_receipt.json`。这不是远端 Poe/GitHub 自动化发布，也不代表实盘授权。
