from pathlib import Path
import hashlib,json,shutil,subprocess,sys,xml.etree.ElementTree as ET
ROOT=Path(__file__).resolve().parents[3]
HERE=Path(__file__).resolve().parent
OLD=ROOT/'quant_research_runs/20261023_daily_release_v3'
DEST=HERE/'candidate_checkout'
AUTO=OLD/'automation_checkout'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def gitsha(b):return hashlib.sha1(b'blob '+str(len(b)).encode()+b'\0'+b).hexdigest()
meta=json.loads((OLD/'publication_snapshot.json').read_text('utf8'))
shutil.copytree(OLD/'strategy_checkout',DEST,ignore=shutil.ignore_patterns('__pycache__','.pytest_cache','.hypothesis'))
base='b163b34f83b8c51ba2c07e9e9f3eb1ff67797c10'
args=['git','ls-tree','-r',base,'--','tests/fixtures','quant_research_runs/20261002_ic_csi500_abs40_debounce/sina_sh000905_ohlcv.csv']
rows=subprocess.check_output(args,cwd=ROOT).decode('utf8').splitlines()
restored=[]
for row in rows:
 info,path=row.split('\t',1);expected=info.split()[2]
 if path in meta['paths']:continue
 target=DEST/path
 if target.exists():assert gitsha(target.read_bytes())==expected;continue
 local=ROOT/path;b=local.read_bytes() if local.exists() else b''
 if gitsha(b)!=expected:b=subprocess.check_output(['git','cat-file','blob',expected],cwd=ROOT)
 assert gitsha(b)==expected
 target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(b)
 restored.append({'path':path,'base_git_blob':expected,'sha256':sha(target)})
# Exact fixture materialization already declared by cloud delivery-regression.yml.
external=[]
csv='data/ic_510500_put_proxy_validation_v1/sina_000905_index.csv'
target=DEST/csv;target.parent.mkdir(parents=True,exist_ok=True)
shutil.copyfile(AUTO/'tests/fixtures/icim/sina_000905_index.csv',target)
external.append({'target':csv,'sha256':sha(target),'source':'automation/tests/fixtures/icim/sina_000905_index.csv'})
shutil.copytree(AUTO/'tests/fixtures/icim/v14_ledger_20261008',DEST/'runtime/ic_im_v1_4_r1')
for p in (DEST/'runtime/ic_im_v1_4_r1').rglob('*'):
 if p.is_file():external.append({'target':p.relative_to(DEST).as_posix(),'sha256':sha(p),'source':'automation/tests/fixtures/icim/v14_ledger_20261008/'+p.relative_to(DEST/'runtime/ic_im_v1_4_r1').as_posix()})
prepared=subprocess.check_output([sys.executable,'-B','-X','utf8',str(AUTO/'tests/fixtures/icim/quarter_roll_20261013/prepare_fixture.py'),'--strategy-root',str(DEST)],cwd=ROOT).decode('utf8')
manifest=json.loads((DEST/'docs/ic_im_daily_release_20261023_repairs_v3_r3_manifest.json').read_text('utf8'))
assert all(sha(DEST/p)==v==sha(ROOT/p) for p,v in manifest['files'].items())
before=json.loads((OLD/'snapshot_gate_02/source_before.json').read_text('utf8'))
assert all(sha(DEST/p)==v==sha(ROOT/p) for p,v in before.items())
setup={'strategy_sha':meta['candidate']['sha'],'execution_matches':len(manifest['files']),'gate_bindings_match':len(before),'restored_base_files':restored,'external_cloud_fixtures':external,'quarter_fixture_preparation':json.loads(prepared),'new_publication_paths_required':[],'production_state_used':False}
(HERE/'fixture_recovery_receipt.json').write_text(json.dumps(setup,ensure_ascii=False,indent=2),'utf8')
print(json.dumps({'restored_base_files':len(restored),'external_cloud_files':len(external),'gate_bindings':len(before)}),flush=True)
with (HERE/'gate_process.log').open('w',encoding='utf8') as log:
 run=subprocess.run([sys.executable,'-B','-X','utf8',str(DEST/'run_ic_im_v1_4_delivery_tests.py'),'--out-dir',str(HERE/'complete_gate_01')],cwd=DEST,stdout=log,stderr=subprocess.STDOUT)
print('gate_exit',run.returncode)
sys.exit(run.returncode)
