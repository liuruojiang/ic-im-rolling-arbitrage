# 日历变更归属、ETF 到期 oracle 与存证隔离复核

2026-10-22（Asia/Shanghai），本地完成。本轮明确区分“打包版本累计改动”和“单轮新增改动”：历史日历扩展是生产行为变更，发生于此前单独验收的历史日历修复；本轮新增的生产改动则仅是独立采集的一致性/完整性分类。

## 1. 日历扩展的准确归属

2022—2025 年日历由 quant_research_runs/20261022_historical_calendar_repair_v1 单独预注册、备份、修复并以 accepted_02 的 2738 项测试验收。其 docs/ic_im_historical_calendar_repair_20261022_v1_spec.md、delivery/source_delta.patch、receipt 和完整账户重算均保留。

该轮修后的 bot SHA-256 为 0075c16aec68cb56b49569d03c1b89bb2707873fbd309971461082b623d0d19f。上一轮8项续审补测的验收记录与本轮当前 bot 都是同一哈希。因此，上一轮“未改生产逻辑”只表示相对历史日历修复后的起点无新增生产修改，不能概括整个累计交付版本。部署清单必须单列此前日历变更。本轮没有再次修改年份/休市集合。

历史影响已在那一轮单独重建：2402 到期顺延到 2024-02-19，修正 IC 漏维护，19 个目标日受影响；统一口径 IC 全期理论年化从35.3924%改为34.8145%，IM逐日收益不变、年化29.9331%。本轮未重新调参或重写这些结果。2026 全年交易会话和 DATA_CUTOFF 后 OHLCV 校验不因本轮改变，2027仍未加入覆盖。

## 2. 510500/2301 的独立官方 oracle

已有测试虽然断言 2023-01-30，但此前48个“已观测期货到期日”不能充当 ETF 期权 oracle。本轮另存 tests/fixtures/icim_sse_2301_expiry_20261022.json，期望日期直接来自以下已打开核对的上交所官网公告，不由日期函数产生：

- [上证公告(衍生品)〔2022〕73号，2022-12-09](https://www.sse.com.cn/assortment/options/disclo/update/c/c_20221209_5713793.shtml)：所有标的2301期权的最后交易日、行权日、到期日从1月25日调整为1月30日，交收日为1月31日。
- [上证公告（衍生品）〔2023〕5号，2023-01-18](https://www.sse.com.cn/assortment/options/disclo/update/c/c_20230117_5715310.shtml)：明确包括500ETF期权，最后交易日、行权日、到期日为1月30日。

新用例将510500/2301对应的 _fourth_wednesday(2023,1) 与该独立公告期望值比对。fixture 是带来源的规范化事实，不冒称原始HTTP正文；期货48个 oracle 与期权公告 oracle 分开标注。

## 3. 重复报文的真实调用链与本轮生产修复

专家所述“raw_response遇到不同重复报文就抛错”与当前代码不符：raw_response负责保存；歧义拒绝位于 EvidenceReader.http_response 的回放读取阶段。CaptureSession.call 对同一 role/key 复用已存结果，ic.chain 的键含完整年月和交易日。新增测试在同一会话中依次运行既有链、独立核心 Put 叠加、卖 Put 身份核验三个真实函数，确认只发生一次 SSE 链 HTTP 请求。

原日独立 capture_daily 在官方已覆盖的实际当日、交易日、收盘后才启动。收盘前和周末不会打开原日采集会话。正常 build_live_trade_signal 查询自身不打开独立采集会话；服务端独立采集返回结果后，才另行查询/补录。通用 capture_session 容器也供后日 Fear/开盘价补证使用，不能笼统声称所有补证会话都只在收盘后启用。

**不假定收盘后 HTTP 报文必然稳定。** 同链复用降低重复取数，但不能代替稳定性证明。修前反例发现：若独立采集确实取得同URL/参数的不同响应，包仍可能标成完整，直到读取原始 HTTP 时才拒绝。focused_before 的20项中5项失败，原始失败保留。

本轮唯一生产文件修改为 ic_im_evidence_adapter_v1.py：独立采集结束、写入完整性 annotation 之前，新增 _record_http_conflicts(session)，按 URL/参数分组，检查正文SHA、状态、编码及保留的响应头是否一致。不同则记录 raw_http_conflict、resolved=False，IC存证标为 incomplete，提供可重试/恢复风险提示；不因这个一致性问题向正常查询抛错。相同报文允许复用；回放仍拒绝歧义，不回退到当前数据。

body变化、time字段刷新、响应头、状态、编码变化五类均验证“存证不完整、正常IC信号仍可返回”；独立存盘异常也验证这一隔离。其他行情格式、身份、下载预算及日历校验没有放宽。不会把无效行情或缺官方日历冒充正式目标。

## 4. 真实完整IC包验收标准

接受专家标准：独立核心 Put、保留 historical_replay、包括被捕获尝试在内的 EvidenceUnavailable=0、回放请求地址集合为原日集合子集，且与原日落账信号逐字段相等，只有事先明示的时间戳例外。

另交付 replay_acceptance.py 作为严格字段比较工具。目前只例外根字段 signal_generated_at；价格、数量、动作、原因、来源、market_date、执行日、完整性及新增/缺失字段均检查，不能静默忽略差异。工具可读完整账本JSON中的 signals.IC，或单个IC信号JSON，并将差异列出、以非零退出码拒绝不相等：

```powershell
python -X utf8 -B quant_research_runs/20261022_calendar_capture_review_v1/replay_acceptance.py --original original_record.json --replayed replayed_ic.json
```

该工具只验证保存值相等，不能单独证明原包真实、独立核心持仓、HTTP地址及零未命中。真实包尚未取得/运行，不能宣布通过；若回放新增 original_evidence 元数据、来源说明或 generation_timing 等字段不同，严格比较会如实报告，不能先放宽成“都忽略”再宣布相等。上一轮合成回放仍只证明机制。

## 5. 验收与保护

- 新增20项：修前15通过/5失败；修后20通过。
- 累计 accepted_01：93文件、2766通过，0失败/错误/跳过；原第五轮19项、上一轮8项和本轮20项均包含。原22项单列字节保持，无扩大排除。
- 978项源码、规范、夹具、配置及研究输入绑定前后一致；收集节点、XML、退出码和日志核对通过。
- 修前5份备份和8912个既有保护对象核对；旧文件预期变化仅 adapter 与登记表。本轮开始时实际 runtime 全路径集合已单独保存，当前156文件（143JSON）路径及字节一致；没有沿用上一轮遗漏既有journal的155路径清单。
- bot日历、旧官方原始输入、冻结规范、旧测试/回执、已交付历史账户及绩效结果未覆盖。仅本地修改，未部署、发日报或写正式账本；没有新增多智能体或监控任务。

2027官方交易所日历、真实完整IC原日包和2026-11-20收盘前部署仍未闭合。使用方应在部署沟通中获知：11-20维护可能起即进入跨年日历诊断，持续时间取决于官方安排及证据完整性，不能承诺几天自动恢复。部署只能保住后续原日证据；补齐官方日历后仍须每个缺口交易日证据完整，并逐日 historical_replay，不能跳日或用当日链补造过去。

来源归属机器核对见 delivery/calendar_provenance.json；生产精确增量见 delivery/source_delta.patch；最终保护和验收绑定见 delivery/final_verification.json。有效累计回执仅 accepted_01/receipt.json。
