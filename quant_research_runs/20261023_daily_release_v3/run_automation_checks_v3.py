"""Full automation regression with recorded Windows test-temp ACL compatibility."""
from pathlib import Path
import hashlib
import json
import os
import sys
import time
import xml.etree.ElementTree as ET

RUN = Path(__file__).resolve().parent
ROOT = RUN.parents[1]
AUTO = RUN / 'automation_checkout'
CONFIG = ROOT / 'quant_research_runs/20261023_multiround_adversarial_v1/pytest.ini'
target = RUN / sys.argv[1]
target.mkdir(exist_ok=False)
task_temp = target / 'isolated_temp'
task_temp.mkdir(mode=0o777)
temp_root = task_temp.resolve()

def hashes():
    result = {}
    for path in sorted(AUTO.rglob('*')):
        if path.is_file() and not any(x in {'.git', '__pycache__', '.pytest_cache', '.hypothesis'} for x in path.relative_to(AUTO).parts):
            result[path.relative_to(AUTO).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result

before = hashes()
(target / 'source_before.json').write_text(json.dumps(before, indent=2), encoding='utf-8')
original_mkdir, original_chmod = os.mkdir, os.chmod
adapted = {'mkdir': 0, 'chmod': 0}

def compatible(path, mode):
    try:
        inside = Path(path).resolve().is_relative_to(temp_root)
    except (TypeError, ValueError):
        inside = False
    return os.name == 'nt' and inside and mode == 0o700

def mkdir(path, mode=0o777, *, dir_fd=None):
    if compatible(path, mode):
        mode = 0o777
        adapted['mkdir'] += 1
    return original_mkdir(path, mode, **({} if dir_fd is None else {'dir_fd': dir_fd}))

def chmod(path, mode, **kwargs):
    if compatible(path, mode):
        mode = 0o777
        adapted['chmod'] += 1
    return original_chmod(path, mode, **kwargs)

os.mkdir, os.chmod = mkdir, chmod
os.environ.update(TEMP=str(temp_root), TMP=str(temp_root), PYTHONDONTWRITEBYTECODE='1',
                  PYTHONUTF8='1', PYTHONIOENCODING='utf-8',
                  HYPOTHESIS_STORAGE_DIRECTORY=str(temp_root / 'hypothesis'),
                  ICIM_STATE_DIR=str(temp_root / 'state'), ICIM_REQUIRE_MIGRATION='0',
                  ICIM_DISABLE_INTERNAL_REFRESH='1', ICIM_DISABLE_EVIDENCE_CAPTURE='1')
os.environ.pop('PYTEST_ADDOPTS', None)
import tempfile
tempfile.tempdir = str(temp_root)
sys.path[:0] = [str(AUTO), str(AUTO / 'scripts')]
os.chdir(AUTO)
import pytest

class Cases:
    def __init__(self):
        self.collected = []
        self.reports = []
    def pytest_collection_finish(self, session):
        self.collected = [item.nodeid for item in session.items]
    def pytest_runtest_logreport(self, report):
        self.reports.append({'nodeid': report.nodeid, 'phase': report.when, 'outcome': report.outcome})

plugin = Cases()
started = time.monotonic()
status = pytest.main(['-q', '-p', 'no:cacheprovider', '-c', str(CONFIG),
                     '--rootdir', str(AUTO), '--basetemp', str(temp_root / 'pytest'),
                     '--junitxml', str(target / 'regression.xml'), str(AUTO / 'tests')], plugins=[plugin])
elapsed = time.monotonic() - started
os.mkdir, os.chmod = original_mkdir, original_chmod
after = hashes()
(target / 'source_after.json').write_text(json.dumps(after, indent=2), encoding='utf-8')
(target / 'collection.json').write_text(json.dumps(plugin.collected, indent=2), encoding='utf-8')
(target / 'executed.json').write_text(json.dumps(plugin.reports, indent=2), encoding='utf-8')
tree = ET.parse(target / 'regression.xml').getroot()
counts = {key: sum(int(s.get(key, 0)) for s in list(tree)) for key in ['tests', 'failures', 'errors', 'skipped']}
receipt = {'exit_code': int(status), 'counts': counts, 'scope': 'all automation tests',
           'elapsed_seconds': elapsed, 'source_files': len(before), 'source_before_equals_after': before == after,
           'changed_files': sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k)),
           'collected_nodes': len(plugin.collected), 'windows_test_temp_mode_adaptations': adapted,
           'adaptation_scope': str(temp_root),
           'non_source_directory_exclusions': ['.git', '__pycache__', '.pytest_cache', '.hypothesis'],
           'runner_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
           'config_sha256': hashlib.sha256(CONFIG.read_bytes()).hexdigest()}
(target / 'receipt.json').write_text(json.dumps(receipt, indent=2), encoding='utf-8')
print(json.dumps(receipt))
raise SystemExit(int(status) or int(bool(counts['skipped']) or before != after))
