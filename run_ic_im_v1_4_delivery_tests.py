"""Run the declared daily regression in an isolated, bounded child process."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import signal
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
MANIFEST = ROOT / 'docs/ic_im_daily_release_20261023_manifest.json'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bindings(manifest):
    names = set(manifest['files']) | set(manifest.get('fixture_sha256', {})) | set(manifest['required_local_tests']) | set(manifest['required_test_helpers'])
    names |= {'run_ic_im_v1_4_delivery_tests.py', '_icim_delivery_test_worker.py',
              'icim_delivery_test_evidence.py', 'docs/ic_im_daily_release_20261023_manifest.json',
              'docs/icim_delivery_legacy_fixtures_20261023.json', 'docs/icim_delivery_pytest.ini',
              'docs/icim_historical_deselections_20261023.json'}
    return {name: sha(ROOT / name) for name in sorted(names)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out-dir', type=Path, required=True)
    args = parser.parse_args()
    target = args.out_dir.resolve()
    if target == ROOT or (ROOT / 'runtime').resolve() in [target, *target.parents]:
        raise RuntimeError('test output must be a separate disposable directory')
    target.mkdir(parents=True, exist_ok=False)
    manifest = json.loads(MANIFEST.read_text(encoding='utf-8'))
    assert manifest['schema'] == 'icim_audited_daily_release_v1'
    assert manifest['daily_gate_outer_timeout_seconds'] == 60
    before = bindings(manifest)
    assert all(before[name] == value for name, value in manifest['files'].items()), 'unverified production source'
    assert all(before[name] == value for name, value in manifest.get('fixture_sha256', {}).items()), 'unverified test fixture'
    registered = manifest['historical_exclusion_registry']
    # The exact registry bytes are shipped separately, avoiding JSON formatting
    # assumptions when validating its unchanged historical identity.
    assert sha(ROOT / 'docs/icim_historical_deselections_20261023.json') == manifest['historical_exclusion_registry_sha256']
    assert registered == json.loads((ROOT / 'docs/icim_historical_deselections_20261023.json').read_text(encoding='utf-8'))
    tests = manifest['required_local_tests']
    assert len(tests) == len(set(tests)) == 43
    allowed = [node for node in registered if node.split('::', 1)[0] in tests]
    assert manifest['daily_gate_deselections'] == allowed
    selected = list(tests)
    for node in allowed:
        selected += ['--deselect', node]
    (target / 'source_before.json').write_text(json.dumps(before, indent=2), encoding='utf-8')
    # Keep test state inside the requested audit output, away from production
    # and away from the system drive's continuously accumulating temp tree.
    disposable = target / 'isolated_temp'
    disposable.mkdir(mode=0o777)
    env = {**os.environ, 'ICIM_TEST_OUTPUT_DIR': str(target), 'PYTHONIOENCODING': 'utf-8',
           'PYTHONDONTWRITEBYTECODE': '1', 'ICIM_STATE_DIR': str(disposable / 'state'),
           'TEMP': str(disposable), 'TMP': str(disposable),
           'ICIM_REQUIRE_MIGRATION': '0', 'ICIM_DISABLE_EVIDENCE_CAPTURE': '1',
           'ICIM_DISABLE_INTERNAL_REFRESH': '1'}
    log = target / 'process_output.txt'
    with log.open('w', encoding='utf-8') as stream:
        child = subprocess.Popen([sys.executable, '-B', '-X', 'utf8', str(ROOT / '_icim_delivery_test_worker.py'), *selected],
            cwd=ROOT, env=env, stdout=stream, stderr=subprocess.STDOUT,
            start_new_session=os.name != 'nt',
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0)
        try:
            status = child.wait(timeout=60)
        except subprocess.TimeoutExpired:
            if os.name == 'nt':
                subprocess.run(['taskkill', '/PID', str(child.pid), '/T', '/F'], capture_output=True)
            else:
                os.killpg(child.pid, signal.SIGKILL)
            child.wait()
            (target / 'timeout.json').write_text(json.dumps({'timeout_seconds': 60, 'status': 'rejected'}), encoding='utf-8')
            raise RuntimeError('complete daily test suite exceeded the 60-second child limit')
    after = bindings(manifest)
    (target / 'source_after.json').write_text(json.dumps(after, indent=2), encoding='utf-8')
    import icim_delivery_test_evidence as gate
    counts = gate.validate_run(target / 'regression.xml', process_exit_code=status,
                               source_before=before, source_after=after)
    artifacts = ['regression.xml', 'regression.txt', 'source_before.json', 'source_after.json', 'test_run.json', 'collection.json']
    receipt = {'exit_code': status, 'source_before_equals_after': before == after,
               'source_files': len(before), 'counts': counts,
               'artifacts': {name: sha(target / name) for name in artifacts}}
    (target / 'receipt.json').write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    gate.validate_receipt(target)
    print(json.dumps({'status': 'PASS', 'counts': counts, 'deselected': len(allowed),
                      'release': manifest['release'], 'bound_files': len(before)}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
