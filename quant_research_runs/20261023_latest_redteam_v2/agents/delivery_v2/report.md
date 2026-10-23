# 日报及服务读取独立对抗审计

固定代码基线：`58986fda2c17df93eb3925bf09a1cebd55a7869e`。本分支确认 1 项 P2，未修改生产源、原测试或原账本，未联网、发邮件、部署。源文件逐字节与提交一致见 `source_pins.json`。范围是本地 v1.4 server/runner 的查询展示与失败路径；不推断远端现状。

## DELIVERY-V2-01 / P2：非交易时段查询及收盘失败兜底漏掉最新已核验记录

**根因位置：** `poe_ic_im_v1_4_server.py:112–145`。`_record_for_display` 在非集合竞价/盘中/午间且 latest 已覆盖最近完成交易日时，返回 `sequence-1`；`execute_query` 对全部查询统一安装该记录的运行锚。该退一条对于同日收盘重新计算有合理用途，但纯快照与计算失败后的兜底也继承了它。

**真实可达调用：** 开盘前 `execute_query("当前持仓", now)` → `ICIMMainlinesBot._handle_snapshot`（bot:8271–8282）→ `_write_last_verified_snapshot`；收盘后 `execute_query("信号", now)` → `_handle_signal` 的 builder 异常分支（bot:8313–8347）→ 同一兜底渲染。展示读取的锚已是倒数第二条。

固定输入为现有真实 `runtime/ic_im_v1_4_r1` 的只读复制，保存于本目录 `fixture_ledger`；`StateStore` 实际验证其完整哈希链。latest 为 sequence 19、verified_day 2026-10-21，SHA-256 文件清单见 `fixture_manifest.json`。每例再复制到独立临时状态目录。真实 coordinator/store/bot/renderer 均执行；仅收盘故障例将信号 builder 注入明确异常。全部 HTTP 调用被测试阻止。

| 北京时间 / 查询 | 应显示的最后已核验日期 | 实测日期 | 结果 |
|---|---|---|---|
| 2026-10-22 08:00 / 当前持仓 | 2026-10-21 | 2026-10-20 | 失败 |
| 2026-10-22 09:10 / 当前持仓 | 2026-10-21 | 2026-10-20 | 失败 |
| 2026-10-22 09:30 / 当前持仓 | 2026-10-21 | 2026-10-21 | 正向对照通过 |
| 2026-10-22 14:30 / 当前持仓 | 2026-10-21 | 2026-10-21 | 正向对照通过 |
| 2026-10-21 18:00 / 信号，来源失败 | 2026-10-21 | 2026-10-20 | 失败 |

各例 IC/IM 均如此。最终断言前已验证 `observed == {}`、无附件且账本 latest digest 不变；因此是只读展示错误，不是已证实账本污染。实际渲染文本保存为 `snapshot_*.md`。错误把较旧的状态称为“最后逐腿核验快照”，隐藏实际最新核验状态；若最新日持仓变化，读者会看到过期仓位。报告仍标注实际旧日期且没有伪造当天新目标，不夸大为实盘损失或新目标误发。

**最小修补建议：** 在 coordinator 锁内区分计算锚与展示锚：同日收盘重算保留 sequence-1；纯快照和计算失败兜底显式读取/渲染 latest。不要整体移除回退。若使用临时锚切换，应在 finally 恢复计算上下文，以免两产品计算相互污染。回归应覆盖开盘前、集合竞价、盘中、午间、收盘失败、非交易日、sequence 0，并保护同日重算不重复状态迁移及失败不落账本。

## 可复现证据

最终测试：`test_delivery_confirmed.py`，固定 fixture，无选择性 deselection。`run_04_confirmed/regression.xml` / `run_04_confirmed/regression.txt` / `run_04_confirmed/test_run.json`：**3 failed、2 passed、0 skipped，5.77 秒，exit 1**。3 个失败是上述 1 个根因的反例，不代表基础回归整体失败。worker 仅对 Windows Python 3.14 mkdir(mode=0700) 做已有测试适配，独立临时状态，不更改生产权限检查。

仓库根目录 PowerShell 复现：

```powershell
$env:ICIM_TEST_OUTPUT_DIR = Join-Path (Get-Location) 'quant_research_runs/20261023_latest_redteam_v2/agents/delivery_v2/run_reproduce'
$env:ICIM_STATE_DIR = Join-Path $env:ICIM_TEST_OUTPUT_DIR 'state'
$env:ICIM_REQUIRE_MIGRATION = '0'
$env:ICIM_DISABLE_EVIDENCE_CAPTURE = '1'
$env:ICIM_DISABLE_INTERNAL_REFRESH = '1'
python -B -X utf8 _icim_delivery_test_worker.py quant_research_runs/20261023_latest_redteam_v2/agents/delivery_v2/test_delivery_confirmed.py
```

运行 worker 会自行为状态创建隔离临时目录，详细路径记录在 test_run.json。

## 撤销与边界

- 跨 15:00 的初始怀疑已撤销：`docs/ic_im_fear_delivery_adversarial_20261015.md` 明确允许合格盘中采集后报告稍后完成；已有 `test_adversarial_before_1500_observation_can_finish_report_later` 覆盖。run_01 的两次失败来自错误预期，不列为缺陷；探索文件保留以供审核。
- 本地 runner:472–475 展开完整参考字典可以增加字段白名单防御，但实际 `fetch_jiu_reference` 只产生九个 jiu 字段，未找到原始响应任意键穿透。仅恶意 mock 能覆盖字段不能证明当前可达 bug。云端 PR162 已修事实保留；不把本地防御增强冒充远端回归。
- 复查测试 gate、版本清单、发送去重相关路径，未在本分支形成额外实证缺陷；不宣称穷尽覆盖。未调用任何真实发送入口。
- 历史 run_01 / run_02 / run_03 是探索或旧输入路径证据，最终统计仅采用 run_04_confirmed。
