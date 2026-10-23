# 免费源接入、真实 IC 原日回放与只读采集交接

本目录对应 2026-10-22 的本地修复。正式账户仍停在 2026-10-21 / sequence 19；研究隔离副本的 2026-10-22 / sequence 20 不可复制回正式账户冒充实盘核验。

## 读取结果

- 测试：../accepted_02/receipt.json、regression.xml、collection.json；原有 22 项单列清单见 ../exclusions.json。
- 真实 IC 验收：../real_03/receipt.json、facts_comparison.json、isolated_original_record.json；原包和原始响应在 ../real_03/evidence。
- 生产采集器已取得完整 IC 四个月份输入：../collector_02/receipt.json（其中 IM 未完整，整轮仍为 NOT_ACCEPTED，不能覆盖其失败记录）。
- 回放契约：../../../docs/ic_im_free_source_replay_contract_20261022_v1_spec.md。

真实验收使用当前独立核心 510500P2612M06500 / security ID 10012149 / 15 张。保留 historical_replay，原日结果先通过正常资格判断并写入隔离账本。回放 EvidenceUnavailable 为零、底层 HTTP 请求为零，原包 URL 集合非空，回放请求集合是原包集合的子集；全部原日事实字段严格一致。仅三个计算时刻、明确的 generation timing 转换和经过独立核验的回放审计信息按契约单独检查。完整原始差异也保留，不称整个字典逐字段相等。

## 采集与恢复

- 正式账本只读路径：D:\动量策略\IC和IM滚动套利\runtime\ic_im_v1_4_r1。
- 新原日证据路径：D:\动量策略\IC和IM滚动套利\data\icim_v14_original_inputs。
- collector_environment.env 给出后续查询/补录进程必须采用的 ICIM_EVIDENCE_DIR。现有服务若仍用默认 state/input_evidence，不会自动看见新目录；本次不假定远端 Poe 已更新。
- start_readonly_collector.ps1 用当前 Python 启动隐藏本机进程；源码由 capture_source_pin.json 的 SHA-256 固定。进程每五分钟检查，完整桶不重复采集，缺包再尝试，重叠进程由文件锁拒绝。仅收盘后采集。
- 查看 launch_receipt.json、collector_pid.txt，以及证据目录 capture_receipts/YYYY-MM-DD/*.json；status=incomplete 明确表示不完整。manifest/原始报文保持严格冲突与完整性检查。
- 不发消息、不生成正式订单、不追加正式账本。主机需要持续开机。当前没有建立重启后自启或云端常驻保证；标准任务计划查询权限受限。
- 如需停止，仅核对 launch_receipt.json 与当前进程后停止记录的 collector PID；不要按所有 Python 进程批量终止。
- 任一被固定的源文件变动，进程会以 source_pin_mismatch 停止，必须先审查新版本、重建 pin 并重启，不能绕过固定校验。

## 未结边界

1. 2027 年交易所官方日历仍需正式通知，未伪造补齐。届时补日历和 oracle，再用逐日完整原日证据走 historical_replay；没有证据的日期仍可能需要人工锚定。
2. 已验收的是真实正常持仓路径，不是未来真实月度维护日。四个挂牌月份预取覆盖已经实采过；11-20 维护与后续每日包必须继续验证完整性。
3. 免费源和当前代理仍可能传输失败；单次采集不保证成功。45 秒是品种配置预算，第三方固定超时不能宣称绝对墙钟上限。
4. 本轮没有重算历史 NAV。历史估值公式和保护的历史产物不变；此前补齐历史日历曾改变 IC 历史回测，因此不能将本轮边界描述扩大为“累计五轮对历史回测零影响”。

部署实测记录：第二次进程 PID 14116，28 文件 pin；首次进程因漏绑依赖已停止。实际 IC 包完整；IM 复核若未补齐，deployment_acceptance.json 明确记为 RUNNING_IC_COMPLETE_IM_RETRYING。第二个采集进程实测以退出码 3 拒绝启动。

2026-10-22 18:41:08 更新：PID 14116 第二轮自动重试结束，IC、IM 均 captured，MO 复核缺口已补齐。第一轮 incomplete 记录保留。最新机器验收为 RUNNING_CAPTURE_COMPLETE；此前重试中状态仅为阶段记录。
