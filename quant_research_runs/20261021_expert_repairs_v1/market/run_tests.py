"""Isolated Windows test runner with sanitized output and no production state."""
from pathlib import Path
import contextlib
import io
import json
import os
import re
import sys
import uuid

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent / os.environ.get("ICIM_AUDIT_STAGE", "cumulative")
HERE.mkdir(parents=True, exist_ok=True)
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

class EvidenceIsolation:
    def __init__(self):
        self.collected = []
        self.deselected = []
        self.selected = []
        self.executed = []

    @pytest.hookimpl(tryfirst=True)
    def pytest_collection_modifyitems(self, items):
        self.collected = [item.nodeid for item in items]
        # The old auditor hardcodes its evidence directory. Preserve its tests
        # and assertions, but isolate newly generated receipts in this run.
        for item in items:
            module = item.module
            if module.__name__ == 'test_icim_round2_audit_master_20261021':
                module.AUDIT = HERE / 'master_bindings'
            elif module.__name__ in {
                'test_icim_a3_ohlcv_20261021',
                'test_icim_batch3_calendar_redteam_20261021',
                'test_icim_batch3_old_call_20261021',
            }:
                module.AUDIT = HERE / 'legacy_bindings' / module.__name__
            elif module.__name__ == 'test_icim_batch3_research_boundary_20261021':
                module.HERE = HERE / 'legacy_bindings' / module.__name__

    def pytest_deselected(self, items):
        self.deselected.extend(item.nodeid for item in items)

    def pytest_collection_finish(self, session):
        self.selected = [item.nodeid for item in session.items]

    def pytest_runtest_logreport(self, report):
        if report.when == 'call':
            self.executed.append(report.nodeid)

def sanitize(s):
    for key, value in os.environ.items():
        if re.search(r'key|token|secret|password', key, re.I) and len(value) >= 8:
            s = s.replace(value, '[REDACTED]')
    return re.sub(r'sk-[A-Za-z0-9_-]{10,}', '[REDACTED]', s)

manifest = json.loads((ROOT/'docs/ic_im_v1_4_script_delivery_sync_20261013_source_manifest.json').read_text(encoding='utf-8'))
tests = sys.argv[1:] or manifest['required_local_tests']+['test_ic_im_put_execution_repairs_20261016.py', 'test_ic_im_option_maintenance_notice_20261016.py', 'test_ic_im_put_delivery_completion_20261016.py']
capture=io.StringIO()
hidden_addopts_present = bool(os.environ.pop('PYTEST_ADDOPTS', ''))
evidence_plugin = EvidenceIsolation()
with contextlib.redirect_stdout(capture), contextlib.redirect_stderr(capture):
    status=pytest.main(['-q','-p','no:cacheprovider','-c',str(Path(__file__).resolve().parent/'pytest.ini'),
                        '--rootdir',str(ROOT),'-o','addopts=','--basetemp',str(TASK_TEMP/'pytest'),
                        '--junitxml',str(HERE/'regression.xml')]+tests,
                       plugins=[evidence_plugin])
safe=sanitize(capture.getvalue())
(HERE/'regression.txt').write_text(safe,encoding='utf-8')
xml=HERE/'regression.xml'
if xml.exists():xml.write_text(sanitize(xml.read_text(encoding='utf-8')),encoding='utf-8')
(HERE/'test_run.json').write_text(json.dumps(dict(exit_code=int(status),files=tests,
     state_dir=str(TASK_TEMP/'state'),test_only_windows_mkdir_mode_adjustment=True,
     implicit_pytest_addopts_removed=hidden_addopts_present),indent=2),encoding='utf-8')
(HERE/'collection.json').write_bytes((json.dumps(dict(collected=evidence_plugin.collected,
     selected=evidence_plugin.selected,deselected=evidence_plugin.deselected,
     executed=evidence_plugin.executed),indent=2)+'\n').encode('utf-8'))
print(safe[-17000:])
sys.exit(int(status))
