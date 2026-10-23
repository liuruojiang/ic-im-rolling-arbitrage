"""Verify the narrow production delta, bound tests, recovery and collector."""
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


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def read(p):
    return json.loads(p.read_text(encoding='utf-8-sig'))


def hashes(folder):
    return {p.relative_to(ROOT).as_posix(): sha(p) for p in folder.rglob('*') if p.is_file()}


baseline = read(RUN / 'baseline.json')
assert hashes(ROOT / 'runtime') == baseline['runtime']
assert all(sha(ROOT / n) == h for n, h in baseline['evidence'].items())
assert all(sha(RUN / 'backup' / n) == h for n, h in baseline['backups'].items())
changed = {n for n, h in baseline['production'].items() if sha(ROOT / n) != h}
assert changed == {'ic_im_signal_evidence_v1.py'}, changed
old_bindings_changed = {n for n, h in baseline['previous_bindings'].items() if sha(ROOT / n) != h}
assert old_bindings_changed == changed, old_bindings_changed
spec = ROOT / 'docs/ic_im_evidence_http_date_20261022_v1_spec.md'
assert sha(spec) == baseline['spec_sha256'] == Path(str(spec) + '.sha256').read_text(encoding='utf-8').split()[0]
assert sha(ROOT / 'docs/ic_im_evidence_conflict_review_20261022_v1_spec.md') == baseline['previous_spec_sha256']
assert (ROOT / 'notes/mainline_registry.md').read_text(encoding='utf-8').endswith(
    (RUN / 'backup/notes/mainline_registry.md').read_text(encoding='utf-8').split('\n', 1)[1])

receipt = read(RUN / 'accepted_02/receipt.json')
assert receipt['exit_code'] == 0 and receipt['counts'] == {'tests': 2959, 'failures': 0, 'errors': 0, 'skipped': 0}
before = read(RUN / 'accepted_02/source_before.json')
after = read(RUN / 'accepted_02/source_after.json')
assert before == after and all(sha(ROOT / n) == h for n, h in after.items())
selection = read(RUN / 'accepted_02/collection.json')
assert len(selection['selected']) == len(selection['executed']) == 2959
exclusions = read(RUN / 'exclusions.json')
assert len(selection['deselected']) == 22 and set(selection['deselected']) == set(exclusions)
assert sha(RUN / 'exclusions.json') == sha(ROOT / 'quant_research_runs/20261022_evidence_conflict_review_v1/exclusions.json')
assert sum(n.startswith('test_icim_round5_recording_semantics_20261021.py::') for n in selection['executed']) == 19
assert sum(n.startswith('test_icim_evidence_http_date_20261022.py::') for n in selection['executed']) == 18

recovery = read(RUN / 'real_recovery_01/receipt.json')
assert recovery['status'] == 'PASS' and len(recovery['cases']) == 5
assert all(sha(ROOT / n) == h for n, h in recovery['source_sha256'].items())
positive = [c for c in recovery['cases'] if not c.get('negative')]
assert len(positive) == 2 and all(c['zero_external_http'] for c in positive)
assert all(c['atomic_rejection'] for c in recovery['cases'] if c.get('negative'))
comparison = read(D / 'recovery_facts_comparison.json')
assert comparison['status'] == 'PASS' and not comparison['differences']
assert all(c['complete_product_states_equal'] and c['compared_signal_fields'] == {'IC': 212, 'IM': 227}
           for c in comparison['cases'])
probe = read(RUN / 'live_probe_01/receipt.json')
assert probe['status'] == 'COMPLETE' and probe['distinct_dates'] == 2 and probe['distinct_market_bodies'] == 1
assert probe['replay_matching_recordings'] == 2 and not probe['failures']
assert not probe['formal_signal_generated'] and not probe['original_close']

old_tree = ast.parse((RUN / 'backup/ic_im_signal_evidence_v1.py').read_text(encoding='utf-8-sig'))
new_tree = ast.parse((ROOT / 'ic_im_signal_evidence_v1.py').read_text(encoding='utf-8-sig'))
def functions(tree):
    return {node.name: ast.dump(node, include_attributes=False) for node in tree.body if isinstance(node, ast.FunctionDef)}
