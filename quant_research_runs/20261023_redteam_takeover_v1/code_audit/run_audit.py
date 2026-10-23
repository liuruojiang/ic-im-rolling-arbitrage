import hashlib
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent / 'final03'
OUT.mkdir(exist_ok=True)
TESTS = [
    'test_icim_redteam_strategy_repairs_20261023.py',
    'test_icim_redteam_server_repairs_20261023.py',
    'quant_research_runs/20261023_redteam_repairs_v3/audit_strategy_state/test_independent_strategy_state.py',
    'quant_research_runs/20261023_redteam_takeover_v1/code_audit/test_im_durable_mark.py',
]
def hashes():
    paths = list(ROOT.glob('*.py')) + [ROOT / p for p in TESTS]
    runtime = [p for p in (ROOT / 'runtime' / 'ic_im_v1_4_r1').rglob('*') if p.is_file()]
    assert runtime, 'formal runtime snapshot must be nonempty'
    paths += runtime
    return {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
def save(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
before = hashes()
save('source_before.json', before)
args = [sys.executable, '-m', 'pytest', *TESTS, '-q', '-p', 'no:cacheprovider',
        '--basetemp=' + str(OUT / 'final_temp'), '--junitxml=' + str(OUT / 'final.xml')]
env = os.environ.copy()
env['ICIM_TEST_OUTPUT_DIR'] = str(OUT / 'test_output')
Path(env['ICIM_TEST_OUTPUT_DIR']).mkdir(exist_ok=True)
run = subprocess.run(args, cwd=ROOT, capture_output=True, env=env)
(OUT / 'final.stdout.txt').write_bytes(run.stdout)
(OUT / 'final.stderr.txt').write_bytes(run.stderr)
after = hashes()
save('source_after.json', after)
root = ET.parse(OUT / 'final.xml').getroot()
suites = root.findall('testsuite') if root.tag == 'testsuites' else [root]
counts = {k: sum(int(s.get(k, 0)) for s in suites) for k in ['tests','failures','errors','skipped']}
receipt = {'exit_code':run.returncode, 'counts':counts, 'source_files':len(before),
           'source_before_equals_after':before == after, 'tests':TESTS,
           'artifacts':{name:hashlib.sha256((OUT / name).read_bytes()).hexdigest() for name in ['final.xml','final.stdout.txt','final.stderr.txt','source_before.json','source_after.json']}}
save('receipt.json', receipt)
print(json.dumps(receipt, ensure_ascii=False))
sys.exit(run.returncode or (0 if before == after else 1))
