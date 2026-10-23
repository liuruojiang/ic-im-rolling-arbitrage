from pathlib import Path
import json
import os
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
RUN = Path(__file__).resolve().parent.parent
D = RUN / 'delivery'
missing = RUN / 'missing_original_inputs'
missing.mkdir(exist_ok=False)
report = []
for name, directory, expected in [('valid', ROOT / 'data/icim_v14_original_inputs', 0),
                                  ('missing', missing, 2)]:
    command = [sys.executable, '-X', 'utf8', '-B', str(D / 'check_recovery_readiness.py'),
        '--state-dir', str(ROOT / 'runtime/ic_im_v1_4_r1'), '--evidence-dir', str(directory),
        '--source-pin', str(D / 'capture_source_pin.json'), '--market-day', '2026-10-22']
    result = subprocess.run(command, capture_output=True, encoding='utf-8',
                            env={**os.environ, 'PYTHONIOENCODING': 'utf-8'})
    (D / ('readiness_' + name + '.stdout.txt')).write_text(result.stdout, encoding='utf-8')
    (D / ('readiness_' + name + '.stderr.txt')).write_text(result.stderr, encoding='utf-8')
    value = json.loads(result.stdout)
    (D / ('readiness_' + name + '.json')).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    assert result.returncode == expected and not value['network_attempts']
    if name == 'valid':
        assert value['status'] == 'ready_for_isolated_recovery' and value['ledger_paths_and_bytes_unchanged']
    else:
        assert all(v['status'] == 'unavailable' for v in value['products'].values())
    report.append({'case': name, 'expected_exit': expected, 'exit': result.returncode, 'network_attempts': 0, 'status': 'PASS'})
(D / 'preflight_checks.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
print(json.dumps(report))