old_functions, new_functions = functions(old_tree), functions(new_tree)
assert set(new_functions) - set(old_functions) == {'_http_identity_headers'}
assert {n for n in old_functions if old_functions[n] != new_functions[n]} == {'raw_http_conflicts'}
assert (ROOT / 'poe_ic_im_mainline_v1_4_bot.py').is_file()
assert sha(ROOT / 'poe_ic_im_mainline_v1_4_bot.py') == baseline['production']['poe_ic_im_mainline_v1_4_bot.py']

pin = read(D / 'capture_source_pin.json')
assert len(pin['files']) == 28 and all(sha(ROOT / n) == h for n, h in pin['files'].items())
launch = read(D / 'launch_receipt.json')
pid = int(launch['pid'])
process = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
    f'Get-Process -Id {pid} -ErrorAction Stop | Select-Object Id,ProcessName,StartTime,CPU | ConvertTo-Json'],
    capture_output=True, encoding='utf-8')
assert process.returncode == 0
info = json.loads(process.stdout)
assert info['Id'] == pid and info['ProcessName'] == 'python'
reports = sorted((ROOT / 'data/icim_v14_original_inputs/capture_receipts').rglob('*.json'))
current = [(p, read(p)) for p in reports if read(p).get('pid') == pid]
assert current, 'new source collector has not completed its first cycle'
path, latest_attempt = current[-1]
assert latest_attempt['ledger_paths_and_bytes_unchanged']
assert latest_attempt['source_pin_sha256'] == sha(D / 'capture_source_pin.json')
assert latest_attempt['status'] in {'captured', 'incomplete', 'already_captured'}
packages = {}
for product in ['IC', 'IM']:
    reader, _ = adapter.load_original(bot, ROOT / 'data/icim_v14_original_inputs', product, date(2026, 10, 22))
    packages[product] = {'manifest': str(reader.manifest_path), 'sha256': reader.manifest_sha256,
                         'all_blobs_verified': True}
ledger = read(ROOT / 'runtime/ic_im_v1_4_r1/latest.json')
result = {'status': 'PASS', 'verified_at': datetime.now().astimezone().isoformat(),
          'production_files_changed': sorted(changed), 'runtime_files_unchanged': len(baseline['runtime']),
          'existing_archive_files_unchanged': len(baseline['evidence']), 'tests': receipt['counts'],
          'source_binding_files': len(after), 'original_exclusions_unchanged': 22,
          'fifth_round_tests_included': 19, 'new_Date_tests': 18, 'real_recovery_scenarios': 5,
          'normal_recovery_external_http': 0, 'real_repeat_Date_probe': probe,
          'real_recovery_facts_comparison': comparison,
          'collector': info, 'latest_attempt_receipt': str(path),
          'latest_attempt_status': latest_attempt['status'],
          'day_archive_status': latest_attempt['consistency_statistics']['status'],
          'statistics': latest_attempt['consistency_statistics'], 'available_packages': packages,
          'formal_ledger': {n: ledger[n] for n in ['verified_day', 'sequence', 'digest']},
          'source_pin_sha256': sha(D / 'capture_source_pin.json'),
          'generic_http_cache_added': False, 'strategy_calendar_sizing_changed': False,
          'historical_results_recalculated': False, 'remote_poe_configuration_changed': False,
          'network_limited_claim': 'SSE repeat probe succeeded; proxy remains intermittent, not a stability guarantee'}
(D / 'final_verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
patch = []
for name in ['ic_im_signal_evidence_v1.py', 'notes/mainline_registry.md', 'test_icim_evidence_http_date_20261022.py']:
    old = RUN / 'backup' / name
    a = old.read_text(encoding='utf-8-sig').splitlines(keepends=True) if old.exists() else []
    b = (ROOT / name).read_text(encoding='utf-8-sig').splitlines(keepends=True)
    patch.extend(difflib.unified_diff(a, b, fromfile='before/' + name if a else '/dev/null', tofile='after/' + name))
(D / 'source_delta.patch').write_text(''.join(patch), encoding='utf-8')
print(json.dumps({k: result[k] for k in ['status', 'tests', 'runtime_files_unchanged',
                                      'latest_attempt_status', 'day_archive_status']}, ensure_ascii=False))
