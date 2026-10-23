"""Unique-stage, source-bound independent probes; never import a live server here."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import os
import shutil
import subprocess
import sys

from evidence_gate import validate_run, validate_receipt

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
stage, *tests = sys.argv[1:]
directory = HERE / stage
if not tests or directory.resolve().is_relative_to(HERE.resolve()) is False:
    raise ValueError("require a local unique stage and test arguments")
directory.mkdir(parents=True, exist_ok=False)

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def sources():
    own = {(ROOT / arg.split('::')[0]).resolve() for arg in tests
           if not arg.startswith('-') and (arg.endswith('.py') or '.py::' in arg)}
    if any(not p.is_relative_to(ROOT.resolve()) or not p.is_file() for p in own):
        raise ValueError("test sources must exist inside the repository")
    paths = [p for p in ROOT.glob('*.py')
             if not p.name.startswith('test_icim_b3adv_') or p.resolve() in own]
    paths += list(own)
    paths += list(HERE.glob('*.py'))
    paths += list((ROOT / 'docs').glob('*_spec.md'))
    paths += list((ROOT / 'docs').glob('*_spec.md.sha256'))
    for folder in ['icim_adversarial', 'icim_external_review_20261021', 'icim_external_review_round2_20261021']:
        paths += [p for p in (ROOT / 'tests/fixtures' / folder).rglob('*')
                  if p.is_file() and '__pycache__' not in p.parts]
    return {p.relative_to(ROOT).as_posix(): sha(p) for p in sorted(set(paths))}

before = sources()
(directory / 'source_before.json').write_bytes((json.dumps(before, indent=2)+'\n').encode())
for arg in tests:
    if not arg.startswith('-') and (arg.endswith('.py') or '.py::' in arg):
        source = (ROOT / arg.split('::')[0]).resolve()
        snapshot = directory/'test_sources'/source.relative_to(ROOT.resolve())
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, snapshot)
env = dict(os.environ, ICIM_AUDIT_STAGE=stage, PYTHONIOENCODING='utf-8')
started = datetime.now(timezone.utc).isoformat()
process = subprocess.run([sys.executable, str(HERE/'run_checks.py'), *tests],
                         cwd=ROOT, env=env, timeout=180)
after = sources()
(directory / 'source_after.json').write_bytes((json.dumps(after, indent=2)+'\n').encode())
try:
    counts = validate_run(directory/'regression.xml', process_exit_code=process.returncode,
                          source_before=before, source_after=after)
except RuntimeError as exc:
    (directory/'rejected.json').write_bytes((json.dumps(dict(reason=str(exc),
        exit_code=process.returncode, source_equal=before==after), indent=2)+'\n').encode())
    raise
receipt = dict(started_at=started, finished_at=datetime.now(timezone.utc).isoformat(),
               exit_code=process.returncode, source_before_equals_after=before==after,
               source_files=len(before), counts=counts,
               artifacts={name:sha(directory/name) for name in ['regression.xml',
                   'regression.txt','source_before.json','source_after.json','test_run.json','collection.json']})
(directory/'receipt.json').write_bytes((json.dumps(receipt,indent=2)+'\n').encode())
try:
    validate_receipt(directory)
except RuntimeError as exc:
    (directory/'receipt.json').rename(directory/'rejected_receipt.json')
    (directory/'rejected.json').write_bytes((json.dumps(dict(reason=str(exc),
        exit_code=process.returncode),indent=2)+'\n').encode())
    raise
print(json.dumps(dict(stage=stage,counts=counts,source_files=len(before))))
