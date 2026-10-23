"""Execute untouched historical verifiers in disposable copies, never the real run directory."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
OLD = ROOT / 'quant_research_runs/20261021_external_review_round2_v1'

def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value if isinstance(value, str) else json.dumps(value), encoding='utf-8')

def main():
    sandbox = HERE / 'historical_counterexample_copies'
    sandbox.mkdir(exist_ok=False)
    copied = sandbox / 'quant_research_runs/20261021_external_review_round2_v1'
    copied.mkdir(parents=True)
    for name in ('final_acceptance.py', 'verify_final.py'):
        shutil.copyfile(OLD / name, copied / name)
    write(copied / 'run_all.py', "from pathlib import Path\np=Path(__file__).resolve().parent/'final'\n(p/'regression.xml').write_text('<testsuites><testsuite tests=\"2\" failures=\"1\" errors=\"0\" skipped=\"1\"><testcase name=\"failed\"><failure/></testcase><testcase name=\"skip\"><skipped/></testcase></testsuite></testsuites>')\n")
    acceptance = subprocess.run([sys.executable, str(copied / 'final_acceptance.py')], cwd=sandbox, text=True, capture_output=True)
    first_receipt = json.loads((copied / 'final/receipt.json').read_text())
    write(copied / 'baseline.json', {'backed_up': {}, 'prior_evidence': {}})
    write(sandbox / 'quant_research_runs/20261021_external_review_v1/baseline.json', {'immutable': {}})
    previous = sandbox / 'quant_research_runs/20261021_external_review_redteam_v1'
    write(previous / 'outputs_baseline.json', {'files': {}, 'scope': 'isolated synthetic fixture'})
    write(previous / 'final/source_after.json', {})
    write(copied / 'final/source_after.json', {})
    bad_receipt = dict(exit_code=0, source_before_equals_after=False, counts=first_receipt['counts'], artifacts={})
    write(copied / 'final/receipt.json', bad_receipt)
    write(sandbox / 'tests/fixtures/icim_external_review_round2_20261021/manifest.json', {})
    spec = sandbox / 'docs/ic_im_external_review_round2_20261021_v1_spec.md'
    write(spec, 'isolated verifier fixture\n')
    write(spec.with_suffix('.md.sha256'), hashlib.sha256(spec.read_bytes()).hexdigest())
    for name in ('poe_ic_im_mainline_v1_4_bot.py', 'ic_im_sse_contract_identity_v1.py', 'test_ic_im_external_review_round2_20261021.py', 'test_ic_im_external_review_display_cache_20261021.py'):
        write(sandbox / name, '# isolated empty compile target\n')
    (sandbox / 'outputs').mkdir()
    # Only git whitespace status is stubbed: this disposable tree is intentionally not a Git repository.
    wrapper = "import subprocess,runpy,types; subprocess.run=lambda *a,**k:types.SimpleNamespace(returncode=0,stdout='',stderr=''); runpy.run_path(" + repr(str(copied / 'verify_final.py')) + ",run_name='__main__')"
    verification = subprocess.run([sys.executable, '-c', wrapper], cwd=sandbox, text=True, capture_output=True)
    verified = json.loads((copied / 'final_verification.json').read_text())
    result = dict(scope='Synthetic isolated copies of immutable historical verifiers; not a claim actual old 1192 cases failed',
                  original_hashes={name:hashlib.sha256((OLD/name).read_bytes()).hexdigest() for name in ('final_acceptance.py','verify_final.py')},
                  acceptance=dict(exit_code=acceptance.returncode, receipt=first_receipt),
                  verification=dict(exit_code=verification.returncode, passed=verified['passed'],
                                    source_before_equals_after=verified['source_before_equals_after'], counts=verified['cumulative']),
                  fixture_notes=['Subprocess exits zero but actual XML records one failure and one skip',
                                 'verify_final receipt explicitly reports source_before_equals_after=false',
                                 'Only git diff --check is stubbed to zero for the non-Git disposable tree'])
    write(HERE / 'historical_false_pass.json', result)
    print(json.dumps(result, indent=2))

if __name__ == '__main__':
    main()
