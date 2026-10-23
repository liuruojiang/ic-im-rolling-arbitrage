"""Independent runner; never writes original external-review evidence."""
from pathlib import Path
import contextlib
import hashlib
import datetime
import io
import json
import os
import re
import sys
import uuid

ROOT = Path(__file__).resolve().parents[4]
HERE = Path(__file__).resolve().parent
assert HERE.name == 'final' and HERE.parent.name == 'display' and HERE.parent.parent.name == '20261021_external_review_redteam_v1'
assert (ROOT/'poe_ic_im_mainline_v1_4_bot.py').is_file()
sys.path.insert(0, str(ROOT))
TASK_TEMP = Path(os.environ['TEMP']) / ('icim_redteam_display_' + uuid.uuid4().hex[:10])
TASK_TEMP.mkdir(parents=True, exist_ok=False)
original_mkdir = os.mkdir
def test_mkdir(path, mode=0o777, *, dir_fd=None):
    return original_mkdir(path, 0o777 if mode == 0o700 else mode,
                          **({} if dir_fd is None else {'dir_fd': dir_fd}))
os.mkdir = test_mkdir
original_chmod = os.chmod
def test_chmod(path, mode, **kwargs):
    return original_chmod(path, 0o777 if mode == 0o700 else mode, **kwargs)
os.chmod = test_chmod
os.environ.update(TEMP=str(TASK_TEMP), TMP=str(TASK_TEMP), ICIM_STATE_DIR=str(TASK_TEMP/'state'),
                  ICIM_REQUIRE_MIGRATION='0', PYTHONDONTWRITEBYTECODE='1')
import tempfile
tempfile.tempdir = str(TASK_TEMP)
import pytest

def sanitize(value):
    for key, secret in os.environ.items():
        if re.search(r'key|token|secret|password', key, re.I) and len(secret) >= 8:
            value = value.replace(secret, '[REDACTED]')
    return re.sub(r'sk-[A-Za-z0-9_-]{10,}', '[REDACTED]', value)

tag = sys.argv[1] if len(sys.argv)>1 else 'final'
assert re.fullmatch(r'[a-z0-9_-]+', tag)
xml = HERE/(tag+'.xml')
assert not xml.exists(), 'Never overwrite first-failure evidence; choose a new run label'
tests = ['test_icim_redteam_display_20261021.py']
FILES = ['poe_ic_im_mainline_v1_4_bot.py','ic_im_fear_grid_v1.py','ic_im_v1_4_policy.py','ic_im_put_execution_v1.py','poe_ic_im_v1_4_state.py','run_ic_im_v1_4_github_digest.py','test_icim_redteam_display_20261021.py']
def source_hashes():
    return {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in FILES}
before_hashes = source_hashes()
(HERE/'before_hashes.json').write_text(json.dumps(dict(captured_at_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),sha256=before_hashes),indent=2),encoding='utf-8')
capture = io.StringIO()
with contextlib.redirect_stdout(capture), contextlib.redirect_stderr(capture):
    status = pytest.main(['-q','-p','no:cacheprovider','--basetemp',str(TASK_TEMP/'pytest'),
                         '--junitxml',str(xml),*tests])
after_hashes = source_hashes()
(HERE/'after_hashes.json').write_text(json.dumps(dict(captured_at_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),sha256=after_hashes,unchanged=before_hashes==after_hashes),indent=2),encoding='utf-8')
assert before_hashes == after_hashes, 'Source or tests changed during final independent run'
safe = sanitize(capture.getvalue())
(HERE/(tag+'.txt')).write_text(safe, encoding='utf-8')
if xml.exists(): xml.write_text(sanitize(xml.read_text(encoding='utf-8')),encoding='utf-8')
(HERE/(tag+'.json')).write_text(json.dumps(dict(exit_code=int(status), tests=tests,
    root=str(ROOT), evidence_dir=str(HERE), state_dir=str(TASK_TEMP/'state'),
    test_only_windows_mkdir_mode_adjustment=True), indent=2),encoding='utf-8')
print(safe.encode('ascii','backslashreplace').decode('ascii'))
raise SystemExit(int(status))
