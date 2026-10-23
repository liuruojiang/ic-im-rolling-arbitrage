"""Additional manual boundary receipts, separate from the frozen 28 tests."""
from datetime import date, datetime, timezone, timedelta
import ast
import difflib
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[4]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import requests
import ic_im_signal_evidence_v1 as evidence

target = HERE / 'manual_metadata_checks'
target.mkdir(exist_ok=False)
day = date(2026, 10, 23)
clock = datetime(2026, 10, 23, 9, tzinfo=timezone(timedelta(hours=8)))


def packet(status):
    value = requests.Response()
    value.status_code, value.encoding = status, 'utf-8'
    value._content, value._content_consumed = b'offline metadata test', True
    return value


signal = {'persistence_eligible': True}
with evidence.capture_session(target / 'bad_capture', day, clock, {'scope': 'metadata'}) as session:
    session.call('probe', {}, lambda: True)
    session.bind_signal(signal)
    session.raw_response('https://example.invalid/test', {}, packet(None))
assert session.context['complete'] is False
assert signal['persistence_eligible'] is False and signal['input_evidence_status'] == 'incomplete'
assert any(f['role'] == 'raw_http_metadata' and f['resolved'] is False for f in session.failures)

with evidence.capture_session(target / 'legacy', day, clock, {'scope': 'metadata'}) as legacy:
    legacy.call('probe', {}, lambda: True)
    legacy.raw_response('https://example.invalid/test', {}, packet(200))
envelope = json.loads(legacy.manifest_path.read_text(encoding='utf-8'))
envelope['body']['raw'][0]['status_code'] = None
envelope['sha256'] = hashlib.sha256(evidence._dumps(envelope['body'])).hexdigest()
legacy.manifest_path.write_bytes(evidence._dumps(envelope))
rejected = []
for kind, fn in [('load', lambda: evidence.load_session(target/'legacy', day)),
                 ('has', lambda: evidence.has_session(target/'legacy', day, {'scope':'metadata'}))]:
    try:
        fn()
    except evidence.EvidenceUnavailable:
        rejected.append(kind)
assert rejected == ['load', 'has']

old = HERE / 'ic_im_signal_evidence_v1.before_metadata.py'
new = ROOT / 'ic_im_signal_evidence_v1.py'
def nodes(path):
    tree = ast.parse(path.read_text(encoding='utf-8-sig'))
    result = {n.name: ast.dump(n, include_attributes=False) for n in tree.body if isinstance(n, ast.FunctionDef)}
    for cls in tree.body:
        if isinstance(cls, ast.ClassDef):
            result.update({cls.name + '.' + n.name: ast.dump(n, include_attributes=False)
                           for n in cls.body if isinstance(n, ast.FunctionDef)})
    return result
a, b = nodes(old), nodes(new)
result = {'status':'PASS', 'new_functions':sorted(set(b)-set(a)),
          'changed_functions':sorted(n for n in a if a[n] != b[n]),
          'malformed_capture_nonthrowing_and_ineligible':True,
          'legacy_complete_but_bad_metadata_rejected':rejected,
          'before_sha256':hashlib.sha256(old.read_bytes()).hexdigest(),
          'after_sha256':hashlib.sha256(new.read_bytes()).hexdigest()}
(HERE/'metadata_repair_verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
patch = ''.join(difflib.unified_diff(old.read_text(encoding='utf-8-sig').splitlines(keepends=True),
                                  new.read_text(encoding='utf-8-sig').splitlines(keepends=True),
                                  fromfile='before/ic_im_signal_evidence_v1.py', tofile='after/ic_im_signal_evidence_v1.py'))
(HERE/'metadata_repair.patch').write_text(patch,encoding='utf-8')
print(json.dumps(result,ensure_ascii=False))
