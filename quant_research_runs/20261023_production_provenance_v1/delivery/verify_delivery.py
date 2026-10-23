"""Sign off this round independently of earlier accepted counts."""
import ast
from datetime import date, datetime
import difflib
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
RUN = Path(__file__).resolve().parent.parent
D = RUN / 'delivery'
sys.path.insert(0, str(ROOT))
import ic_im_evidence_adapter_v1 as adapter
import poe_ic_im_mainline_v1_4_bot as bot


def read(p):
    return json.loads(p.read_text(encoding='utf-8-sig'))


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def hashes(folder):
    return {p.relative_to(ROOT).as_posix(): sha(p) for p in folder.rglob('*') if p.is_file()}


def functions(path):
    return {n.name: ast.dump(n, include_attributes=False) for n in ast.parse(
        path.read_text(encoding='utf-8-sig')).body if isinstance(n, ast.FunctionDef)}


baseline = read(RUN / 'baseline.json')
assert hashes(ROOT / 'runtime') == baseline['runtime']
assert all(sha(ROOT / n) == h for n, h in baseline['evidence'].items())
assert all(sha(RUN / 'backup' / n) == h for n, h in baseline['backups'].items())
changed = {n for n, h in baseline['production'].items() if sha(ROOT / n) != h}
assert changed == {'poe_ic_im_mainline_v1_4_bot.py', 'ic_im_evidence_adapter_v1.py'}, changed
assert {n for n, h in baseline['previous_bindings'].items() if sha(ROOT / n) != h} == changed
spec = ROOT / 'docs/ic_im_production_provenance_20261023_v1_spec.md'
assert sha(spec) == baseline['spec_sha256'] == Path(str(spec) + '.sha256').read_text(encoding='utf-8').split()[0]
assert (ROOT / 'notes/mainline_registry.md').read_text(encoding='utf-8').endswith(
    (RUN / 'backup/notes/mainline_registry.md').read_text(encoding='utf-8').split('\n', 1)[1])
receipt = read(RUN / 'accepted_01/receipt.json')
assert receipt['exit_code'] == 0 and receipt['counts'] == {'tests': 2982, 'failures': 0, 'errors': 0, 'skipped': 0}
before, after = read(RUN / 'accepted_01/source_before.json'), read(RUN / 'accepted_01/source_after.json')
assert before == after and all(sha(ROOT / n) == h for n, h in after.items())
selection = read(RUN / 'accepted_01/collection.json')
assert len(selection['selected']) == len(selection['executed']) == 2982
assert len(selection['deselected']) == 22 and set(selection['deselected']) == set(read(RUN / 'exclusions.json'))
assert sha(RUN / 'exclusions.json') == sha(ROOT / 'quant_research_runs/20261022_evidence_http_date_v1/exclusions.json')
assert sum(n.startswith('test_icim_round5_recording_semantics_20261021.py::') for n in selection['executed']) == 19
assert sum(n.startswith('test_icim_production_provenance_20261023.py::') for n in selection['executed']) == 23

old = functions(RUN / 'backup/poe_ic_im_mainline_v1_4_bot.py')
new = functions(ROOT / 'poe_ic_im_mainline_v1_4_bot.py')
assert set(new) - set(old) == {'_collect_quote_batches'}
assert {n for n in old if old[n] != new[n]} == {
    '_prefetch_mo_replay_verification', '_sina_put_detail_batches', '_fetch_510500_chain_for_day', '_source_failure_error'}
old_adapter, new_adapter = functions(RUN / 'backup/ic_im_evidence_adapter_v1.py'), functions(ROOT / 'ic_im_evidence_adapter_v1.py')
assert set(old_adapter) == set(new_adapter)
assert {n for n in old_adapter if old_adapter[n] != new_adapter[n]} == {'_keyed'}
tree = ast.parse((ROOT / 'poe_ic_im_mainline_v1_4_bot.py').read_text(encoding='utf-8-sig'))
cache_users = {n.name for n in tree.body if isinstance(n, ast.FunctionDef) and
               any(isinstance(node, ast.Name) and node.id == '_IC_QUOTE_QUERY_CACHE' for node in ast.walk(n))}
