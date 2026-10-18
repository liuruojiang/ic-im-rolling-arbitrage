"""Apply the user-authorized ICIM-only task override; keep cadence and microcap."""
from pathlib import Path
import hashlib
import json
import re
import time
import tomllib

HERE=Path(__file__).resolve().parent
target=Path('D:/Codex/home/automations/ic-im/automation.toml')
before=target.read_text(encoding='utf-8')
data=tomllib.loads(before)
(HERE/'backups/automation.toml').write_text(before,encoding='utf-8')
section='''

2026-10-16 用户授权的Put执行与日报交付闭环覆盖（仅IC/IM，覆盖旧435bbfd固定版本要求；微盘全部规则和配置保持）：
正式策略PR50已合并，执行SHA=94a34d7552f52fc06e58ad26ee560969097658d3。每次启动仍先读notes/mainline_registry.md及本轮docs/ic_im_put_delivery_completion_20261016.md；以后经正式发布的新版本按当次远端main生产workflow固定pin及runtime逐文件SHA核验，不能永久锁在旧SHA。两个新根依赖ic_im_put_execution_v1.py与ic_im_option_maintenance_notice.py必须随正式代码分发，不允许缺模块或dirty未验证源码绕过门禁。fix11三身份、schema4/r1及既有月度收盘维护/结算恢复规则保持。
本地IC/IM累计专项在原33文件基础上增加test_ic_im_put_execution_repairs_20261016.py、test_ic_im_option_maintenance_notice_20261016.py、test_ic_im_put_delivery_completion_20261016.py，共36文件；保持60秒硬限、隔离测试state、迁移0、短TEMP/TMP、-B/no:cacheprovider、零失败错误跳过。生产仍原runtime路径和ICIM_REQUIRE_MIGRATION=1。此次865通过只是阶段证据，不固定未来数量。
日报消费者必须从当次automation远端main固定SHA取得完整依赖集：build_ic_im_v1_4_digest.py、ic_im_v1_4_put_display.py、ic_im_v1_4_delivery_contract.py、ic_im_put_execution_contract.py、ic_im_option_maintenance_notice.py、check_ic_im_v1_4_delivery.py及prepare_ic_im_v1_4_marker.py；以实际导入完整性为准补足其他既有模块。不得只下载单个打包器。标题、纯文本和HTML分别列独立开盘事件、卖Put待核验成本和月度提醒，不用候选收盘价充当成本。
月度Put在前一实际交易日收盘及维护日盘中突出提醒，HOLD不免除维护；具体新合约及数量由用户在维护日盘尾按当时行情和策略自行判断，当天收盘完成，不推到下一开盘。提醒使用官方calendar校验真实维护节点，报告/逐腿回放任一历史均用历史措辞；缺信号时仅显示当次可信日历，不能显示旧目标。
先通过最近成功生产工件整体恢复或官方严格续接追平最近完成交易日；恢复前备份当前完整合法链，拒绝摘取另一分支单日拼接。盘中快照不写账本。直接网络不可用时，可通过已授权GitHub connector更新automation仓库docs/icim-readiness-requests/current.json，填当次北京时间date、mode=realtime、scope=verified_research_handoff_no_send及export_handoff=true，触发现有隔离无发送readiness（只main有效，必须在当日有效时段）。在有效窗口内核实实际strategy/consumer SHA、最近完成账本、两产品当日完整行情及run步骤后，只读接收ICIM_HANDOFF数据，严格验证ZIP SHA与逐文件SHA/迁移/完整链。此路径明确标为本地任务发起的云端计算，不冒称本地重新联网；不调用SMTP发送或改变intent/marker。未获得当日完整新产物则BLOCKED，不能用旧成功补齐。
本地14:15与云端20:00调度、原有限重试、15:00实际发布门禁和发送权限不改；本地不另行SMTP。正常去重保持，不重发昨日成功邮件。代码CI、无发送报告/认证、正式SMTP发送、INBOX收件分别验收，不能混称。
'''
assert '2026-10-16 用户授权的Put执行与日报交付闭环覆盖' not in data['prompt']
replacement=json.dumps(data['prompt']+section,ensure_ascii=False)
after=re.sub(r'^prompt = .*$',lambda m:'prompt = '+replacement,before,flags=re.M)
after=re.sub(r'^updated_at = \d+$','updated_at = '+str(int(time.time()*1000)),after,flags=re.M)
check=tomllib.loads(after)
for key,value in data.items():
    if key not in {'prompt','updated_at'}:assert check[key]==value
staged=HERE/'automation.updated.toml'
staged.write_text(after,encoding='utf-8')
target.write_text(after,encoding='utf-8')
assert tomllib.loads(target.read_text(encoding='utf-8'))==check
(HERE/'local_task_update.json').write_text(json.dumps(dict(path=str(target),strategy_sha='94a34d7552f52fc06e58ad26ee560969097658d3',
    before_sha256=hashlib.sha256(before.encode()).hexdigest(),after_sha256=hashlib.sha256(after.encode()).hexdigest(),
    schedule_preserved=True,microcap_clauses_preserved=True,status=check['status']),indent=2),encoding='utf-8')
print('Local ICIM task prompt updated and read back; cadence, status and microcap clauses preserved.')
