from pathlib import Path
import hashlib,json,re,yaml
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
AUTO=ROOT/'quant_research_runs/20261023_daily_release_v3/automation_checkout'
remote=json.loads((HERE/'remote_metadata.json').read_text('utf-8'))
audited=json.loads((HERE/'verification_01/source_after.json').read_text('utf-8'))
def git_blob(raw):return hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
rows=[]
for item in remote['files']:
    raw=(AUTO/item['path']).read_bytes()
    row={**item,'local_git_blob_sha':git_blob(raw),'local_sha256':hashlib.sha256(raw).hexdigest()}
    row['matches_remote']=row['sha']==row['local_git_blob_sha']
    row['matches_independently_tested_snapshot']=row['local_sha256']==audited[item['path']]
    rows.append(row)
workflow_path='.github/workflows/ic-im-v1-4-daily-digest.yml'
workflow=(AUTO/workflow_path).read_bytes()
steps=yaml.safe_load(workflow)['jobs']['send']['steps']
pins=[s['with']['ref'] for s in steps if s.get('with',{}).get('repository')=='liuruojiang/ic-im-rolling-arbitrage']
readiness_path='.github/workflows/ic-im-v1-4-delivery-readiness.yml'
readiness=(AUTO/readiness_path).read_bytes()
request_workflow_matches=git_blob(readiness)==remote['readiness_workflow_sha']
ready_steps=[s for job in yaml.safe_load(readiness)['jobs'].values() for s in job.get('steps',[])]
uploads=[s.get('with',{}).get('name') for s in ready_steps if 'upload-artifact@' in s.get('uses','')]
smtp_commands=[s.get('run','') for s in ready_steps if 'check_mail_transport.py' in s.get('run','')]
receipt={'status':'PASS','automation_merge_commit':remote['merge_commit'],'request_commit':remote['request_commit'],'strategy_pins':pins,'files':rows,'default_branch_workflow_matches':git_blob(workflow)==remote['default_branch_workflow_sha'],'readiness_workflow_matches_prior_tested_bytes':request_workflow_matches and hashlib.sha256(readiness).hexdigest()==audited[readiness_path],'readiness_artifact_upload_names':uploads,'readiness_smtp_commands':smtp_commands,'readiness_has_send_report_command':any('send_report.py' in s.get('run','') for s in ready_steps),'smtp_executed_by_auditor':False,'remote_writes_by_auditor':False,'production_ledger_writes_by_auditor':False,'scope':'immutable remote blob identities versus independently tested local byte hashes; no claim about readiness completion or recipient delivery'}
assert all(r['matches_remote'] and r['matches_independently_tested_snapshot'] for r in rows)
assert pins==['e3f26824a8ff0a89b6125d4385f75e0a6cfa2a7e']
assert receipt['default_branch_workflow_matches'] and receipt['readiness_workflow_matches_prior_tested_bytes']
assert not receipt['readiness_has_send_report_command']
assert len(uploads)==1 and uploads[0].startswith('ic-im-v1-4-r1-readiness-')
(HERE/'postpublication_receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),'utf-8')
print(json.dumps({k:v for k,v in receipt.items() if k!='files'},ensure_ascii=False))