assert cache_users == {'_fetch_510500_chain_for_day', 'build_live_trade_signal'}
assert read(D / 'A_H_provenance.json')['status'] == 'PASS'
stats = read(D / 'timeout_frequency.json')
assert not stats['unreadable_receipts']
assert set(stats['days']['2026-10-22']['products']) == {'IC', 'IM'}
recovery = read(RUN / 'real_recovery_01/receipt.json')
assert recovery['status'] == 'PASS' and len(recovery['cases']) == 5
assert all(sha(ROOT / n) == h for n, h in recovery['source_sha256'].items())
assert all(c['zero_external_http'] for c in recovery['cases'] if not c.get('negative'))
assert all(c['atomic_rejection'] for c in recovery['cases'] if c.get('negative'))
comparison = read(D / 'recovery_facts_comparison.json')
assert comparison['status'] == 'PASS' and not comparison['differences']
assert all(c['complete_product_states_equal'] and c['compared_signal_fields'] == {'IC':212, 'IM':227}
           for c in comparison['cases'])
preflight = read(D / 'preflight_checks.json')
assert len(preflight) == 2 and all(p['status'] == 'PASS' and not p['network_attempts'] for p in preflight)
pin = read(D / 'capture_source_pin.json')
assert len(pin['files']) == 28 and all(sha(ROOT / n) == h for n, h in pin['files'].items())
launch = read(D / 'launch_receipt.json')
pid = int(launch['pid'])
process = subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',
    f'Get-Process -Id {pid} -ErrorAction Stop | Select-Object Id,ProcessName,StartTime,CPU | ConvertTo-Json'],
    capture_output=True, encoding='utf-8')
assert process.returncode == 0
info = json.loads(process.stdout)
assert info['Id'] == pid and info['ProcessName'] == 'python'
paths = sorted((ROOT / 'data/icim_v14_original_inputs/capture_receipts').rglob('*.json'))
current = [(p, read(p)) for p in paths if read(p).get('pid') == pid]
assert current
path, cycle = current[-1]
assert cycle['source_pin_sha256'] == sha(D / 'capture_source_pin.json')
assert cycle['ledger_paths_and_bytes_unchanged']
assert cycle['status'] == 'waiting_for_close', cycle['status']
assert cycle['started_at'][:10] == '2026-10-23'
available = {}
for product in ['IC','IM']:
    reader, _ = adapter.load_original(bot, ROOT / 'data/icim_v14_original_inputs', product, date(2026,10,22))
    available[product] = {'manifest': str(reader.manifest_path), 'sha256': reader.manifest_sha256, 'all_blobs_verified': True}
ledger = read(ROOT / 'runtime/ic_im_v1_4_r1/latest.json')
result = {'status':'PASS', 'verified_at':datetime.now().astimezone().isoformat(),
    'production_files_changed':sorted(changed), 'runtime_files_unchanged':len(baseline['runtime']),
    'existing_archive_files_unchanged':len(baseline['evidence']), 'tests':receipt['counts'],
    'source_binding_files':len(after), 'original_exclusions_unchanged':22,
    'new_followup_tests':23, 'fifth_round_tests_included':19,
    'real_recovery_scenarios':5, 'read_only_preflights':2, 'normal_recovery_external_http':0,
    'recovery_field_comparison':comparison, 'A_H_provenance':'A_H_provenance.json',
    'timeout_statistics':'timeout_frequency.json', 'available_original_packages':available,
    'collector':info, 'collector_receipt':str(path), 'collector_status':cycle['status'],
    'source_pin_sha256':sha(D/'capture_source_pin.json'),
    'formal_ledger':{k:ledger[k] for k in ['verified_day','sequence','digest']},
    'strategy_sizing_calendar_math_changed':False, 'historical_nav_recalculated':False,
    'generic_URL_cache_added':False, 'remote_Poe_deployed':False,
    'hard_wallclock_45s_guarantee':False,
    'budget_contract':'same deadline covers workers/wait/shutdown; no over-budget successful return; running requests can have a non-killable tail'}
(D/'final_verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
patch = []
for name in sorted(changed | {'notes/mainline_registry.md','test_icim_production_provenance_20261023.py'}):
    p = RUN / 'backup' / name
    a = p.read_text(encoding='utf-8-sig').splitlines(keepends=True) if p.exists() else []
    b = (ROOT / name).read_text(encoding='utf-8-sig').splitlines(keepends=True)
    patch.extend(difflib.unified_diff(a,b,fromfile='before/'+name if a else '/dev/null',tofile='after/'+name))
(D/'source_delta.patch').write_text(''.join(patch),encoding='utf-8')
print(json.dumps({k:result[k] for k in ['status','tests','runtime_files_unchanged','existing_archive_files_unchanged','collector_status']},ensure_ascii=False))
