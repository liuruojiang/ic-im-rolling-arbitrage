"""Independent read-only check of the final, cloud-computed local live report."""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys
import zipfile
from datetime import datetime

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
C=HERE.parent/'coordinator'
H=C/'handoff-38856549173'
F=C/'local_daily_final'
sha=lambda b:hashlib.sha256(b).hexdigest()
blob=lambda b:hashlib.sha1(b'blob '+str(len(b)).encode()+b'\0'+b).hexdigest()
tree={x['path']:x for x in json.loads((C/'production_tree.json').read_text('utf-8'))}
ctree={x['path']:x for x in json.loads((C/'consumer_tree.json').read_text('utf-8'))}
snapshots={}
for kind,manifest_file,dirname,git_tree,prefix in [
 ('strategy','formal_runtime_final_manifest.json','formal_runtime_final',tree,''),
 ('consumer','formal_consumer_final_manifest.json','formal_consumer_final',ctree,'scripts/')]:
    manifest=json.loads((C/manifest_file).read_text('utf-8'))
    sha_errors=[]
    git_errors=[]
    for name,digest in manifest['files'].items():
        raw=(C/dirname/name).read_bytes()
        if sha(raw)!=digest: sha_errors.append(name)
        git_entry=git_tree.get(prefix+name)
        if git_entry is not None and blob(raw)!=git_entry['sha']:git_errors.append(name)
    snapshots[kind]={'files':len(manifest['files']),'sha256_errors':sha_errors,'git_blob_errors_against_saved_tree':git_errors}
    assert not sha_errors

handoff=json.loads((H/'handoff_manifest.json').read_text('utf-8'))
before={name:sha((H/name).read_bytes())for name in handoff['files']}
assert before==handoff['files']
verification=json.loads((H/'verification.json').read_text('utf-8'))
assert sha((H/'handoff.zip').read_bytes())==verification['zip_sha256']
with zipfile.ZipFile(H/'handoff.zip') as z:
    for name,digest in handoff['files'].items():
        assert sha(z.read(name))==digest
payload=json.loads((H/'report/result.json').read_text('utf-8'))
assert payload['status']=='ok' and payload['market_date']=='2026-10-16'
assert payload['publication_mode']=='realtime'
assert payload['advanced_sessions']==0 and payload['sequence']==15
assert payload['verified_day']=='2026-10-15'
assert datetime.fromisoformat(payload['report_generated_at']).hour<15
for product,s in payload['signals'].items():
    assert s['fear_greed_index']==51.36 and s['fear_data_date']=='2026-10-16'
    assert s['fear_cross_validation']=='matched'
    assert s['jiu_fear_greed_index']==2.28 and s['jiu_data_date']=='2026-10-15'
    assert s['jiu_reference_only'] is True
a,b=(payload['signals'][p]for p in ('IC','IM'))
assert {k:v for k,v in a.items()if k.startswith(('fear_','jiu_'))}=={k:v for k,v in b.items()if k.startswith(('fear_','jiu_'))}

meta=json.loads((F/'metadata.json').read_text('utf-8'))
assert meta['body']==(F/'ic_im_v1_4_digest.md').read_text('utf-8')
assert meta['html_body']==(F/'ic_im_v1_4_digest.html').read_text('utf-8')
assert Path(meta['attachment']).resolve()==(H/'report/ic_im_v1_4_realtime_signal.md').resolve()
assert '[盘中实时][月度Put需维护/需调整]' in meta['subject']
for name in ('ic_im_v1_4_digest.md','ic_im_v1_4_digest.html','local_verified_daily.md','local_verified_daily.html'):
    text=(F/name).read_text('utf-8')
    for term in ['9圈恐慌贪婪指数','2.280','2026-10-15','51.360','2026-10-16','仅供参考','510500P2612M06500','盘尾复核']:
        assert term in text,(name,term)
    assert '2026-10-16' in text and '当天收盘完成换仓' in text
assert '云端计算快照' in (F/'local_verified_daily.md').read_text('utf-8')

# Re-run the fixed consumer in a separate process without any network or SMTP.
out=HERE/'independent_live_render'
env=os.environ.copy()
env.update(PYTHONDONTWRITEBYTECODE='1',GITHUB_RUN_URL='')
cmd=[sys.executable,'-B','-X','utf8',str(C/'formal_consumer_final/build_ic_im_v1_4_digest.py'),
     '--result',str(H/'report/result.json'),'--report',str(H/'report/ic_im_v1_4_realtime_signal.md'),
     '--out-dir',str(out)]
result=subprocess.run(cmd,env=env,capture_output=True,text=True,encoding='utf-8',timeout=30)
(HERE/'independent_live_render.log').write_text(result.stdout+result.stderr,encoding='utf-8')
assert result.returncode==0
remeta=json.loads((out/'metadata.json').read_text('utf-8'))
assert remeta==meta
assert {name:sha((H/name).read_bytes())for name in handoff['files']}==before
cfg=Path('D:/Codex/home/automations/ic-im/automation.toml')
assert cfg.read_bytes()==(HERE/'automation.readonly.toml').read_bytes()
evidence={
 'verified_at':datetime.now().astimezone().isoformat(),
 'status':'PASS_WITH_WARNINGS','strategy_sha':handoff['strategy_sha'],
 'automation_sha':json.loads((C/'release.json').read_text('utf-8'))['automation_sha'],
 'source_run':38856549173,'source':'Fresh cloud-computed realtime research snapshot, independently rendered locally by fixed formal consumer',
 'report_generated_at':payload['report_generated_at'],'market_date':payload['market_date'],
 'snapshots':snapshots,'zip_sha256':verification['zip_sha256'],
 'checked_input_files':len(before),'input_manifest_unchanged':True,
 'independent_consumer_rerender_exact_metadata_equal':True,
 'markdown_html_and_subject_semantics_passed':True,'shared_source_snapshot_equal':True,
 'bfw':{'value':51.36,'data_date':'2026-10-16','cross_validation':'matched'},
 'jiu':{'value':2.28,'data_date':'2026-10-15','reference_only':True},
 'ic_candidate_contract':'510500P2612M06500','put_maintenance':'2026-10-16 same-day close, candidate requires end-of-session review',
 'external_config_sha256':sha(cfg.read_bytes()),'external_config_changed':False,
 'warnings':['Actual external task prompt remains unchanged; proposed 38-file update is not installed.',
             'Saved production_tree.json / consumer_tree.json appear to precede the final patch; 4 strategy and 1 consumer Git blob identities differ. Final SHA-256 manifests match all 437/14 files, but final remote Git provenance needs coordinator evidence.',
             'This verifies a local rendering of cloud computation; it is not a local direct-network recomputation.',
             'The live success snapshot uses both BFW sources; single-source/partial behavior is verified separately by injected regression cases.',
             '14:15 scheduled execution, 20:00 normal SMTP submission and Gmail inbox arrival are not evidenced by this no-send run.',
             'Existing valuation/rate proxy and missing individual option quotation metadata remain explicitly disclosed.'],
 'no_new_market_fetch':True,'no_ledger_continuation':True,'no_smtp':True,
 'outputs':{p.name:sha(p.read_bytes())for p in F.iterdir()if p.is_file()}}
(HERE/'final_live_local_independent_verification.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(evidence,ensure_ascii=False,indent=2))
