"""Independent read-only production-path probes; all writes are audit evidence."""
import sys
sys.dont_write_bytecode = True
from pathlib import Path
import json, copy, math, hashlib, re, os
from datetime import date
ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT/'quant_research_runs/20261018_monthly_spreads_v1/cloud/scripts'))
if os.environ.get('ICIM_AUDIT_CLOUD_ROOT'):
    sys.path.insert(0,str(ROOT/os.environ['ICIM_AUDIT_CLOUD_ROOT']/'scripts'))
import pandas as pd
import ic_im_quarter_roll_v1_3 as q
import poe_ic_im_mainline_v1_4_bot as bot
import poe_ic_im_v1_4_state as state
import build_ic_im_v1_4_digest as mail

def exp(c): return bot._third_friday(*bot._contract_month(c))
def attempt(f):
    try: return {'ok':True, 'value':f()}
    except Exception as e: return {'ok':False, 'exception':type(e).__name__, 'message':str(e)}

evidence = {'network_verification':'gh API blocked by session proxy; local release snapshots checked', 'cases':[]}
for product in ['IC','IM']:
    f = pd.DataFrame({'instrument':[product+m for m in ['2610','2611','2612','2703']],
                      'lastprice':[1e-300,1e308,6000.,5900.]})
    f.attrs.update(source='synthetic finite-price source fault',source_date='2026-10-15')
    spreads = q.monthly_spreads(product,f,date(2026,10,15),exp)
    serialized=state._jsonable(spreads)
    evidence['cases'].append({'product':product,'all_prices_finite_positive':all(math.isfinite(v) and v>0 for v in f.lastprice),
        'before_json':repr(spreads),'serialized':serialized,
        'quarter':q.quarter_spread(product,f,date(2026,10,15),exp),
        'local_pre_json':attempt(lambda:q.format_spreads(spreads)),
        'local_post_json':attempt(lambda:q.format_spreads(serialized)),
        'cloud_post_json':attempt(lambda:mail.month_spread_reasons(serialized))})

# Independent differential check over real source sessions and both products.
rows=[]
for product,p in [('IC','data/ic_monthly_discount_roll_v1/cffex_ic_contracts.csv'),('IM','data/im_monthly_roll_3m_lowest_put_v1/cffex_im_contracts.csv')]:
    raw=pd.read_csv(ROOT/p)
    for day_s, subset in raw.groupby('date'):
        day=date.fromisoformat(str(day_s)[:10])
        f=subset.rename(columns={'contract':'instrument','close':'lastprice'})
        f.attrs.update(source='saved official CFFEX close',source_date=day.isoformat())
        s={'market_date':day.isoformat(),**q.monthly_spreads(product,f,day,exp)}
        before=copy.deepcopy(s)
        a=[q.format_month_spread(s[key],label) for key,label in q.MONTH_SPREAD_LABELS.items()]
        b=mail.month_spread_reasons(s)
        rows.append({'product':product,'day':str(day_s),'same':a==b,'unchanged':s==before,
                     'local':a if a!=b else [],'cloud':b if a!=b else []})
evidence['real_sessions_count']=len(rows)
evidence['real_sessions_mismatch']=[r for r in rows if not r['same'] or not r['unchanged']]

signal={'market_date':'2026-10-19','month_next_spread':dict(status='ok',near_contract='IM2610',far_contract='IM2611',near_price=6000.,far_price=5900.,points=999.,ratio=.9,annualized=.9,same_contract=False,source_date='2026-10-15',source='CFFEX')}
evidence['unvalidated_bad_payload_render']=attempt(lambda:mail.month_spread_reasons(signal))

