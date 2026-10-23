"""Isolated Windows test runner with sanitized output and no production state."""
from pathlib import Path
import contextlib
import io
import json
import os
import re
import sys
import uuid
from datetime import datetime

ROOT = Path(__file__).resolve().parent
HERE = Path(os.environ["ICIM_TEST_OUTPUT_DIR"])
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
                  ICIM_REQUIRE_MIGRATION='0', PYTHONDONTWRITEBYTECODE='1',
                  ICIM_DISABLE_EVIDENCE_CAPTURE='1')
import tempfile
tempfile.tempdir = str(TASK_TEMP)
if os.environ.get('ICIM_AUDIT_USE_BACKUPS') == '1':
    sys.path.insert(0, str(Path(__file__).resolve().parent/'backup'))
    import poe_ic_im_mainline_v1_4_bot, poe_ic_im_v1_4_state, ic_im_signal_evidence_v1, ic_im_evidence_adapter_v1, poe_ic_im_v1_4_server
    loaded = {m.__name__: str(Path(m.__file__).resolve()) for m in [poe_ic_im_mainline_v1_4_bot, poe_ic_im_v1_4_state, ic_im_signal_evidence_v1, ic_im_evidence_adapter_v1, poe_ic_im_v1_4_server]}
    (HERE/"actual_backup_modules.json").write_text(json.dumps(loaded,indent=2),encoding="utf-8")
import pytest

class EvidenceIsolation:
    def __init__(self):
        self.collected = []
        self.deselected = []
        self.selected = []
        self.executed = []

    def pytest_runtest_logstart(self, nodeid, location):
        with (HERE/'progress.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({'nodeid':nodeid, 'started_at':datetime.now().astimezone().isoformat()})+'\n')

    @pytest.fixture(autouse=True)
    def legacy_wire_inputs(self, request, monkeypatch):
        adaptations = json.loads((ROOT/'docs/icim_delivery_legacy_fixtures_20261023.json').read_text(encoding='utf-8'))
        node = request.node.nodeid
        if node in adaptations.get('sina_wire_string_mock_boundary', []):
            # Only this old requests.get fake assumes params is always a dict.
            # Translate the real CSV query string back to that fake's input;
            # the production HTTP wrapper and evidence recorder still run.
            from urllib.parse import parse_qs
            import poe_ic_im_mainline_v1_4_bot as bot
            original_setattr = monkeypatch.setattr
            def set_compatible(target, name, value, *args, **kwargs):
                if target is bot.requests and name == 'get':
                    fake_get = value
                    def wire_fake(url, params=None, **kw):
                        if isinstance(params, str):
                            parsed = parse_qs(params, strict_parsing=True)
                            params = {key: values[0] for key, values in parsed.items()}
                        return fake_get(url, params=params, **kw)
                    value = wire_fake
                return original_setattr(target, name, value, *args, **kwargs)
            monkeypatch.setattr(monkeypatch, 'setattr', set_compatible)
        if node not in adaptations['single_response_chain_batch_size_64'] and node not in adaptations['synthetic_etf_previous_close_7_8']:
            return
        import poe_ic_im_mainline_v1_4_bot as bot
        if node in adaptations['single_response_chain_batch_size_64']:
            monkeypatch.setattr(bot, '_SINA_OPTION_QUOTE_BATCH_SIZE', 64)
        if node == 'test_ic_im_v1_4_integration_guards.py::test_sina_chain_marks_one_yesterday_row_without_reselecting_another_strike':
            # This older three-row synthetic fixture exposes content-only
            # Mock objects and a bare ID list. Give its same values the actual
            # vendor envelope; do not weaken the production parser/streaming.
            def content_fixture(url, label, *, params=None):
                response = bot._evidence_http_get(url, params=params)
                response.raise_for_status()
                raw = bot._response_with_size_limit(response, label)
                if params and re.fullmatch(rb'CON_OP_[0-9]{8}(?:,CON_OP_[0-9]{8})*', raw):
                    raw = ('var hq_str_' + params['list'] + '="').encode() + raw + b'";'
                response.close()
                return raw
            monkeypatch.setattr(bot, '_public_quote_bytes', content_fixture)
        if node in adaptations['synthetic_etf_previous_close_7_8']:
            aggregate = bot.fetch_510500_quote_with_failover
            def complete_synthetic_etf(*args, **kwargs):
                primary = bot.fetch_sse_510500_quote
                def fixture():
                    value = primary()
                    if set(value) == {'date','time','last'} and value['last'] == 7.8:
                        return {**value, 'prev_close': 7.8}
                    return value
                with monkeypatch.context() as patch:
                    patch.setattr(bot, 'fetch_sse_510500_quote', fixture)
                    return aggregate(*args, **kwargs)
            monkeypatch.setattr(bot, 'fetch_510500_quote_with_failover', complete_synthetic_etf)

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
            elif module.__name__ == 'test_icim_b3adv_concurrency_20261021':
                module.AUDIT = HERE / 'legacy_bindings' / module.__name__
            elif module.__name__ == 'test_icim_b3adv_scope_20261021':
                def observation(name, value, module_name=module.__name__):
                    target = HERE / 'legacy_bindings' / module_name / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with target.open('x', encoding='utf-8') as handle:
                        json.dump(value, handle, ensure_ascii=False, indent=2)
                module.observation = observation
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
    status=pytest.main(['-q','-p','no:cacheprovider','-c',str(ROOT/'docs/icim_delivery_pytest.ini'),
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
