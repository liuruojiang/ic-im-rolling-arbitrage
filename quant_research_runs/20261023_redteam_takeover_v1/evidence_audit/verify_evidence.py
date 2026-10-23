from pathlib import Path
import datetime, hashlib, json, sys, os, xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[3]
OLD = ROOT / 'quant_research_runs/20261023_redteam_repairs_v3'
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import icim_delivery_test_evidence as gate
from _pytest.junitxml import mangle_test_address

def read(p): return json.loads(p.read_text('utf-8'))
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def mismatches(bindings):
    return [n for n,h in bindings.items() if not (ROOT/n).is_file() or sha(ROOT/n)!=h]

result={'status':'BLOCKED_PROTECTED_DATA_CHANGED_AND_DELIVERY_INCOMPLETE','checked_at':datetime.datetime.now(datetime.timezone.utc).isoformat(), 'writes':'this new evidence_audit directory only', 'runs':{}}
manifest=read(ROOT/'docs/ic_im_daily_release_20261023_repairs_v3_r3_manifest.json')
result['production_manifest']={'files':len(manifest['files']),'mismatches':mismatches(manifest['files'])}
historical=read(ROOT/'docs/icim_historical_deselections_20261023.json')
for run in ['cumulative_02','daily_gate_03']:
    d=OLD/run; receipt=read(d/'receipt.json'); counts=gate.validate_receipt(d)
    bound=read(d/'source_after.json'); coll=read(d/'collection.json')
    expected=set(historical if run=='cumulative_02' else manifest['daily_gate_deselections'])
    result['runs'][run]={'counts':counts,'receipt_sha256':sha(d/'receipt.json'),'bound_files':len(bound),'current_mismatches':mismatches(bound),'deselected':len(coll['deselected']),'deselections_match':set(coll['deselected'])==expected}
rollup=read(OLD/'independent_rollup.json'); independent=[]
for entry in rollup['runs']:
    d=OLD/entry['run']; cases=ET.parse(d/'regression.xml').getroot().findall('.//testcase')
    counts={'tests':len(cases),'failures':sum(c.find('failure') is not None for c in cases),'errors':sum(c.find('error') is not None for c in cases),'skipped':sum(c.find('skipped') is not None for c in cases)}
    coll=read(d/'collection.json'); tr=read(d/'test_run.json')
    expected=set()
    for node in coll['executed']:
        name=mangle_test_address(node);expected.add(('.'.join(name[:-1]),name[-1]))
    item={'run':entry['run'],'counts':counts,'counts_match_rollup':all(counts[k]==entry[k] for k in counts),'exit_code':tr['exit_code'],'xml_identity_matches':expected=={(c.get('classname'),c.get('name')) for c in cases},'executed_matches_selected':set(coll['executed'])==set(coll['selected'])}
    if (d/'receipt.json').exists():
        rc=read(d/'receipt.json');item['artifact_hash_mismatches']=[n for n,h in rc['artifacts'].items() if sha(d/n)!=h]
    if (d/'source_after.json').exists():
        bound=read(d/'source_after.json');item.update(bound_files=len(bound),before_equals_after=read(d/'source_before.json')==bound,current_mismatches=mismatches(bound))
    independent.append(item)
result['independent']={'actual_total':sum(x['counts']['tests'] for x in independent),'runs':independent}
reports={}
for n,key in [('audit_market_evidence/report.json','source_sha256_at_report'),('audit_server/result.json','reviewed_source_hashes')]:
    d=read(OLD/n);reports[n]={'bound_files':len(d[key]),'mismatches':mismatches(d[key])}
result['independent_reports']=reports
rec=read(OLD/'real_recovery_02/receipt.json')
import ic_im_signal_evidence_v1 as evidence
packages={}
for k,v in rec['selected_packages'].items():
    path=Path(v['manifest']); reader=evidence._load_one(path.parents[2],path,datetime.date.fromisoformat(rec['market_day']))
    packages[k]={'manifest_body_hash_matches':reader.manifest_sha256==v['sha256'],'all_blob_validation_passed':True,'manifest_file_sha256':sha(path)}
