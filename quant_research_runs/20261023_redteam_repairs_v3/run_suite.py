"""Bound, isolated repair acceptance harness; every run gets a new directory."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('run')
    parser.add_argument('--cumulative', action='store_true')
    parser.add_argument('tests', nargs='*')
    opt = parser.parse_args()
    out = HERE / opt.run
    out.mkdir(exist_ok=False)
    temp = out / 'isolated_temp'
    temp.mkdir(mode=0o777)
    args = opt.tests
    if opt.cumulative:
        args = json.loads((ROOT / 'quant_research_runs/20261023_multiround_adversarial_v1/all_args.json').read_text('utf-8')) + [
            'test_icim_redteam_strategy_repairs_20261023.py',
            'test_icim_redteam_market_repairs_20261023.py',
            'test_icim_redteam_evidence_repairs_20261023.py',
            'test_icim_redteam_server_repairs_20261023.py',
        ]
    assert args
    (out / 'arguments.json').write_text(json.dumps(args, indent=2), encoding='utf-8')

    def bindings():
        paths = list(ROOT.glob('*.py')) + list((ROOT / 'docs').glob('*')) + list((ROOT / 'tests/fixtures').rglob('*'))
        paths += list((HERE / 'independent').rglob('*.py'))
        return {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in paths if p.is_file() and '__pycache__' not in p.parts}

    before = bindings()
    (out / 'source_before.json').write_text(json.dumps(before, indent=2), encoding='utf-8')
    env = {**os.environ, 'ICIM_TEST_OUTPUT_DIR': str(out), 'PYTHONIOENCODING': 'utf-8',
           'PYTHONDONTWRITEBYTECODE': '1', 'ICIM_STATE_DIR': str(temp / 'state'),
           'ICIM_REQUIRE_MIGRATION': '0', 'ICIM_DISABLE_EVIDENCE_CAPTURE': '1',
           'ICIM_DISABLE_INTERNAL_REFRESH': '1', 'TEMP': str(temp), 'TMP': str(temp)}
    start = time.monotonic()
    with (out / 'process_output.txt').open('w', encoding='utf-8') as f:
        result = subprocess.run([sys.executable, '-B', '-X', 'utf8', str(ROOT / '_icim_delivery_test_worker.py'), *args],
                                cwd=ROOT, env=env, stdout=f, stderr=subprocess.STDOUT, timeout=900)
    after = bindings()
    (out / 'source_after.json').write_text(json.dumps(after, indent=2), encoding='utf-8')
    sys.path.insert(0, str(ROOT))
    import icim_delivery_test_evidence as gate
    receipt = {'baseline_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
               'working_tree_patch': True, 'exit_code': result.returncode, 'elapsed_seconds': time.monotonic() - start,
               'source_before_equals_after': before == after, 'source_files': len(before)}
    try:
        receipt['counts'] = gate.validate_run(out / 'regression.xml', process_exit_code=result.returncode,
                                            source_before=before, source_after=after)
    except Exception as exc:
        receipt['acceptance_error'] = str(exc)
    receipt['artifacts'] = {n: hashlib.sha256((out / n).read_bytes()).hexdigest() for n in
                            ['collection.json', 'regression.xml', 'regression.txt', 'test_run.json', 'source_before.json', 'source_after.json']
                            if (out / n).exists()}
    (out / 'receipt.json').write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    if 'acceptance_error' not in receipt:
        gate.validate_receipt(out)
    print(json.dumps(receipt, indent=2))
    return result.returncode or int('acceptance_error' in receipt)


if __name__ == '__main__':
    raise SystemExit(main())
