"""Isolated Windows test runner with sanitized output and no production state."""
from pathlib import Path
import contextlib
import io
import json
import os
import re
import sys
import uuid

ROOT = Path(__file__).resolve().parents[4]
HERE = Path(__file__).resolve().parent
if any('/cloud/tests/' in arg.replace('\\', '/') for arg in sys.argv[1:]):
    HERE = HERE / 'cloud'
sys.path.insert(0, str(ROOT))
# CPython 3.14 mkdir(0700) on this restricted Windows token produces a directory
# the test process cannot reopen. Only the disposable test tree is affected.
TASK_TEMP = Path(os.environ['TEMP']) / ('icim_put_' + uuid.uuid4().hex[:8])
TASK_TEMP.mkdir(parents=True, exist_ok=False)
original_mkdir = os.mkdir
def test_mkdir(path, mode=0o777, *, dir_fd=None):
    if mode == 0o700:
        mode = 0o777
    return original_mkdir(path, mode, **({} if dir_fd is None else {'dir_fd': dir_fd}))
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

def sanitize(s):
    for key, value in os.environ.items():
        if re.search(r'key|token|secret|password', key, re.I) and len(value) >= 8:
            s = s.replace(value, '[REDACTED]')
    return re.sub(r'sk-[A-Za-z0-9_-]{10,}', '[REDACTED]', s)

manifest = json.loads((ROOT/'docs/ic_im_v1_4_script_delivery_sync_20261013_source_manifest.json').read_text(encoding='utf-8'))
tests = sys.argv[1:] or manifest['required_local_tests']+['test_ic_im_put_execution_repairs_20261016.py', 'test_ic_im_option_maintenance_notice_20261016.py', 'test_ic_im_put_delivery_completion_20261016.py']
import hashlib
from datetime import datetime, timezone
AUDITED = ["ic_im_put_execution_v1.py", "ic_im_v1_4_policy.py",
           "poe_ic_im_v1_4_state.py", "poe_ic_im_mainline_v1_4_bot.py",
           "test_icim_redteam_execution_20261021.py",
           "test_ic_im_external_review_20261021.py",
           "test_ic_im_ordinary_put_route_fix_20261013.py",
           "test_ic_im_put_execution_repairs_20261016.py"]
def source_hashes():
    return {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in AUDITED}
before_hashes = source_hashes()
before_at = datetime.now(timezone.utc).isoformat()
(HERE/"source_hashes_before.json").write_text(json.dumps(dict(captured_at_utc=before_at, files=before_hashes), indent=2), encoding="utf-8")

capture=io.StringIO()
with contextlib.redirect_stdout(capture), contextlib.redirect_stderr(capture):
    status=pytest.main(['-q','-p','no:cacheprovider','--basetemp',str(TASK_TEMP/'pytest'),
                        '--junitxml',str(HERE/'regression.xml')]+tests)
safe=sanitize(capture.getvalue())
(HERE/'regression.txt').write_text(safe,encoding='utf-8')
xml=HERE/'regression.xml'
if xml.exists():xml.write_text(sanitize(xml.read_text(encoding='utf-8')),encoding='utf-8')
(HERE/'test_run.json').write_text(json.dumps(dict(exit_code=int(status),files=tests,
     state_dir=str(TASK_TEMP/'state'),test_only_windows_mkdir_mode_adjustment=True),indent=2),encoding='utf-8')
after_hashes = source_hashes()
stable = before_hashes == after_hashes
(HERE/"source_hashes_after.json").write_text(json.dumps(dict(captured_at_utc=datetime.now(timezone.utc).isoformat(), files=after_hashes, unchanged_during_run=stable), indent=2), encoding="utf-8")
(HERE/"verification.json").write_text(json.dumps(dict(exit_code=int(status), unchanged_during_run=stable, before=before_hashes, after=after_hashes), indent=2), encoding="utf-8")
if not stable:
    print("SOURCE CHANGED DURING RUN")
    status = 99

print(safe[-17000:])
sys.exit(int(status))