result['real_recovery']={'status':rec['status'],'market_day':rec['market_day'],'synthetic_market_inputs':rec['synthetic_market_inputs'],'source_mismatches':mismatches(rec['source_sha256']),'driver_matches':sha(OLD/'recovery_audit.py')==rec['driver_sha256'],'packages':packages,'cases':[{k:c.get(k) for k in ['name','status','before','after','zero_external_http','atomic_rejection']} for c in rec['cases']], 'comparison':read(OLD/'recovery_comparison.json')}
base=read(OLD/'baseline.json')['files'];changed=mismatches(base)
protected=[n for n in changed if n.startswith(('runtime/','outputs/','docs/','quant_research_runs/20261023_latest_redteam_v2/'))]
additions={folder:[p.relative_to(ROOT).as_posix() for p in (ROOT/folder).rglob('*') if p.is_file() and p.relative_to(ROOT).as_posix() not in base] for folder in ['runtime','outputs']}
result['baseline']={'files':len(base),'changed':changed,'protected_changes':protected,'additions':additions}
latest='runtime/ic_im_v1_4_r1/latest.json'; journal='runtime/ic_im_v1_4_r1/journal/000020-2026-10-22.json'
now=read(ROOT/latest)
result['latest_pointer']={'baseline_sha256':base[latest],'current_sha256':sha(ROOT/latest),'current_metadata':{k:now[k] for k in ['sequence','verified_day','digest','source','updated_at']},'mtime_utc':datetime.datetime.fromtimestamp((ROOT/latest).stat().st_mtime,datetime.timezone.utc).isoformat(),'journal20_in_baseline':journal in base,'journal20_baseline_sha256':base.get(journal),'journal20_current_sha256':sha(ROOT/journal),'current_latest_equals_journal20':(ROOT/latest).read_bytes()==(ROOT/journal).read_bytes(),'baseline_latest_equals_journal19':base[latest]==sha(ROOT/'runtime/ic_im_v1_4_r1/journal/000019-2026-10-21.json')}
os.environ.update(ICIM_DISABLE_EVIDENCE_CAPTURE='1',ICIM_DISABLE_INTERNAL_REFRESH='1')
import poe_ic_im_v1_4_state as state
verified=state.StateStore(ROOT/'runtime/ic_im_v1_4_r1').load_latest()
result['latest_pointer']['full_chain_load_latest_passed']=verified==now
def diff(a,b,path=''):
    if type(a)!=type(b): return [path]
    if isinstance(a,dict):
        out=[]
        for k in sorted(set(a)|set(b)):
            out.extend([path+'/'+k] if k not in a or k not in b else diff(a[k],b[k],path+'/'+k))
        return out
    if isinstance(a,list):
        return [path] if len(a)!=len(b) else [p for i,(x,y) in enumerate(zip(a,b)) for p in diff(x,y,path+'/'+str(i))]
    return [] if a==b else [path]
recovered=read(OLD/'real_recovery_02/automatic_recovery/recovered_record.json')
excluded={'fetch_time','signal_calculation_started_at','signal_generated_at'}
result['current_vs_isolated_recovery']={'product_differences':diff(now['products'],recovered['products']),'signal_differences_excluding_three_calculation_clocks':{product:diff({k:v for k,v in now['signals'][product].items() if k not in excluded},{k:v for k,v in recovered['signals'][product].items() if k not in excluded}) for product in ['IC','IM']},'excluded_signal_fields':sorted(excluded)}
result['writer_attribution']={'status':'UNKNOWN','latest_and_journal_creation_time_local':'2026-10-23T14:54:51+08:00','collector_launch_local':read(OLD/'delivery/collector_launch.json')['started_at'],'collector_first_receipt_already_seq20':read(OLD/'delivery/collector_first_receipt.json')['ledger'],'inference':'The observed ledger change predates this collector launch. Internal updated_at is a 2026-10-22 timestamp; this alone does not identify who copied or wrote the record.'}
result['missing_delivery_files']=[n for n in ['final_verification.json','commit_repairs.ps1','verify_staged.py','commit_hashes.json','commit_paths.nul','commit_preparation.json'] if not (OLD/n).exists()]
(OUT/'verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf-8')
print(json.dumps(result,ensure_ascii=False,indent=2))
