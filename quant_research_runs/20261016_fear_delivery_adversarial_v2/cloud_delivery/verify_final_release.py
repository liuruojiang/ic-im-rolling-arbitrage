from pathlib import Path
import hashlib,json,sys,zipfile

ROOT=Path(__file__).resolve().parents[3]
HERE=Path(__file__).resolve().parent
C=ROOT/'quant_research_runs/20261016_fear_delivery_adversarial_v2/coordinator'
H=C/'handoff-38856549173'
R=json.loads((H/'report/result.json').read_text(encoding='utf8'))
M=json.loads((H/'handoff_manifest.json').read_text(encoding='utf8'))
zip_bytes=(H/'handoff.zip').read_bytes()
assert hashlib.sha256(zip_bytes).hexdigest()=='587a54945617b38cb903bf3fd95ff77ebb9c117f02d8df69ed6ef0f4a140d4db'
assert len(zip_bytes)==113897
assert M['strategy_sha']=='36ef68aa1e96a1d352d67f60eeb36e3e622bd010'
for name,digest in M['files'].items():
    assert hashlib.sha256((H/name).read_bytes()).hexdigest()==digest
with zipfile.ZipFile(H/'handoff.zip') as z:
    assert set(z.namelist())==set(M['files'])|{'handoff_manifest.json'}
    assert all(not name.startswith('/') and '..' not in Path(name).parts for name in z.namelist())
assert R['status']=='ok' and R['publication_mode']=='realtime'
assert R['market_date']=='2026-10-16' and R['completed_day']=='2026-10-15'
assert R['report_generated_at']=='2026-10-16T13:37:34+08:00' and R['advanced_sessions']==0
IC,IM=R['signals']['IC'],R['signals']['IM']
fear_keys=set(k for s in (IC,IM) for k in s if k.startswith('fear_'))
differences={k:[IC.get(k),IM.get(k)] for k in fear_keys if IC.get(k)!=IM.get(k)}
assert not differences
assert IC['fear_greed_index']==51.36 and IC['fear_cross_validation']=='matched'
assert IC['put_target_contract']=='510500P2612M06500' and IC['put_target_core_qty']==16
for s in (IC,IM):
    assert s['jiu_reference_only'] is True and s['jiu_data_date']=='2026-10-15'
    assert s['jiu_fear_greed_index']==2.28
    assert s['option_monthly_reset_execution_date']=='2026-10-16'
    assert s['next_trade_date']=='2026-10-19'
checks={}
for name in ('ic_im_v1_4_digest.md','ic_im_v1_4_digest.html'):
    text=(C/'local_daily_final'/name).read_text(encoding='utf8')
    required=['合计16张（核心16张 510500P2612M06500','Put：2026-10-16收盘维护候选（盘尾复核）',
              '期货：下一交易日2026-10-19研究目标','9圈恐慌贪婪指数：2.280','数据日 2026-10-15',
              '与百分位数据日不同','仅供参考，不参与网格信号']
    checks[name]={term:term in text for term in required}
    assert all(checks[name].values())
    assert '合计17张' not in text
sys.path.insert(0,str(ROOT))
import ic_im_fear_grid_v1 as feed
transitions={}
for p,s in R['signals'].items():
    a=feed.transition(p,s['grid_current'],s['grid_entry_source_current'],s['score'],s['fear_greed_index'])
    b=feed.transition(p,s['grid_current'],s['grid_entry_source_current'],s['score'],s['jiu_fear_greed_index'])
    assert a[0]==s['grid_target'] and a[1]==s['grid_entry_source_target'] and a[2]==s['grid_transition_reason']
    transitions[p]=dict(primary_percentile=a,unused_jiu_counterfactual=b,published_target=s['grid_target'])
out=dict(strategy_sha=M['strategy_sha'],automation_sha='bcbd91a4714d4b649e3a6b26af90789ef605b848',
         run=38856549173,report_generated_at=R['report_generated_at'],shared_fear_fields_count=len(fear_keys),
         shared_fear_differences=differences,zip_sha256=hashlib.sha256(zip_bytes).hexdigest(),
         zip_files_verified=len(M['files']),render_checks=checks,transitions=transitions,
         ic_monthly_candidate=IC['put_target_contract'],ic_monthly_candidate_qty=IC['put_target_core_qty'],
         source_jiu_date=IC['jiu_data_date'],source_jiu_value=IC['jiu_fear_greed_index'],
         message_sent=False,scope='Read-only final artifact inspection. SMTP envelope check succeeded without DATA; no inbox receipt claimed.')
(HERE/'final_release_independent_verification.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf8')
print(json.dumps(out,ensure_ascii=False,indent=2))

