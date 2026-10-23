"""Run the entire automation repository's offline tests with isolated state."""
from pathlib import Path
import os
import sys
import json
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
RUN = Path(__file__).resolve().parent
AUTO = Path('D:/workspace/icim_release_20261023/automation')
target = RUN / sys.argv[1]
target.mkdir(exist_ok=False)
task_temp = target / 'isolated_temp'
task_temp.mkdir(mode=0o777)
original_mkdir, original_chmod = os.mkdir, os.chmod
def mkdir(path, mode=0o777, *, dir_fd=None):
    return original_mkdir(path, 0o777 if mode == 0o700 else mode, **({} if dir_fd is None else {'dir_fd': dir_fd}))
def chmod(path, mode, **kwargs):
    return original_chmod(path, 0o777 if mode == 0o700 else mode, **kwargs)
os.mkdir, os.chmod = mkdir, chmod
os.environ.update(TEMP=str(task_temp.resolve()), TMP=str(task_temp.resolve()), PYTHONDONTWRITEBYTECODE='1',
                  ICIM_STATE_DIR=str((task_temp/'state').resolve()), ICIM_REQUIRE_MIGRATION='0',
                  ICIM_DISABLE_INTERNAL_REFRESH='1', ICIM_DISABLE_EVIDENCE_CAPTURE='1')
os.environ.pop('PYTEST_ADDOPTS', None)
import tempfile
tempfile.tempdir = str(task_temp.resolve())
sys.path[:0] = [str(AUTO), str(AUTO/'scripts')]
import pytest
class Cases:
    def __init__(self): self.nodes = []
    def pytest_runtest_logreport(self, report):
        if report.when == 'call': self.nodes.append(report.nodeid)
plugin = Cases()
status = pytest.main(['-q', '-p', 'no:cacheprovider', '-c', str(ROOT/'quant_research_runs/20261023_multiround_adversarial_v1/pytest.ini'),
                     '--rootdir', str(AUTO), '--basetemp', str((task_temp/'pytest').resolve()),
                     '--junitxml', str((target/'regression.xml').resolve()), str(AUTO/'tests')], plugins=[plugin])
(target/'executed.json').write_text(json.dumps(plugin.nodes, indent=2), encoding='utf-8')
tree = ET.parse(target/'regression.xml').getroot()
counts = {key: sum(int(s.get(key, 0)) for s in list(tree)) for key in ['tests','failures','errors','skipped']}
(target/'receipt.json').write_text(json.dumps({'exit_code':int(status), 'counts':counts, 'scope':'all automation tests'},indent=2), encoding='utf-8')
raise SystemExit(int(status) or (1 if counts['skipped'] else 0))