real_payload_file=ROOT/'quant_research_runs/20261016_put_delivery_completion_v1/handoff-38853603565/report/result.json'
if real_payload_file.exists():
    original=json.loads(real_payload_file.read_text(encoding='utf-8'))
    original_hash=hashlib.sha256(real_payload_file.read_bytes()).hexdigest()
    evidence['real_payload_base_validation']=attempt(lambda:mail.validate_success_payload(original))
    changed=copy.deepcopy(original)
    for product in ['IC','IM']:
        f=pd.DataFrame({'instrument':[product+m for m in ['2610','2611','2612','2703']], 'lastprice':[1.,1e308,6000.,5900.]})
        f.attrs.update(source='synthetic finite-price fault inserted into saved real producer signal',source_date=original['market_date'])
        generated=q.monthly_spreads(product,f,date.fromisoformat(original['market_date']),exp)
        changed['signals'][product].update(state._jsonable(generated))
    evidence['real_payload_fault_validation']=attempt(lambda:mail.validate_success_payload(changed))
    evidence['real_payload_fault_mail']=attempt(lambda:mail.build_success(changed,'',''))
    evidence['real_payload_fault_html']=attempt(lambda:mail.build_success_html(changed,''))
    evidence['real_signal_fault_state_validation']={p:attempt(lambda p=p:state.validate_delivery_values(changed['signals'][p],p)) for p in ['IC','IM']}
    forged=copy.deepcopy(original)
    forged['signals']['IC'].update(signal)
    # Restore IC identity and actual date after adding bad optional diagnostics.
    forged['signals']['IC']['market_date']=original['market_date']
    evidence['real_payload_bad_diagnostic_validation']=attempt(lambda:mail.validate_success_payload(forged))
    evidence['real_payload_bad_diagnostic_mail']=attempt(lambda:mail.month_spread_reasons(forged['signals']['IC']))
    evidence['real_payload_file_unchanged']=hashlib.sha256(real_payload_file.read_bytes()).hexdigest()==original_hash
    baseline_file=HERE/'evidence_before_fix.json'
    if baseline_file.exists():
        old=copy.deepcopy(original)
        baseline=json.loads(baseline_file.read_text(encoding='utf-8'))
        for case in baseline['cases']:
            fields=copy.deepcopy(case['serialized'])
            for value in fields.values(): value['source_date']=original['market_date']
            old['signals'][case['product']].update(fields)
        import run_ic_im_v1_4_github_digest as runner
        evidence['old_bad_stored_metadata_local_report']=attempt(lambda:runner.render_stored_close_report({'verified_day':original['market_date'],'sequence':original['sequence'],'digest':original['digest'],'signals':old['signals']}))
        evidence['old_bad_stored_metadata_mail']=attempt(lambda:mail.build_success(old,'',''))
        evidence['old_bad_stored_metadata_html']=attempt(lambda:mail.build_success_html(old,''))

manifest=json.loads((ROOT/'quant_research_runs/20261018_monthly_spreads_v1/source_manifest.json').read_text())
checks={}
for path,expected in manifest.items():
    p=ROOT/path if not path.startswith('automation/') else ROOT/'quant_research_runs/20261018_monthly_spreads_v1/cloud'/path.removeprefix('automation/')
    content=p.read_bytes().replace(b'\r\n',b'\n')
    checks[path]={'lf_sha_match':hashlib.sha256(content).hexdigest()==expected['sha256_lf'], 'git_blob_match':hashlib.sha1(b'blob '+str(len(content)).encode()+b'\0'+content).hexdigest()==expected['git_blob_lf']}
evidence['manifest_checks']=checks
evidence['ledger_path_review']={
    'append_confirmed_signals':'poe_ic_im_v1_4_state.py:1455 stores all signal fields via _jsonable(signals)',
    'validate_delivery_values':'No monthly-spread status/date/product/finite-derived-value guard',
    'anchor_derivation':'No month_next_spread or month_quarter_spread reference in state anchor derivation',
    'official_ledger_written':False}
archived_path=ROOT/'quant_research_runs/20261016_put_delivery_completion_v1/cloud/tests/fixtures/icim/readiness_20261009/result.json'
if archived_path.exists():
    archived=json.loads(archived_path.read_text(encoding='utf-8'))
    subject,plain,_=mail.build_success(archived,'','')
    rendered=mail.build_success_html(archived,'')
    actual={name:hashlib.sha256(s.encode()).hexdigest() for name,s in [('subject',subject),('plain',plain),('html',rendered)]}
    expected=json.loads((ROOT/'quant_research_runs/20261018_monthly_spreads_v1/archive_render_verification.json').read_text())
    evidence['oct09_archive_independent_hashes']={'actual':actual,'expected':expected,'match':actual==expected}
(HERE/os.environ.get('ICIM_AUDIT_EVIDENCE_NAME','evidence.json')).write_text(json.dumps(evidence,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
print(json.dumps({'cases':[(c['product'],c['local_post_json']['ok'],c['cloud_post_json']['ok']) for c in evidence['cases']], 'real_sessions_count':len(rows),'mismatch_count':len(evidence['real_sessions_mismatch']),'manifest_match':all(all(c.values()) for c in checks.values()), 'archive_match':evidence.get('oct09_archive_independent_hashes',{}).get('match'), 'old_bad_metadata_rendered':{k:v['ok'] for k,v in evidence.items() if k.startswith('old_bad_stored_metadata')}},ensure_ascii=True))
