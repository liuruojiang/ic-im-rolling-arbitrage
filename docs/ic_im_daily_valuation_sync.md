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

### 2026-09-08 中债自动输入追加（取代上段国债仍冻结的现状）

用户随后明确要求补上GitHub自行抓取国债数据。18:00日报的同一次运行在生成信号前，调用 `ic_im_chinabond.py` 读取中国债券信息网官方“中债国债收益率曲线”的10年期限，网页百分数除以100作为模型小数值；不得取商业银行债、财政部两位舍入曲线或其他期限。官方页面 https://yield.chinabond.com.cn/cbweb-pbc-web/pbc/more?locale=cn_zh ，日终约17:30发布。

- 最多3次请求，单次连接/读取超时5/15秒、间隔2秒，整个取数步骤硬超时90秒。结果写入当次 `strategy-artifacts/chinabond.json`，运行前移除同一路径残留文件；不依赖本机、VIP或额外凭据。
- 通过 `ICIM_CHINABOND_SNAPSHOT_FILE` 输入计算。源日期必须等于当前信号日；不允许未来、过期、未知单位或17:30前使用当天日终数据；历史补账不套用今日利率。
- ERP和IC Put/IM Call的IV、Delta定价共用同日选定利率，不修改Black-Scholes模型、策略阈值、股息或相对估值阈值。原历史代理重放与已签名账本不追改。
- 未取得、未更新、格式/单位错误、超时或源日期不匹配时，继续使用原2026-08-14冻结利率1.6964%，**不是上一日缓存利率**；正文明确写“国债利率回退／非当日利率”、原因、使用日期及源日期。不能因为PE/PB是真实值就把所有估值输入标为真实。
- 本地普通查询不主动增加网络请求；可显式运行同一取数入口并指定快照环境变量。GitHub正常18:00路径已自动执行取数。未部署Poe远端、未改16:00VIP任务，也不额外发邮件。
- 验证：`test_ic_im_chinabond.py` 和既有量化/日报门禁。无邮件云端验收沿用 `icim-valuation-smoke.yml`，须指定 `expected_bond_date` 为要核验的已完成交易日。取数成功不等于当日完整邮件已经发出。
- 本次追加的可恢复提交：策略 `3bbedf19c1a959ddc6d794e210998b3f97bab7d5`；自动化 `6c53261ba47c46af64b8dbbacbff7581968426ec`。修改前相关已跟踪文件均干净；恢复代码时不得倒退账本。

公开账本只增加来源模式、日期、原因、哈希等provenance，不携带VIP PE/PB原始值、下载文件或凭证。Markdown日报和Gmail HTML/文本正文均醒目标注来源。

## 验证与恢复

- 改动前策略可恢复提交：`14f32700f31dd2b1820a4c0598d96402361896d8`；自动化仓库：`f8a5a1e1af9453a48929a7d7011ded4a9c237933`。改动前相关已跟踪文件均干净，其他用户研究文件不纳入提交。
- 边界测试：`test_ic_im_daily_valuation.py`；既有账本、日报、行情门禁测试不取消。
- GitHub `ICIM valuation input smoke check` 是手动无邮件验收，只确认Secret可读取及真实/回退分支；不会写账本或发邮件，不能当成18:00完整日报成功证明。
- 回滚数据接入可移除Secret环境变量并停用本地快照输入，仍需保留披露。若撤回代码，用上述提交中受影响文件的版本制作明确回滚提交，不倒退或重写已有账本。
