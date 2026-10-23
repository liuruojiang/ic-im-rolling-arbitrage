from pathlib import Path
import datetime, hashlib, json, zipfile, subprocess

ROOT = Path.cwd()
OUT = Path(__file__).resolve().parent
OLD = ROOT/'quant_research_runs/20261023_daily_release_sync_v1'
STATE = ROOT/'runtime/ic_im_v1_4_r1'
def sha(raw): return hashlib.sha256(raw).hexdigest()
def read(p): return json.loads(p.read_text('utf-8'))
def snapshot(): return {p.relative_to(STATE).as_posix():sha(p.read_bytes()) for p in STATE.rglob('*') if p.is_file()}
before=snapshot()
baseline=read(ROOT/'quant_research_runs/20261023_redteam_repairs_v3/baseline.json')['files']
archive=OLD/'readiness_39892433587.zip'; summary=read(OLD/'readiness_39892433587_summary.json')
result={'status':'CONFIRMED_EXISTING_REMOTE_HANDOFF_LINEAGE_WRITER_UNATTRIBUTED','checked_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'network_used':False,'formal_ledger_writes':False,'source_run':39892433587,'source_job':summary['job'],'source_strategy_sha':summary['strategy_sha'],'source_automation_sha':summary['automation_sha'],'archive_sha256':sha(archive.read_bytes()),'archive_matches_logged_handoff_sha':sha(archive.read_bytes())=='04baf5d42d657095a1770a38f57037926de2ffed8211662739407b1633af942a','archive_unchanged_from_pre_repair_git_commit':sha(subprocess.check_output(['git','show','58986fda2c17df93eb3925bf09a1cebd55a7869e:'+archive.relative_to(ROOT).as_posix()],cwd=ROOT))==sha(archive.read_bytes()),'run_summary_unchanged_from_pre_repair_git_commit':sha(subprocess.check_output(['git','show','58986fda2c17df93eb3925bf09a1cebd55a7869e:'+(OLD/'readiness_39892433587_summary.json').relative_to(ROOT).as_posix()],cwd=ROOT))==sha((OLD/'readiness_39892433587_summary.json').read_bytes()),'remote_summary':summary}
with zipfile.ZipFile(archive) as z:
    manifest=json.loads(z.read('handoff_manifest.json'))
    result['handoff_manifest']={k:v for k,v in manifest.items() if k!='files'}
    result['handoff_file_count']=len(manifest['files'])
    result['archive_members_exact']=len(z.namelist())==len(set(z.namelist())) and set(z.namelist())==set(manifest['files'])|{'handoff_manifest.json'}
    result['handoff_hash_mismatches']=[name for name,digest in manifest['files'].items() if sha(z.read(name))!=digest]
    ledger={name[7:]:z.read(name) for name in manifest['files'] if name.startswith('ledger/')}
    result['local_vs_handoff_byte_differences']=[name for name,data in ledger.items() if not (STATE/name).is_file() or (STATE/name).read_bytes()!=data]
    result['ledger_files_compared']=len(ledger)
    result['seq0_through_19_match_pre_repair_baseline']=all(baseline['runtime/ic_im_v1_4_r1/'+name]==sha(data) for name,data in ledger.items() if name.startswith('journal/') and not name.startswith('journal/000020'))
    current=json.loads(ledger['latest.json']); last=None; sequences=[]
    for name in sorted(n for n in ledger if n.startswith('journal/')):
        rec=json.loads(ledger[name]); payload={k:v for k,v in rec.items() if k!='digest'}
        calculated=sha(json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8'))
        assert rec['digest']==calculated and rec['previous_digest']==last
        last=rec['digest'];sequences.append(rec['sequence'])
    assert sequences==list(range(current['sequence']+1)) and last==current['digest']
    assert ledger['latest.json']==ledger['journal/000020-2026-10-22.json']
    result['canonical_hash_chain_valid']=True
    result['seq20_record']={k:current[k] for k in ['sequence','verified_day','updated_at','source','previous_digest','digest']}
    result['seq20_file_sha256']=sha(ledger['latest.json'])
    result['preflight']=json.loads(z.read('report/preflight.json'))
    result['local_preview_matches_handoff']=all((OLD/'local_handoff_preview'/name).read_bytes()==data for name,data in [('ledger/'+n,d) for n,d in ledger.items()])
result['formal_ledger_unchanged_during_this_check']=before==snapshot()
automation=Path('D:/Codex/home/automations/ic-im/runs/20261023/daily_intraday_1428/icim')
names=['adoption_apply_command.json','ledger_adoption/adoption_result.json','production_chain_verification.json','official_artifact_api.json','official_run_api.json']
saved={n:read(automation/n) for n in names}
for name in names:
    dest=OUT/'local_automation_receipts'/name; dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes((automation/name).read_bytes())
command=saved['adoption_apply_command.json']; adopted=saved['ledger_adoption/adoption_result.json']; chain=saved['production_chain_verification.json']; artifact=saved['official_artifact_api.json']; run=saved['official_run_api.json']
official=automation/'official_ledger.zip'
assert artifact['id']==adopted['artifact_id']==manifest['source_artifact_id']==12423604023
assert run['id']==artifact['workflow_run']['id']==adopted['run_id']==39774840955
assert run['status']=='completed' and run['conclusion']=='success'
assert command['exit_code']==adopted['exit_code']==0 and adopted['status']=='ADOPTED' and adopted['applied'] is True
assert sha(official.read_bytes())==adopted['archive_sha256']==artifact['digest'].removeprefix('sha256:')
assert official.stat().st_size==artifact['size_in_bytes']==137231
with zipfile.ZipFile(official) as z:
    assert set(z.namelist())==set(ledger) and len(z.namelist())==len(ledger)
    assert all(z.read(name)==data for name,data in ledger.items())
assert chain['full_chain'] is True and chain['migration'] is True
assert all(sha((STATE/name).read_bytes())==digest for name,digest in chain['file_sha256'].items())
result['status']='CONFIRMED_LEGAL_REMOTE_LEDGER_ADOPTION_BY_LOCAL_DAILY_AUTOMATION'
result['local_adoption']={'automation_run_directory':str(automation),'command':command,'result':adopted,'saved_receipt_hashes':{n:sha((automation/n).read_bytes()) for n in names},'official_run_id':run['id'],'official_run_url':run['html_url'],'official_artifact_id':artifact['id'],'official_artifact_name':artifact['name'],'official_archive_sha256':sha(official.read_bytes()),'official_archive_bytes':official.stat().st_size,'official_archive_matches_handoff_and_current_ledger':True,'production_receipt_all_23_json_match':len(chain['file_sha256'])==23}
result['attribution_limit']='The local daily automation executed adopt_official_pinned.py --apply at 14:54:41..53 +08:00 and recorded successful adoption of the official successful run 39774840955 artifact 12423604023. This identifies the recorded writer workflow and exact byte source. It does not transform historical seq20 into a v3-generated record or undo the earlier incorrect claim that formal runtime was unchanged. No network or production write was performed during this verification.'
assert all(result[k] for k in ['archive_matches_logged_handoff_sha','archive_unchanged_from_pre_repair_git_commit','run_summary_unchanged_from_pre_repair_git_commit','archive_members_exact','seq0_through_19_match_pre_repair_baseline','canonical_hash_chain_valid','local_preview_matches_handoff','formal_ledger_unchanged_during_this_check'])
assert not result['handoff_hash_mismatches'] and not result['local_vs_handoff_byte_differences']
(OUT/'seq20_origin_verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf-8')
print(json.dumps(result,ensure_ascii=False,indent=2))
