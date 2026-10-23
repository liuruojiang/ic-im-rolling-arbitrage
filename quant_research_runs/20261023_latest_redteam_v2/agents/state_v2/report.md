# 账本、并发与服务端恢复独立对抗审计

固定提交：`58986fda2c17df93eb3925bf09a1cebd55a7869e`。没有修改生产、既有测试、冻结规格或正式账本；无网络请求、发送及部署。

## 结论

确认 2 项 P2，均位于服务端独立采集入口；未发现新的已证实 P0/P1 账本原子性问题。最终新增 10 个测试中 7 个控制通过、3 个预期安全行为断言失败；3 个失败归于 2 个根因，不按失败数重复计数。

### S-V2-1：采集校验时钟未推进

`poe_ic_im_v1_4_server.py:201-214` 直接调用 `capture_daily`，没有使用 `strategy.collection_clock`。adapter 的 `runtime_clock` 只冻结策略时间，不建立采集单调时钟。`_iv_observation_clock` 在没有上下文时原样返回开始时间。后台循环还在 `catch_up_once` 后复用同一个先前 `now`（364-374）。

反例：开始为15:40:00，供应商更新时间15:40:01，接收时模拟单调时钟已过去2秒；实际CFFEX快照门禁仍以15:40:00判断，误报未来。实际 `LedgerCoordinator.capture_current_inputs → adapter.capture_daily → CaptureSession.finish` 将两个包封为不完整；用相同合成角色和报文，仅在外层加现有 `collection_clock`，两包完整通过。见 `full_capture_context_False.json` 和 `full_capture_context_True.json`。这是明确合成受控实验，不是声称上游真实当日故障已发生。

建议在服务端入口持锁后建立采集时钟上下文，并在后台catch-up完成后重新取真实当前时刻开始独立采集。禁止以放宽未来时间门槛替代。独立 `CaptureRunner` 已有 `collection_clock`，本项只指服务端入口。

### S-V2-2：采集锚点沿用上次查询

`poe_ic_im_v1_4_server.py:209-214` 不从自身store读取并安装当前锚点。`execute_query` 在145-146行安装的是推导当天信号所需旧日锚点；成功append后不会更新全局锚点。adapter 449行用该全局值填写 `anchor_day_at_capture`，491-498行还据此确定优先采集月份。

反例经过实际公共query和StateStore，隔离账本从10-20成功推进10-21，紧接着采集入口实读的锚点仍为10-20，见 `stale_capture_anchor_observation.json`。该断言在采集调用边界观测实际共享值；metadata字段和排序消费由生产源码定位，未伪称完整真实行情重放。

建议同锁内重新读取自身最新head、安装anchors；用finally恢复原全局锚点，复用独立CaptureRunner的既有模式。当前明确影响是证据归属/优先级；因collector仍收集所有实际月份，不声称已经造成错误目标或仓位。

## 已验证控制和局限

- IC或IM单腿诊断标记导致整对拒绝，无任何账本文件变化。
- current记录改sequence或verified_day而未同步哈希，独立哈希门禁拒绝。
- journal已落盘、head替换前故障，在新StateStore实例中重启恢复一次，重复旧current不能再次推进。
- 同样到达时间的快照在正确collection_clock上下文中通过。
- 第一轮8例为2失败/6通过。增加完整封存测试时，测试自己的目录保护断言误把新增隔离证据当作账本写入，两例脚手架失败留在 `scaffolding_results.*`。已把实验evidence_root明确分离，最终3失败/7通过；没有修改生产或旧测试。
- 这些测试使用合成信号和合成供应商角色，真实生产验证器、真实线程池、真实文件封存/状态存储；不属于历史收益重算或真实网络可靠性验收。

复跑：`python -B -X utf8 quant_research_runs/20261023_latest_redteam_v2/agents/state_v2/run.py`。当前代码退出1是预期反例结果。
