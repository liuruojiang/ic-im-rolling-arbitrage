"""Independent frozen-fixture adversarial suite. Run only against stable sources."""
from pathlib import Path
import sys,os,json,hashlib,subprocess,time
HERE=Path(__file__).resolve().parent
ROOT=next(p for p in HERE.parents if (p/'AGENTS.md').is_file())
OUT=HERE/(sys.argv[1] if len(sys.argv)>1 else 'run_01')
OUT.mkdir(exist_ok=False)
temp=OUT/'isolated_temp';temp.mkdir(mode=0o777)
args=[str(p.relative_to(ROOT)).replace('\\','/') for p in sorted(HERE.glob('test_ind_*.py'))]
def bindings():
    paths=list(ROOT.glob('*.py'))+list(HERE.glob('*.py'))+list((ROOT/'docs').glob('*'))+list((ROOT/'tests/fixtures').rglob('*'))
    return {p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in paths if p.is_file()}
before=bindings();(OUT/'source_before.json').write_text(json.dumps(before,indent=2),encoding='utf-8')
env={**os.environ,'ICIM_TEST_OUTPUT_DIR':str(OUT),'PYTHONIOENCODING':'utf-8','PYTHONDONTWRITEBYTECODE':'1','ICIM_STATE_DIR':str(temp/'state'),'ICIM_REQUIRE_MIGRATION':'0','ICIM_DISABLE_EVIDENCE_CAPTURE':'1','ICIM_DISABLE_INTERNAL_REFRESH':'1','TEMP':str(temp),'TMP':str(temp)}
start=time.monotonic()
with (OUT/'process_output.txt').open('w',encoding='utf-8') as f:
    p=subprocess.run([sys.executable,'-B','-X','utf8',str(ROOT/'_icim_delivery_test_worker.py'),*args],cwd=ROOT,env=env,stdout=f,stderr=subprocess.STDOUT,timeout=300)
after=bindings();(OUT/'source_after.json').write_text(json.dumps(after,indent=2),encoding='utf-8')
sys.path.insert(0,str(ROOT));import icim_delivery_test_evidence as gate
receipt={'base_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),'exit_code':p.returncode,'elapsed_seconds':time.monotonic()-start,'source_before_equals_after':before==after,'source_files':len(before)}
try:receipt['counts']=gate.validate_run(OUT/'regression.xml',process_exit_code=p.returncode,source_before=before,source_after=after)
except Exception as e:receipt['acceptance_error']=str(e)
receipt['artifacts']={n:hashlib.sha256((OUT/n).read_bytes()).hexdigest() for n in ['collection.json','regression.xml','regression.txt','test_run.json','source_before.json','source_after.json'] if (OUT/n).exists()}
(OUT/'receipt.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
if 'acceptance_error' not in receipt:gate.validate_receipt(OUT)
print(json.dumps(receipt,indent=2));sys.exit(p.returncode or int('acceptance_error' in receipt))
