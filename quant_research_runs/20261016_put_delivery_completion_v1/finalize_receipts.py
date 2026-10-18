"""Write the final audit record from already verified, no-send evidence."""
from pathlib import Path
import hashlib
import json
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
def read(path):
    return json.loads((HERE/path).read_text(encoding='utf-8'))
def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
strategy = read('formal_runtime/release_manifest.json')
consumer = read('cloud/source_runtime_manifest.json')
assert strategy['strategy_sha'] == '36ef68aa1e96a1d352d67f60eeb36e3e622bd010'
assert consumer['automation_sha'] == 'bcbd91a4714d4b649e3a6b26af90789ef605b848'
for name, digest in strategy['files'].items():
    assert sha(HERE/'formal_runtime'/name) == digest, name
for name, entry in consumer['files'].items():
    assert sha(HERE/'cloud/scripts'/name) == entry['sha256'], name
xml = ET.parse(HERE/'formal_verification/regression.xml').getroot()
suite = xml.find('testsuite') if xml.tag != 'testsuite' else xml
assert int(suite.attrib['tests']) == 894
assert all(int(suite.attrib[k]) == 0 for k in ('failures','errors','skipped'))
assert len(read('formal_verification/test_run.json')['files']) == 38
assert read('formal_verification/hard_deadline.json')['exit_code'] == 0
evidence = {}
for run, folder in [(38856549173, 'local_daily_final'), (38856968370, 'local_close_final_current')]:
    handoff = HERE/f'handoff-{run}'
    verification = read(f'handoff-{run}/verification.json')
    result = read(f'handoff-{run}/report/result.json')
    assert verification['full_chain_and_migration_verified']
    assert result['status'] == 'ok' and result['advanced_sessions'] == 0
    source = handoff/'ledger'
    for member in source.rglob('*.json'):
        live = ROOT/'runtime/ic_im_v1_4_r1'/member.relative_to(source)
        assert live.read_bytes() == member.read_bytes(), str(member)
    metadata = read(f'{folder}/metadata.json')
    assert metadata['body'].count('月度Put展期换仓提醒：') == 2
    evidence[str(run)] = dict(verification=verification,
        generated_at=result['generated_at'], report_generated_at=result['report_generated_at'],
        advanced_sessions=0, digest_files={x.name:sha(x) for x in (HERE/folder).iterdir() if x.is_file()},
        smtp=dict(tls_verified=True, authenticated=True, recipients_accepted=1,
                  envelope_reset=True, message_sent=False, DATA_used=False))

receipt = dict(status='verified_ready_no_send', strategy_sha=strategy['strategy_sha'],
    consumer_source_sha=consumer['automation_sha'],
    automation_request_only_head='6f0a006cde333e015aa0fe9f372c64345e5c669c',
    root_python_files=len(strategy['files']), consumer_python_files=len(consumer['files']),
    local_tests=dict(files=38,passed=894,failed=0,errors=0,skipped=0,
                     deadline=read('formal_verification/hard_deadline.json')),
    cloud_ci=dict(run_id=38856549180,automation_passed=1107,icim_passed=952,microcap='success'),
    official_ledger=dict(source_run=38731463272,artifact_id=11970296521,
        sequence=15,verified_day='2026-10-15',
        digest='ea18004690504420ce43a69aad809b005c9663e57a50f6ee78a75b6f631971a3',
        full_live_json_byte_equal_to_both_handoffs=True,original_branch_preserved=True),
    handoffs=evidence,
    local_task=dict(actual_toml_written=False,reason='session filesystem write restriction',
        effective_override='mandatory registry -> docs/ic_im_put_delivery_completion_20261016.md',
        status='ACTIVE preserved',schedule='weekday 14:15 preserved',microcap_rules='preserved'),
    normal_delivery=dict(schedule='20:00 Asia/Shanghai preserved',extra_email_sent=False,
        prior_success_resent=False,intent_and_delivered_markers_untouched=True,
        actual_20261016_scheduled_delivery='not yet observed',inbox_delivery='not yet observed'),
    immutable_guard=read('final_immutable_guard.json'))
