"""Finalize the mutable release runbook; never changes frozen specs or state."""
from pathlib import Path
import json
import tomllib

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
doc='''# IC/IM Put修复及日报同步发布，2026-10-16

用户授权修复执行、成本、提醒和消费者全部已知问题并同步远端。本轮已正式发布并验证就绪；今天14:15定时任务、20:00正式邮件及INBOX送达须在实际发生后分别登记，不能以无发送预检替代。IC不含Call；第3项维护日收盘换仓和第4项结算/恢复规则保持。

## 本地14:15任务必读执行覆盖

本节依据用户本轮授权，覆盖原任务文本中IC/IM旧435bbfd固定版本及旧测试文件数；原任务的微盘全部条款、ACTIVE状态、工作日14:15、15:00实际发布门禁、有限重试及本地不另发SMTP均保持。冻结规范、首次研究输出与旧账本不回写。

每次先读取登记表、既有系统规范及record，再解析远端自动化main中 `.github/workflows/ic-im-v1-4-daily-digest.yml` 唯一固定的策略SHA，固定该次自动化消费者SHA。完整checkout后，以该策略Git tree逐个核对全部根Python文件、Git blob及SHA-256，建立 `formal_runtime/release_manifest.json`。不要因共享目录有同名文件便导入该文件；以后生产pin变化须重建并核验快照。

必需本地门禁为 `docs/ic_im_v1_4_script_delivery_sync_20261013_source_manifest.json` 原33文件，加以下5文件，共38文件：

- `test_ic_im_put_execution_repairs_20261016.py`
- `test_ic_im_option_maintenance_notice_20261016.py`
- `test_ic_im_put_delivery_completion_20261016.py`
- `test_ic_im_fear_source_delivery_20261016.py`
- `test_ic_im_fear_delivery_adversarial_20261016.py`

使用本轮证据目录的 `validate_formal_release.py` → `run_formal_checks.py`，外层真实60秒子进程硬限，独立临时state、测试迁移0、零失败/错误/跳过；生产迁移门禁保持1。Windows mkdir兼容仅作用于隔离测试进程。保留XML、完整输出及实际源SHA，不把不同阶段计数相加冒称单次全量。包括 `ic_im_put_execution_v1.py`、`ic_im_option_maintenance_notice.py` 和恐慌来源模块在内的所有依赖必须完整分发。

本地消费者从当次远端自动化固定SHA取得完整模块与实际import依赖，至少包括 `build_ic_im_v1_4_digest.py`、`ic_im_v1_4_put_display.py`、`ic_im_v1_4_delivery_contract.py`、`ic_im_put_execution_contract.py`、`ic_im_option_maintenance_notice.py`、`check_ic_im_v1_4_delivery.py`、`prepare_ic_im_v1_4_marker.py`。记录原始字节与LF规范化源码哈希，标题、Markdown、HTML及原附件一并验收。

生产账本必须来自最近成功正式生产run的 `ic-im-v1-4-r1-ledger` 工件，独立核验main、成功、未过期、生产SHA、迁移证明、完整哈希链、官方连续交易日及最近已完成交易日。先备份完整合法本地分支，在真实生产写锁内整体恢复；不得摘取另一分支单日拼接，不能手工写最新日期。需要续接时只用官方严格收盘续接入口。盘中IC/IM当天完整行情缺一则BLOCKED，盘中快照不写账本。

直接行情网络不可用时，可经已授权GitHub connector更新自动化仓库 `docs/icim-readiness-requests/current.json`，填写本次北京时间date、真实requested_at、`mode: realtime`、`scope: verified_research_handoff_no_send`、`export_handoff: true`。先核验现行readiness workflow具备入口，请求确实触发main新run，并在当日有效窗口内完成；不得复用旧成功run冒充新采集。该路径明确标为“本地任务发起的云端计算”，不冒称本地重新联网。

只接受正式策略/消费者SHA匹配、最近完成交易日账本已恢复、IC/IM当日行情完整、日报与消费者门禁成功的当次run。用job日志 `ICIM_HANDOFF_META_AP` / `ICIM_HANDOFF_CHUNK` 按nibble-ap解码（A–P对应十六进制0–f）；人工可读SHA/END行仅供展示，不能以短值日志脱敏后的字符串验收。使用本轮 `receive_handoff.py` 先核验ZIP字节数/SHA、manifest白名单及逐文件SHA，再核验迁移、全链及交易日。其执行前亦须核验固定源码快照。导出账本digest必须等于隔离恢复时digest，不把临时补记分支提升为正式链。缺任一证明则独立BLOCKED，不展示旧目标为当天信号。禁止使用Python优化模式绕过断言。

月度Put在前一实际交易日收盘及维护日盘中突出提醒，HOLD不免除维护、零配置不新增仓位。以官方月度节点及节假日顺延校验日期；合约和数量由用户在维护日盘尾按当时行情及策略配置自行判断，当天收盘完成，不推至下个开盘。原附件及消费者均分列当前旧持仓和普通月度候选，期货下一交易日目标与Put维护日收盘候选分列，候选收盘估值不能充当卖Put开仓成本。历史回放使用历史措辞，不因提醒生成或解释为获准实盘指令。

本地只输出研究日报、不调用额外SMTP。云端20:00正常去重发送保留；无发送检查仅TLS、认证、MAIL/RCPT、RSET，不执行DATA、不创建正式intent/delivered marker或生产账本工件、不重发昨日成功邮件。代码CI、无发送报告/认证、正式SMTP发送及INBOX送达分开登记。

原 `automation.toml` 受会话文件写权限限制未写入，完整待更新配置存于本轮证据目录 `automation.updated.toml`。既有任务强制读取登记表，登记表链接本文件，故以上必读仓库覆盖规则生效；不得声称原TOML已写入。无需改变原状态、调度或微盘规则。

## 最终发布版本与验收

最终运行策略为 `36ef68aa1e96a1d352d67f60eeb36e3e622bd010`；消费者源码为 `bcbd91a4714d4b649e3a6b26af90789ef605b848`。最终收盘请求提交 `6f0a006cde333e015aa0fe9f372c64345e5c669c` 只改无发送请求文件，消费者字节保持。此后仅发布审计文档/门禁runner，不改变生产运行pin。本次固定快照420个根Python文件、14个完整消费者模块逐文件与远端Git blob及SHA-256匹配。

执行修复包含：先解析前日独立三倍兑现开盘事件、再形成当日收盘目标；卖Put成本只认指定日实际开盘证据、待确认保持空值；季度换仓后的次日退出绑定实际EOD期货合约；持久层独立绑定价格日/成本证据，阻止候选自行证明、提前清空或覆盖信号。月度提醒校验官方节点及报告级历史上下文。

实际快照审计另发现并修复IC候选06500被误显示为旧持仓07500，以及原附件/邮件表将当日Put候选笼统写为下一交易日目标。当前旧合约与候选明确分列，独立三倍计划不替代普通核心保护。Oct09归档标题/正文/HTML哈希保持。

并行恐慌来源任务的正式更新已完整保留并采用：现行 `observation_batch` 使单次报告内IC/IM共享一次真实采集、按日期/mode隔离、逐腿复制、异常/取消清理且下次报告重新采集；语义来源校验比较日期/数值/选择/CSV哈希/质量/回退，动态HTML原文哈希不充当两次采集相同的前提，各自证据仍验证。本地曾拟的另一缓存及诊断实现仅留备份，未发布，未重复部署。

- 本地38文件894项通过，零失败/错误/跳过；外层硬限60秒，实际27.5759578秒。
- 云端CI run38856549180：自动化1107项通过、IC/IM952项通过、微盘成功；不把各范围相加。
- 盘中最终run38856549173：北京时间13:36:29采集启动、13:37:34产出，status=ok、完整IC/IM当日行情、两个维护提醒；Fear同源51.36且CSV/网页匹配。源result原始SHA `f64e1dac2a36253d58e1a49d72f18b38135677871e3bc634a680d33615cd6fa6`，ZIP113897字节、SHA `587a54945617b38cb903bf3fd95ff77ebb9c117f02d8df69ed6ef0f4a140d4db`。
- 收盘最终run38856968370：北京时间13:41:15完成Oct15保存快照重渲染，原附件/Markdown/HTML均正确在表前显示两项Oct16维护提醒；源result原始SHA `4c35ee318110780e9353db739e355769d0391828b3887396dc62cd9a42b675a1`，ZIP115940字节、SHA `c128d25ad95f2f288f3f38621eb442b25d514f5c9ab3ad3166099c248555bc13`。这不代表Oct15原邮件当时已包含新模板，亦未重发旧邮件。
- 两次最终run均通过SMTP TLS/认证/1个收件人/RSET，无DATA、message_sent=false；完整账本交接通过、advanced_sessions=0。独立对抗复核通过，实时样本未触发独立三倍/卖Put开仓，相关分支证据来自专项测试及11笔真实开盘报价。

最近成功正式账本源run38731463272、artifact11970296521；全链seq0..15及迁移验证通过。本地已在写锁内整体恢复，最新seq15/2026-10-15、digest `ea18004690504420ce43a69aad809b005c9663e57a50f6ee78a75b6f631971a3`。原本地seq14分支完整保留于本轮backups及runtime retired目录。当前生产全部账本JSON与两份最终handoff逐字节一致，盘中未续账。

426项保护对象中422项未变，4项为授权整体恢复替换的规范运行分支文件，其原字节均已备份；冻结规范、SHA及首次正式研究输出不变。历史四臂回测维持原记录，11笔真实开盘证据及8条路径7724行独立复算通过。IC CAGR35.4879%→35.3924%、最大回撤-9.0013%不变；IM CAGR30.4005%→29.9331%、回撤-12.6992%→-12.9182%。历史引擎已遵守兑现时序，timing_only=baseline、both=cost_only。绩效使用每倍期货30%保证金/缓冲。

## 证据与历史问题保留

本轮目录 `quant_research_runs/20261016_put_delivery_completion_v1/`，最终摘要 `record.md`，机器回执 `final_delivery_receipt.json`，本地日报 `local_daily_final/local_research_daily.md`，前日收盘复核稿 `local_close_final_current/`，两次最终hand off各自verification.json及form​al_verification回归输出。冻结预注册为 `docs/ic_im_put_delivery_completion_20261016_v1_spec.md` 及其原SHA文件。

策略PR50/51、自动化PR153/154/155构成本轮前序正式修复；并行来源任务随后推进至上述最终版本。初次base64日志传输遭短值脱敏破坏已由A–P传输替代，SHA拒收时未改本地链；run38855821169因两产品来源快照不一致被消费者拒绝、未进入SMTP/handoff。旧local_daily_verified、local_close_final及旧阶段回执只保留历史，不作为最终验收。原始保护备份、未发布备选方案和签名传输URL不得随整目录公开。
'''.replace('form\u200bal_verification','formal_verification')
(ROOT/'docs/ic_im_put_delivery_completion_20261016.md').write_text(doc,encoding='utf-8')
p=HERE/'automation.updated.toml'
obj=tomllib.loads(p.read_text(encoding='utf-8'))
addition='\n最终必读覆盖以docs/ic_im_put_delivery_completion_20261016.md为准：原33测试加执行/维护/完成/恐慌来源/恐慌对抗共5文件，总38文件；每次采用远端生产workflow固定pin和完整消费者，以420根源码/14模块为本次已验证基线，不能把本次SHA当永久最新版。验证脚本为本轮validate_formal_release.py，真实60秒外层硬限。\n'
if addition.strip() not in obj['prompt']:
    old=json.dumps(obj['prompt'],ensure_ascii=False)
    new=json.dumps(obj['prompt']+addition,ensure_ascii=False)
    text=p.read_text(encoding='utf-8')
    assert old in text
    p.write_text(text.replace(old,new,1),encoding='utf-8')
    updated=tomllib.loads(p.read_text(encoding='utf-8'))
    assert all(updated[k]==v for k,v in obj.items() if k!='prompt')
print('mutable runbook and staged configuration finalized; original task config untouched')
