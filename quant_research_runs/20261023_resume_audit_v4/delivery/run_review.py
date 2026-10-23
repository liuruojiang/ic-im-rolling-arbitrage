from pathlib import Path
import hashlib,json,os,sys,time,xml.etree.ElementTree as ET
ROOT=Path(__file__).resolve().parents[3]
HERE=Path(__file__).resolve().parent
BASE=ROOT/'quant_research_runs/20261023_daily_release_v3'
AUTO=BASE/'automation_checkout'
STRATEGY=BASE/'strategy_checkout'
OUT=HERE/'verification_01'
OUT.mkdir(exist_ok=False)
TEMP=OUT/'temp'
TEMP.mkdir(mode=0o777)
os.environ.update(ICIM_AUDIT_STRATEGY_ROOT=str(STRATEGY),ICIM_AUDIT_FINAL_SHA='e3f26824a8ff0a89b6125d4385f75e0a6cfa2a7e',ICIM_AUDIT_AUTOMATION_ROOT=str(AUTO),ICIM_STATE_DIR=str(TEMP/'state'),ICIM_REQUIRE_MIGRATION='0',ICIM_DISABLE_INTERNAL_REFRESH='1',ICIM_DISABLE_EVIDENCE_CAPTURE='1',PYTHONDONTWRITEBYTECODE='1',TEMP=str(TEMP),TMP=str(TEMP))
def hashes():
    return {p.relative_to(AUTO).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in AUTO.rglob('*') if p.is_file() and not any(x in {'.git','__pycache__','.pytest_cache','.hypothesis'} for x in p.relative_to(AUTO).parts)}
before=hashes()
(OUT/'source_before.json').write_text(json.dumps(before,indent=2),'utf-8')
original_mkdir,original_chmod=os.mkdir,os.chmod
adapted=[]
def compatible(path,mode):
    try:return os.name=='nt' and mode==0o700 and Path(path).resolve().is_relative_to(TEMP.resolve())
    except (TypeError,ValueError):return False
def mkdir(path,mode=0o777,*,dir_fd=None):
    if compatible(path,mode):mode=0o777;adapted.append(str(path))
    return original_mkdir(path,mode,**({} if dir_fd is None else {'dir_fd':dir_fd}))
def chmod(path,mode,**kw):
    if compatible(path,mode):mode=0o777;adapted.append(str(path))
    return original_chmod(path,mode,**kw)
os.mkdir,os.chmod=mkdir,chmod
sys.path[:0]=[str(AUTO),str(AUTO/'scripts')]
import tempfile
tempfile.tempdir=str(TEMP)
import pytest,yaml,re
workflow=AUTO/'.github/workflows/ic-im-v1-4-daily-digest.yml'
text=workflow.read_text('utf-8'); doc=yaml.safe_load(text);steps=doc['jobs']['send']['steps']
ids=[s['id'] for s in steps if 'id' in s]
refs=set(re.findall(r'\bsteps\.([A-Za-z_][A-Za-z_0-9]*)\.',text))
struct={'steps':len(steps),'step_ids':ids,'unresolved_step_references':sorted(refs-set(ids)),'steps_without_run_or_uses':[s.get('name') for s in steps if not ('run' in s or 'uses' in s)],'duplicate_ids':len(ids)!=len(set(ids))}
assert not struct['unresolved_step_references'] and not struct['steps_without_run_or_uses'] and not struct['duplicate_ids']
tests=[str(BASE/'independent_delivery/test_delivery_boundaries.py')]+[str(AUTO/'tests'/name) for name in ['test_ic_im_v1_4_workflow.py','test_ic_im_v1_4_readiness.py','test_ic_im_v1_4_readiness_handoff.py','test_ic_im_v1_4_result_day_delivery.py','test_ic_im_v1_4_reference_isolation_20261023.py','test_ic_im_v1_4_contract_adversarial_20261009.py','test_ic_im_v1_4_source_manifest_v3.py']]
class Cases:
    def __init__(self):self.collected=[];self.reports=[]
    def pytest_collection_finish(self,session):self.collected=[i.nodeid for i in session.items]
    def pytest_runtest_logreport(self,report):self.reports.append({'nodeid':report.nodeid,'phase':report.when,'outcome':report.outcome})
cases=Cases();start=time.monotonic()
status=pytest.main(['-q','-p','no:cacheprovider','-c',str(BASE/'independent_delivery/pytest.ini'),'--confcutdir',str(BASE),'--basetemp',str(TEMP/'pytest'),'--junitxml',str(OUT/'regression.xml')]+tests,plugins=[cases])
os.mkdir,os.chmod=original_mkdir,original_chmod
after=hashes();tree=ET.parse(OUT/'regression.xml').getroot()
counts={key:sum(int(s.get(key,0)) for s in list(tree)) for key in ['tests','failures','errors','skipped']}
old=json.loads((BASE/'automation_full_03/source_after.json').read_text('utf-8'))
receipt={'exit_code':int(status),'counts':counts,'collected_nodes':len(cases.collected),'elapsed_seconds':time.monotonic()-start,'source_files':len(before),'source_before_equals_after':before==after,'prior_full_03_source_matches':before==old,'prior_source_differences':sorted(k for k in set(before)|set(old) if before.get(k)!=old.get(k)),'workflow_structure':struct,'temp_acl_adaptation_count':len(adapted),'temp_acl_adaptation_scope':str(TEMP),'smtp_executed':False,'production_ledger_written':False}
for name,data in [('receipt.json',receipt),('source_after.json',after),('collection.json',cases.collected),('executed.json',cases.reports)]: (OUT/name).write_text(json.dumps(data,indent=2,ensure_ascii=False),'utf-8')
print(json.dumps(receipt,ensure_ascii=False))
raise SystemExit(int(status) or int(before!=after))
