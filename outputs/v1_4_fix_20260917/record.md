# IC / IM v1.4-r1 fix1 修复与历史影响记录

## 结果

审查发现的策略时点、恢复仓位、成本基准、行情新鲜度、动作表达、账本身份/互斥和迁移问题已修复，并补充边界测试。构建更新为`v1.4-20260917-r1-coreput3x-fixedshort95-fix1`；策略数学及规则版本不变。

仍有一项外部依赖：仓库没有经核验的官方期权结算/交割结果和整轮含成本恢复凭据生产器。正式调用链会明确标记阻塞并等待，不会从日行情猜测结果。因此修复后的实现可安全失败关闭，但卖Put到期后的自动生命周期尚未闭环。

## 修改

- `ic_im_v1_4_policy.py`：收盘与T+1约束、月维护优先级、恢复仓、有限值、动作一致性、IC核心独立状态。
- `poe_ic_im_mainline_v1_4_bot.py`：IC新鲜报价、历史失败关闭、核心独立报价/配量/重建、IM计划日重建、结算证据状态。
- `poe_ic_im_v1_4_state.py`：新写入身份、合约/到期/有限值、互斥和独立核心状态校验。
- `migrate_ic_im_v1_3_r7_to_v1_4_r1_state.py`：保留IC双袖精确数量并验证迁移后锚点。
- `run_ic_im_v1_4_github_digest.py`：1.4身份和修复构建。
- 新增策略、状态、迁移、调用链和日报回归测试。

## 验证

```powershell
python -X utf8 -m py_compile ic_im_v1_4_policy.py poe_ic_im_mainline_v1_4_bot.py poe_ic_im_v1_4_state.py poe_ic_im_v1_4_server.py run_ic_im_v1_4_github_digest.py migrate_ic_im_v1_3_r7_to_v1_4_r1_state.py
python -X utf8 -m pytest -q test_ic_im_v1_4_policy.py test_migrate_ic_im_v1_3_r7_to_v1_4_r1_state.py test_ic_im_v1_4_state_guards.py test_ic_im_v1_4_integration_guards.py test_run_ic_im_v1_4_github_digest.py test_poe_ic_im_mainline_v1_3_bot.py test_poe_ic_im_v1_3_state.py test_run_ic_im_v1_3_github_digest.py test_ic_im_quarter_roll_v1_3.py test_ic_im_mainline_v1_3_r6_fixed_performance.py test_ic_im_v1_3_properties.py test_ic_im_mainline_v1_3.py test_ic_mainline_v1_3.py test_im_mainline_v1_3.py test_grid_half_release_20260913.py test_im_grid160_half_release_20260914.py --disable-warnings
python -X utf8 outputs/v1_4_fix_20260917/rerun_historical.py
python -X utf8 outputs/v1_4_fix_20260917/compare_historical.py
```

结果：生产模块编译通过；262项测试通过、1个既存第三方弃用警告；历史比较58个文件、119,527行完全一致，最大数值差0。

## 历史影响

修复的是正式信号adapter、持久状态、迁移和报告合同；冻结研究引擎没有导入这些生产模块。使用原入口和原数据快照重跑后：

- IC真实全样本：v1.3年化26.8930%、Sharpe 1.5105、最大回撤-11.0656%；联合方案年化30.3826%、Sharpe 1.8327、最大回撤-11.9365%。
- IM真实全样本、MO Put单边5BP：只做核心Put 3x的年化37.8068%、Sharpe 1.7301、最大回撤-16.2588%；q3联合方案年化38.8036%、Sharpe 1.8107、最大回撤-16.8454%。
- IM真实卖Put仍只有3个周期，最大正向贡献占67.7089%；原样本集中风险不变。

上述IC真实期为2022-09-19至2026-08-14；IM真实期为2022-07-22至2026-08-14。模型期是理论延展。本轮没有新增8月14日后的历史行情，没有改变成本、30%保证金/缓冲、现金利息或账户整数化口径。

## 完整性与边界

风险改动前备份：`.codex_backups/20260917_112337`；登记表备份：`.codex_backups/20260917_112818`。正式runtime保持只读。没有发送、发布、部署或下单。
