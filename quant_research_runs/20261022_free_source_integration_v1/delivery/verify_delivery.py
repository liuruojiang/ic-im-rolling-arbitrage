"""Read-only verification of final source, preserved artifacts, and acceptance receipts."""
from pathlib import Path
from datetime import datetime
import ast, difflib, hashlib, json

ROOT = Path(__file__).resolve().parents[3]
RUN = ROOT / 'quant_research_runs/20261022_free_source_integration_v1'
DELIVERY = RUN / 'delivery'
def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def read(path): return json.loads(path.read_text(encoding='utf-8-sig'))
base = read(RUN/'baseline.json')
production_changes = {'poe_ic_im_mainline_v1_4_bot.py', 'ic_im_evidence_adapter_v1.py', 'ic_im_signal_evidence_v1.py'}
allowed = production_changes | {'notes/mainline_registry.md'}
changed, unexpected = [], []
for name, expected in base['protected'].items():
    actual = digest(ROOT/name) if (ROOT/name).is_file() else None
    if actual != expected:
        changed.append(name)
        if name not in allowed: unexpected.append(name)
assert set(changed) == allowed, (changed, unexpected)
backups = {name: digest(RUN/'backup'/name) == expected for name, expected in base['backups'].items()}
assert all(backups.values()), backups
old_registry=(RUN/'backup/notes/mainline_registry.md').read_text(encoding='utf-8')
new_registry=(ROOT/'notes/mainline_registry.md').read_text(encoding='utf-8')
assert new_registry.endswith(old_registry.split('\n',1)[1]), 'old registry content was not preserved'
runtime = {p.relative_to(ROOT).as_posix():digest(p) for p in (ROOT/'runtime').rglob('*') if p.is_file()}
assert runtime == base['runtime'], 'formal runtime paths/bytes drift'
receipt = read(RUN/'accepted_02/receipt.json')
assert receipt['exit_code'] == 0 and receipt['counts'] == {'tests':2918,'failures':0,'errors':0,'skipped':0}
before, after = read(RUN/'accepted_02/source_before.json'), read(RUN/'accepted_02/source_after.json')
assert before == after
assert all((ROOT/n).is_file() and digest(ROOT/n)==h for n,h in after.items()), 'accepted source no longer current'
selection = read(RUN/'accepted_02/collection.json')
exclusions = read(RUN/'exclusions.json')
assert len(exclusions)==22 and set(selection['deselected']) == set(exclusions)
assert len(selection['selected']) == len(selection['executed']) == 2918
old_exclusions = ROOT/'quant_research_runs/20261022_calendar_capture_review_v1/exclusions.json'
assert digest(old_exclusions) == digest(RUN/'exclusions.json')
round5 = [n for n in selection['executed'] if n.startswith('test_icim_round5_recording_semantics_20261021.py::')]
assert len(round5)==19, len(round5)
real = read(RUN/'real_03/receipt.json'); facts=read(RUN/'real_03/facts_comparison.json')
assert real['status']=='PASS_FACTS_AND_VERIFIED_REPLAY_METADATA' and facts['pass']
assert not facts['facts_differences'] and not facts['audit_errors']
assert real['formal_runtime_unchanged'] and real['anchors_restored']
assert real['stages']['isolated_append']=={'verified_day':'2026-10-22','sequence':20}
r=real['stages']['replay']
assert r['original_urls'] and r['address_subset'] and not r['requests'] and not r['misses'] and not r['reported_missing']
assert r['complete'] and r['eligible'] and 'iv.monitor' in r['roles_read']
assert all(digest(ROOT/n)==h for n,h in real['source_sha256'].items())
pin = read(DELIVERY/'capture_source_pin.json')
assert pin['schema']=='icim_capture_source_pin_v1' and len(pin['files'])==28 and 'im_put_policy.py' in pin['files']
assert all(digest(ROOT/n)==h for n,h in pin['files'].items())
spec_names=['ic_im_free_source_integration_20261022_v1_spec.md','ic_im_free_source_replay_contract_20261022_v1_spec.md','ic_im_bounded_parallel_capture_20261022_v1_spec.md','ic_im_sina_batch_wire_20261022_v1_spec.md']
specs={n:digest(ROOT/'docs'/n) for n in spec_names}
assert all((ROOT/'docs'/(n+'.sha256')).read_text(encoding='utf-8').split()[0]==h for n,h in specs.items())
def functions(path):
    tree=ast.parse(path.read_text(encoding='utf-8-sig'))
    return {n.name:ast.dump(n,include_attributes=False) for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))}
old,new=functions(RUN/'backup/poe_ic_im_mainline_v1_4_bot.py'),functions(ROOT/'poe_ic_im_mainline_v1_4_bot.py')
unchanged=[n for n in old if old[n]==new.get(n) and any(s in n.lower() for s in ['historical','black','delta','third_friday','fourth_wednesday','exchange_trading'])]
assert all(n in unchanged for n in ['historical_replay','fetch_sse_510500_historical_quote','fetch_sse_existing_put_historical_quote','_third_friday','_fourth_wednesday','_bs_price_delta'])
patch=[]
for name in sorted(allowed|{'capture_ic_im_v1_4_inputs.py','test_icim_calendar_capture_review_20261022.py'}):
    orig=RUN/'backup'/name
    a=orig.read_text(encoding='utf-8-sig').splitlines(keepends=True) if orig.exists() else []
    b=(ROOT/name).read_text(encoding='utf-8-sig').splitlines(keepends=True)
    patch.extend(difflib.unified_diff(a,b,fromfile='before/'+name if a else '/dev/null',tofile='after/'+name))
(DELIVERY/'source_delta.patch').write_text(''.join(patch),encoding='utf-8')
latest=read(ROOT/'runtime/ic_im_v1_4_r1/latest.json')
result={'verified_at':datetime.now().astimezone().isoformat(),'status':'PASS',
 'protected_objects':len(base['protected']),'intended_production_changes':sorted(production_changes),'intended_documentation_changes':['notes/mainline_registry.md'],
 'unchanged_protected_objects':len(base['protected'])-len(changed),'unexpected_changes':unexpected,
 'backup_checks':backups,'formal_runtime_files':len(runtime),'formal_runtime_paths_and_bytes_unchanged':True,
 'formal_ledger':{k:latest[k] for k in ['sequence','verified_day','digest']},
 'accepted_tests':receipt['counts'],'accepted_binding_files':len(after),'accepted_binding_still_current':True,
 'original_exclusions_unchanged':22,'fifth_round_tests_executed':len(round5),
 'fixture_adaptations':read(RUN/'legacy_fixture_adaptations.json'),
 'real_ic_facts_acceptance':real['status'],'real_source_sha256':real['source_sha256'],
 'source_pin_files':len(pin['files']),'source_pin_sha256':digest(DELIVERY/'capture_source_pin.json'),
 'math_and_history_functions_ast_unchanged':unchanged,'historical_nav_rerun_this_round':False,
 'spec_sha256':specs,'launch':read(DELIVERY/'launch_receipt.json')}
(DELIVERY/'final_verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({k:result[k] for k in ['status','protected_objects','formal_runtime_files','accepted_tests','accepted_binding_files','source_pin_files']},ensure_ascii=False))
