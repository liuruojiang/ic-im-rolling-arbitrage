# 独立日报展示对抗审计

审计仅使用已存档生产账本/云端fixture并注入异常，未修改生产代码、账本或发送邮件。

复现命令：`python -X utf8 -m pytest quant_research_runs/20261016_fear_delivery_adversarial_v2/rendering/test_rendering_audit.py -q --tb=short -p no:cacheprovider`。

首轮结果：2项失败、3项通过。失败断言是需求反例，不是环境故障。

## 尚未满足的行为

1. **P2：行情/账本失败后，官方失败日报完全漏掉9圈参考。** `run_ic_im_v1_4_github_digest.py:570-583` 只尝试 `fetch_score`；该函数不采集9圈，因此即使9圈在线，失败result、云端正文和HTML也没有9圈。本反例将9圈接口模拟为可用，观察到调用次数为0。证据：`failure_runner/result.json`、`failure_runner/mail.txt`、`failure_runner/mail.html`。
2. **P2：Poe单品种信号失败时，同时漏掉9圈和百分位来源提示。** `poe_ic_im_mainline_v1_4_bot.py:6823-6837` 的独立恐慌观察只输出数值、日期、status；未请求9圈，未输出 `source_disclosure`。当CSV有效、网页失败、其他行情失败时，Poe显示百分位观察值却没有“未完成双侧验证”。证据：`poe_failure.md`。

最小修复：在上述两个failure路径分别独立尝试 `fetch_jiu_reference(signal_day)`；无论百分位是否成功均合并9圈结果；Poe异常展示追加 `source_disclosure` 与 `reference_disclosure`。参考取数异常应显示不可用，不再引发整个报告失败。云端failure renderer本身已有可接受的9圈渲染能力，只缺上游字段。

## 独立通过项

- 真实云端成功fixture补入9圈参考后，纯文本/HTML均列2.28、真实日期、官网和“仅供参考，不参与网格信号”；输入signal和payload均未被改写。
- 真实本地seq15账本注入百分位无值时，stored close report显示网格 `UNCONFIRMED`、合计 `N/A（未确认）`，并继续展示9圈；原始账本对象不被修改。
- 原始未附参考字段的旧云端fixture，现有纯文本和HTML与修改前归档renderer逐字节一致。

无需因上述展示缺口推翻已通过的信号隔离或历史兼容验证，但在修复并重跑前不能声称所有正常及失败日报路径均已完全处理。

## 修复后终验

主代理修复两个failure采集/展示缺口后，未修改原测试断言重跑：**5项全部通过**。终验使用 `-p no:cacheprovider`，唯一警告为现有fastapi_poe/Pydantic弃用提示，不影响结果。

首次失败的 `results.xml` 保留不变，终验结果另存 `results_after_fix.xml`。同一复现测试生成的 `failure_runner/`、`poe_failure.md`、成功/partial渲染样本现为修复后的最终输出，已包含9圈以及百分位单侧验证提示。
