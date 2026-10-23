"""Verify audit evidence against fresh artifacts and unchanged protected trees."""
import ast
from datetime import datetime
import difflib
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
RUN = Path(__file__).resolve().parent.parent
D = RUN / 'delivery'
sys.path.insert(0, str(RUN))
import evidence_gate


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def hashes(folder):
    return {p.relative_to(ROOT).as_posix(): sha(p) for p in folder.rglob('*') if p.is_file()}


def functions(path):
    return {n.name: ast.dump(n, include_attributes=False) for n in ast.parse(
        path.read_text(encoding='utf-8-sig')).body if isinstance(n, (ast.FunctionDef, ast.ClassDef))}


def main():
    stage = sys.argv[1]
    baseline = read(RUN / 'baseline.json')
    counts = evidence_gate.validate_receipt(RUN / stage)
    assert counts['tests'] >= 3109, counts
    source = read(RUN / stage / 'source_after.json')
    assert all((ROOT / n).is_file() and sha(ROOT / n) == h for n, h in source.items())
    selection = read(RUN / stage / 'collection.json')
    assert len(selection['deselected']) == 22
    assert set(selection['deselected']) == set(read(RUN / 'exclusions.json'))
    old_run = ROOT / 'quant_research_runs/20261023_production_provenance_v1'
    assert sha(RUN / 'exclusions.json') == sha(old_run / 'exclusions.json')
    assert all(sha(RUN / 'backup' / n) == h for n, h in baseline['production'].items())
    assert hashes(ROOT / 'runtime') == baseline['runtime']
    assert hashes(ROOT / 'outputs') == baseline['frozen_outputs']
    assert all((ROOT / n).is_file() and sha(ROOT / n) == h for n, h in baseline['original_evidence'].items())
    spec = ROOT / 'docs/ic_im_multiround_adversarial_20261023_v1_spec.md'
    assert sha(spec) == baseline['spec_sha256'] == Path(str(spec) + '.sha256').read_text(encoding='utf-8').split()[0]
    changed = {n for n, h in baseline['production'].items() if sha(ROOT / n) != h}
    assert changed == {'poe_ic_im_mainline_v1_4_bot.py', 'ic_im_evidence_adapter_v1.py',
                       'ic_im_signal_evidence_v1.py', 'poe_ic_im_v1_4_server.py', 'capture_ic_im_v1_4_inputs.py'}, changed
    previous_changed = sorted(n for n, h in baseline['previous_binding'].items() if sha(ROOT / n) != h)
    fixtures = read(D / 'fixture_changes.json')['changes']
    assert set(previous_changed) == changed | set(fixtures), previous_changed
    for name, pair in fixtures.items():
        assert sha(RUN / 'backup' / name) == pair['before_sha256'] == baseline['previous_binding'][name]
        assert sha(ROOT / name) == pair['after_sha256']
    recovery = read(RUN / 'real_recovery_01/receipt.json')
    assert recovery['status'] == 'PASS' and len(recovery['cases']) == 5
    assert all(sha(ROOT / n) == h for n, h in recovery['source_sha256'].items())
    assert all(c['zero_external_http'] for c in recovery['cases'] if not c.get('negative'))
    assert all(c['atomic_rejection'] for c in recovery['cases'] if c.get('negative'))
    comparison = read(D / 'recovery_facts_comparison.json')
    assert comparison['status'] == 'PASS' and not comparison['differences']
    assert all(c['complete_product_states_equal'] and c['compared_signal_fields'] == {'IC': 212, 'IM': 227}
               for c in comparison['cases'])
    preflight = read(D / 'preflight_checks.json')
    assert len(preflight) == 2 and all(p['status'] == 'PASS' and not p['network_attempts'] for p in preflight)
    pin = read(D / 'capture_source_pin.json')
    assert len(pin['files']) == 28 and all(sha(ROOT / n) == h for n, h in pin['files'].items())
    launch = read(D / 'launch_receipt.json')
    pid = int(launch['pid'])
    process = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
        f'Get-Process -Id {pid} -ErrorAction Stop | Select-Object Id,ProcessName,StartTime | ConvertTo-Json'],
        capture_output=True, encoding='utf-8')
    assert process.returncode == 0, process.stderr
    info = json.loads(process.stdout)
    assert info['Id'] == pid and info['ProcessName'] == 'python'
    receipts = sorted((ROOT / 'data/icim_v14_original_inputs/capture_receipts').rglob('*.json'))
    current = [(p, read(p)) for p in receipts if read(p).get('pid') == pid]
    assert current
    path, cycle = current[-1]
    assert cycle['source_pin_sha256'] == sha(D / 'capture_source_pin.json')
    assert cycle['ledger_paths_and_bytes_unchanged']
    assert cycle['status'] == 'waiting_for_close', cycle['status']
    assert cycle['started_at'][:10] == '2026-10-23'
    delta, ast_delta = [], {}
    for n in sorted(changed):
        a, b = RUN / 'backup' / n, ROOT / n
        delta.extend(difflib.unified_diff(a.read_text(encoding='utf-8-sig').splitlines(True),
                    b.read_text(encoding='utf-8-sig').splitlines(True), fromfile='before/' + n, tofile='after/' + n))
        x, y = functions(a), functions(b)
        ast_delta[n] = {'added': sorted(set(y) - set(x)), 'removed': sorted(set(x) - set(y)),
                        'changed': sorted(k for k in set(x) & set(y) if x[k] != y[k])}
    (D / 'source_delta.patch').write_text(''.join(delta), encoding='utf-8')
    ledger = read(ROOT / 'runtime/ic_im_v1_4_r1/latest.json')
    report = {'status': 'PASS', 'verified_at': datetime.now().astimezone().isoformat(),
        'tests': counts, 'stage': stage, 'source_binding_files': len(source),
        'original_exclusions_unchanged': 22,
        'fifth_round_tests_included': sum(n.startswith('test_icim_round5_recording_semantics_20261021.py::') for n in selection['executed']),
        'new_audit_tests': sum(n.startswith('test_icim_ma23_') for n in selection['executed']),
        'production_files_changed': sorted(changed), 'previous_binding_changes': previous_changed,
        'ast_delta': ast_delta, 'formal_runtime_files_unchanged': len(baseline['runtime']),
        'existing_original_files_unchanged': len(baseline['original_evidence']),
        'frozen_output_files_unchanged': len(baseline['frozen_outputs']),
        'formal_ledger': {k: ledger[k] for k in ['verified_day', 'sequence', 'digest']},
        'real_recovery_scenarios': 5, 'read_only_preflights': 2,
        'normal_recovery_external_http': 0, 'recovery_field_comparison': comparison,
        'collector': info, 'collector_receipt': str(path), 'collector_status': cycle['status'],
        'source_pin_sha256': sha(D / 'capture_source_pin.json'),
        'strategy_sizing_calendar_math_changed_this_round': False, 'historical_nav_recalculated_this_round': False,
        'remote_Poe_deployed': False, 'hard_wallclock_45s_guarantee': False}
    (D / 'final_verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
