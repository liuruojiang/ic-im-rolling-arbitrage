from pathlib import Path
import argparse, datetime, hashlib, json, os, subprocess, sys, xml.etree.ElementTree as ET

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
parser=argparse.ArgumentParser()
parser.add_argument('--strategy-root',required=True)
parser.add_argument('--strategy-sha',required=True)
parser.add_argument('--automation-root',required=True)
parser.add_argument('--out',required=True)
args=parser.parse_args()
strategy=Path(args.strategy_root).resolve();auto=Path(args.automation_root).resolve();out=Path(args.out).resolve()
assert subprocess.check_output(['git','-C',str(strategy),'rev-parse','HEAD'],text=True).strip()==args.strategy_sha
out.mkdir(parents=True,exist_ok=False)
def hashes():
    paths=list((auto/'scripts').glob('*.py'))+list((auto/'.github/workflows').glob('*.yml'))+list((auto/'docs').glob('*manifest.json'))
    manifest=json.loads((strategy/'docs/ic_im_daily_release_20261023_repairs_v3_r3_manifest.json').read_text('utf-8'))
    paths += [strategy/name for name in manifest['files']]+[strategy/'docs/ic_im_daily_release_20261023_repairs_v3_r3_manifest.json',HERE/'test_delivery_boundaries.py',HERE/'run_audit.py']
    return {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths if p.is_file()}
before=hashes();(out/'source_before.json').write_text(json.dumps(before,indent=2),'utf-8')
env={**os.environ,'ICIM_AUDIT_STRATEGY_ROOT':str(strategy),'ICIM_AUDIT_FINAL_SHA':args.strategy_sha,'ICIM_AUDIT_AUTOMATION_ROOT':str(auto),'PYTHONDONTWRITEBYTECODE':'1','PYTHONIOENCODING':'utf-8','ICIM_DISABLE_EVIDENCE_CAPTURE':'1','ICIM_DISABLE_INTERNAL_REFRESH':'1'}
cmd=[sys.executable,'-B','-X','utf8','-m','pytest','-c',str(HERE/'pytest.ini'),'--confcutdir',str(HERE),'-q','--basetemp',str(out/'temp'),'--junitxml',str(out/'regression.xml'),str(HERE/'test_delivery_boundaries.py')]
start=datetime.datetime.now(datetime.timezone.utc).isoformat()
with (out/'regression.txt').open('w',encoding='utf-8') as stream:
    run=subprocess.run(cmd,cwd=ROOT,env=env,stdout=stream,stderr=subprocess.STDOUT,timeout=300)
after=hashes();(out/'source_after.json').write_text(json.dumps(after,indent=2),'utf-8')
cases=ET.parse(out/'regression.xml').getroot().findall('.//testcase')
counts={'tests':len(cases),'failures':sum(c.find('failure') is not None for c in cases),'errors':sum(c.find('error') is not None for c in cases),'skipped':sum(c.find('skipped') is not None for c in cases)}
receipt={'status':'PASS' if run.returncode==0 and before==after and not any(counts[k] for k in ['failures','errors','skipped']) else 'NOT_ACCEPTED','started_at':start,'finished_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'strategy_sha':args.strategy_sha,'strategy_root':str(strategy),'automation_root':str(auto),'test_cases':[{'classname':c.get('classname'),'name':c.get('name')} for c in cases],'counts':counts,'exit_code':run.returncode,'source_before_equals_after':before==after,'command':cmd,'network_forbidden':True,'smtp_executed':False,'production_marker_write':False,'real_data_day':'2026-10-22','artifacts':{name:hashlib.sha256((out/name).read_bytes()).hexdigest() for name in ['regression.xml','regression.txt','source_before.json','source_after.json']}}
(out/'receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),'utf-8')
print(json.dumps({k:receipt[k] for k in ['status','counts','exit_code','source_before_equals_after']},ensure_ascii=False))
raise SystemExit(0 if receipt['status']=='PASS' else 1)