(HERE/'final_delivery_receipt.json').write_text(json.dumps(receipt,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')

record = '''# 2026-10-16 Put 修复与日报交付验收

已修复并正式发布三倍兑现时序、卖 Put 实际开仓成本、季度换仓后的退出合约身份、持久层证据绑定、月度维护提醒及候选合约/执行日期显示。第3项维护日收盘换仓、第4项结算恢复规则保持；IC不含Call。

最终运行策略为 `36ef68aa1e96a1d352d67f60eeb36e3e622bd010`，消费者为 `bcbd91a4714d4b649e3a6b26af90789ef605b848`。本地固定快照420个根Python文件和14个消费者模块逐文件匹配远端Git blob及SHA-256。共享来源修复采用正式 `observation_batch`；本地另拟方案未发布。

本地38个必需文件、894项全部通过，零失败/错误/跳过，外层60秒硬限下27.58秒完成。云端CI run38856549180：自动化1107项、IC/IM952项通过，微盘任务成功。各范围分别记数，不相加冒充一次全量测试。

正式账本来自最近成功生产run38731463272/artifact11970296521，已验证迁移、全链及官方连续交易日，并在写锁内整体恢复本地。当前seq15/2026-10-15，digest `ea18004690504420ce43a69aad809b005c9663e57a50f6ee78a75b6f631971a3`；原本地seq14分支完整保留。当前所有账本JSON与两份最终handoff逐字节一致，盘中未续账。

| 最终验证 | run | 实际产出时间（北京） | 结果 |
|---|---:|---|---|
| 当日盘中 | 38856549173 | 2026-10-16 13:37:34 | IC/IM当日完整行情、维护提醒、消费者、完整账本交接通过 |
| 前日收盘重新渲染 | 38856968370 | 2026-10-16 13:41:15 | Oct15收盘结果正确提醒Oct16维护；未重发旧日报 |

两次SMTP均已验证TLS、认证、1个收件人及RSET；没有DATA，没有发送邮件，也未改动正式发送intent/delivered marker。今天14:15任务、20:00正常邮件及INBOX送达尚未发生，不计作已验收的实际投递。

月度提醒在前一交易日收盘及维护日盘中突出显示；HOLD仍须核对，零配置不新增仓位。候选仅供盘尾按实时行情复核，当天收盘完成维护，不顺延周一。最新盘中IC旧持仓07500与候选06500明确分列，IM旧7600与候选6800分列；期货下一交易日与Put维护日分别标注。本次实时样本未触发独立三倍/卖Put开仓事件，相关证据来自专项对抗测试及11笔真实开盘报价复验。

原始四臂历史结果不改写：IC CAGR 35.4879%→35.3924%，最大回撤-9.0013%不变；IM CAGR 30.4005%→29.9331%，最大回撤-12.6992%→-12.9182%。历史引擎本已遵守兑现时序，timing_only与基线相同，both与cost_only相同；8条路径7724行独立复算通过。绩效按每倍期货30%保证金/缓冲。

本地原automation.toml受会话写权限限制未写入；既有任务强制读取登记表，登记表指向已发布的必读执行覆盖文档，按当前生产pin、38文件门禁和完整handoff执行。完整待更新配置已保留。ACTIVE、14:15、15:00实际发布门禁、微盘全部规则和云端20:00去重发送保持。

426项保护对象中422项未变；4项为授权整体恢复后更换的运行账本分支文件，原字节均在备份中。冻结规范、SHA-256及首次正式研究输出未改写。

详见 `final_delivery_receipt.json`、`formal_verification/`、`handoff-38856549173/verification.json`、`handoff-38856968370/verification.json`。最终本地日报为 `local_daily_final/local_research_daily.md`；最终前日收盘复核稿在 `local_close_final_current/`。此前失败run与旧模板产物仅保留审计历史，不能当作最终验收。
'''
(HERE/'record.md').write_text(record,encoding='utf-8')
metadata = read('local_daily_final/metadata.json')
wrapper = '''# 本地 IC/IM 研究日报：2026-10-16

来源：本地任务发起的云端计算（run38856549173），本地固定消费者复核渲染。实际采集始于北京时间13:36，报告完成于13:37:34；此处是该次快照，不冒称当前盘尾行情。完整账本截至2026-10-15，盘中快照未入账。以下为通过正式消费者校验的原文，供研究审计与维护提醒。

'''+metadata['body']+'\n'
(HERE/'local_daily_final/local_research_daily.md').write_text(wrapper,encoding='utf-8')
print(json.dumps(dict(status='ok',root_python_files=len(strategy['files']),consumer_python_files=len(consumer['files']),tests=894,live_chain_equal=True),ensure_ascii=True))
