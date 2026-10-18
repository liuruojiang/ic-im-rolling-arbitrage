"""Render the verified stored close with a separately archived public reference."""
from pathlib import Path
import copy
import hashlib
import json
import os
import re
import sys
import tomllib
from datetime import datetime

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
sys.path.insert(0,str(ROOT))
os.environ.update(ICIM_STATE_DIR=str(HERE/'isolated_state'),ICIM_REQUIRE_MIGRATION='0',PYTHONDONTWRITEBYTECODE='1')
import poe_ic_im_v1_4_state as state
import run_ic_im_v1_4_github_digest as runner

sha=lambda b:hashlib.sha256(b).hexdigest()
ledger=ROOT/'runtime/ic_im_v1_4_r1'
protected=[ledger/'latest.json',ledger/'migration_record.json',*sorted((ledger/'journal').glob('*.json'))]
before={str(p):sha(p.read_bytes())for p in protected}
latest=state.StateStore(ledger).load_latest()
assert latest['sequence']==15 and latest['verified_day']=='2026-10-15'
origin=ROOT/'quant_research_runs/20261016_fear_single_source_delivery_v1/online_observations.txt'
excerpt=origin.read_text('utf-8')
row=next(line[line.index('{'):]for line in excerpt.splitlines()if '"jiu_fear_greed_index"' in line)
# GitHub masks this boolean. Its value is irrelevant to the archived numerical
# reference. Remove it, then add our own explicit presentation-only metadata.
row=re.sub(r'"jiu_reference_only":\s*\*\*\*,\s*','',row)
reference=json.loads(row)
assert reference['jiu_fear_greed_index']==2.28
assert reference['jiu_data_date']=='2026-10-15'
assert reference['jiu_retrieved_at']=='2026-10-16T13:09:30+08:00'
reference['jiu_reference_only']=True
view=copy.deepcopy(latest)
for signal in view['signals'].values():
    signal.update(reference)
header=(
 '# 本地只读成品核验：2026-10-15已确认收盘账本\n\n'
 '本文件使用当前正式root renderer，只读重渲染已验证的10月15日seq15账本。'
 '它不是10月16日盘中新信号，也没有重新计算网格、取新行情、续账或发邮件。\n\n'
 '9圈参考取自已归档的公开来源观察：云端readiness run 38854223720，'
 '原采集2026-10-16 13:09:30+08:00，数据日2026-10-15、指数2.28。'
 '该参考元数据仅添加到内存报告视图，没有写回账本。\n\n')
report=header+runner.render_stored_close_report(view)
(HERE/'stored_close_with_archived_jiu.md').write_text(report,encoding='utf-8')
assert report.count('9圈恐慌贪婪指数：2.28')==2
assert report.count('；采集 2026-10-16T13:09:30+08:00')==2
assert report.count('仅供参考，不参与网格信号')==2
assert 'https://jiucaishuo.com/indicators?tab=fg' in report
assert '月度Put维护日历提醒' in report
assert 'IC 月度Put展期换仓提醒' in report and 'IM 月度Put展期换仓提醒' in report
assert '2026-10-16 当天收盘完成换仓' in report

# This second document exercises presentation only. These warnings are injected
# fixtures; the stored 10/15 signal is not relabelled in production.
warning='仅取得CSV单侧数据，未完成双侧验证；未取得网页收盘后发布证明。'
template=copy.deepcopy(view)
for signal in template['signals'].values():
    signal['fear_source_warning']=warning
template_report=('# 展示模板反例，仅测试提示文案\n\n'
                 '以下单侧警告为隔离注入，不表示真实账本当时缺少网页，不能用作交易信号。\n\n'
                 +runner.render_stored_close_report(template))
(HERE/'single_source_disclosure_template.md').write_text(template_report,encoding='utf-8')
assert template_report.count('- **百分位取数提示：'+warning+'**')==2
after={str(p):sha(p.read_bytes())for p in protected}
assert before==after

proposed=HERE/'automation.proposed.toml'
config=proposed.read_text('utf-8')
parsed=tomllib.loads(config)
prompt=parsed['prompt']
append=HERE/'proposed_appendix.txt'
added='test_ic_im_fear_delivery_adversarial_20261016.py'
def upgrade(text):
    text=text.replace('以下4文件，共37文件：','以下5文件，共38文件：')
    return text.replace('test_ic_im_fear_source_delivery_20261016.py；零失败',
                        'test_ic_im_fear_source_delivery_20261016.py、'+added+'；零失败')
new_prompt=upgrade(prompt)
assert added in new_prompt and '共38文件' in new_prompt
updated=re.sub(r'^prompt = .*$',lambda _: 'prompt = '+json.dumps(new_prompt,ensure_ascii=False),config,count=1,flags=re.M)
assert tomllib.loads(updated)['prompt']==new_prompt
assert {k:v for k,v in tomllib.loads(updated).items()if k!='prompt'}=={k:v for k,v in parsed.items()if k!='prompt'}
proposed.write_text(updated,encoding='utf-8')
append.write_text(upgrade(append.read_text('utf-8')),encoding='utf-8')
original_cfg=Path('D:/Codex/home/automations/ic-im/automation.toml')
assert original_cfg.read_bytes()==(HERE/'automation.readonly.toml').read_bytes()
evidence={
 'verified_at':datetime.now().astimezone().isoformat(),
 'scope':'Existing 10/15 stored-close local rendering with separately archived 9圈. Not a new 10/16 intraday signal.',
 'ledger_verified_day':latest['verified_day'],'ledger_sequence':latest['sequence'],'ledger_digest':latest['digest'],
 'public_reference_origin':'readiness run 38854223720 / job 116822120469',
 'public_reference_excerpt_path':str(origin),'public_reference_excerpt_sha256':sha(origin.read_bytes()),
 'reference_metadata':reference,'reference_was_fetched_in_this_audit':False,
 'checks':{'both_products_jiu':True,'original_reference_timestamp':True,'reference_only':True,
           'put_maintenance_reminders_both_products':True,'maintenance_close_clock':True,
           'single_side_warning_rendered_twice_in_injected_template':True,
           'production_ledger_json_unchanged':before==after,'actual_automation_config_unchanged':True,
           'proposed_38_test_list':True,'all_other_automation_fields_preserved':True},
 'source_sha256':{name:sha((ROOT/name).read_bytes())for name in ['run_ic_im_v1_4_github_digest.py','ic_im_fear_grid_v1.py','ic_im_option_maintenance_notice.py']},
 'output_sha256':{name:sha((HERE/name).read_bytes())for name in ['stored_close_with_archived_jiu.md','single_source_disclosure_template.md','automation.proposed.toml']},
 'protected_json_sha256':before,
 'no_market_fetch':True,'no_ledger_continuation':True,'no_smtp':True,'external_config_written':False}
(HERE/'stored_close_verification.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in evidence.items()if k not in ['protected_json_sha256']},ensure_ascii=False,indent=2))
