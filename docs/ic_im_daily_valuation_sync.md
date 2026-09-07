# 每日乐咕估值输入与代理回退

2026-09-08 用户授权：每日北京时间16:00由本机Codex读取已登录VIP网页，推送GitHub；缺失时沿用原代理，但日报和邮件明确披露。授权更新并发布日报程序及工作流，不改策略阈值、不额外发送邮件、不下单。

## 数据与调度

- Codex heartbeat：`vip-github`，每日16:00，Edge现有登录会话。电脑、Codex及浏览器连接须可用；登录失效需人工恢复。
- 中证500/1000各取整体PE TTM和整体PB；四个页面必须同日、有限正数，拒绝等权、中位数、缺项和未来/过期数据。
- 本地 `outputs/legulegu_daily_sync/YYYY-MM-DD/web-observations.json` 仅包含 `valuation_date`、`indices`、`source_urls`。格式由 `sync_ic_im_daily_valuation.py` 验证。
- 网页证据单独保留 `web-evidence.md` 或下载证据；使用以下入口生成带时间和证据哈希的快照并上传：

```text
python -X utf8 sync_ic_im_daily_valuation.py --input <observations.json> --evidence <evidence-file> --expected-date <YYYY-MM-DD> --out-dir <local-dated-output> --upload
```

- 上传为 `liuruojiang/codex-daily-automation-probe` 的 Actions Secret `ICIM_LEGULEGU_DAILY_SNAPSHOT`，不是公开Git提交；原始历史VIP文件、账号及Cookie不得上传。上传器允许初次验收使用最近已完成交易日；每日16:00任务须将expected-date设为当天，不能上传旧值冒充当天。
- 本地同时维护 `runtime/legulegu/latest.json`，普通本机日报读取；可用 `ICIM_LEGULEGU_SNAPSHOT_FILE` 显式指定。云端只从Secret环境变量读取。GitHub仍在18:00生成原定邮件。

## 计算和失败边界

`ic_im_daily_valuation.resolve` 按信号日精确匹配，不将昨天或未来快照套入今日，不把16:00快照用于14:30盘中计算。若IC/IM任一字段无效，两者全部回退。无快照、损坏、过期分别在正文披露；不把已有代理账本因稍后上传的真实快照重新标为真实或追改。

真实输入进入原PB/ERP评分和Put、网格目标，不改变评分公式、Put/Call/展期阈值。持久网格状态保留真实值驱动的历史状态，后续缺失日按原代理分数继续迟滞判断，不能从全代理历史重算抹掉已记录状态。

代理锚点仍为2026-08-14，PE/PB乘以最新指数价格/锚点价格。国债收益率、股息贡献、相对估值阈值未在本次更新；即使PE/PB真实，也必须披露这些输入仍冻结。MOM120仍是价格指数口径。行情、成交量、日期、期权链、账本完整性等原失败门禁不放宽。

公开账本只增加来源模式、日期、原因、哈希等provenance，不携带VIP PE/PB原始值、下载文件或凭证。Markdown日报和Gmail HTML/文本正文均醒目标注来源。

## 验证与恢复

- 改动前策略可恢复提交：`14f32700f31dd2b1820a4c0598d96402361896d8`；自动化仓库：`f8a5a1e1af9453a48929a7d7011ded4a9c237933`。改动前相关已跟踪文件均干净，其他用户研究文件不纳入提交。
- 边界测试：`test_ic_im_daily_valuation.py`；既有账本、日报、行情门禁测试不取消。
- GitHub `ICIM valuation input smoke check` 是手动无邮件验收，只确认Secret可读取及真实/回退分支；不会写账本或发邮件，不能当成18:00完整日报成功证明。
- 回滚数据接入可移除Secret环境变量并停用本地快照输入，仍需保留披露。若撤回代码，用上述提交中受影响文件的版本制作明确回滚提交，不倒退或重写已有账本。
