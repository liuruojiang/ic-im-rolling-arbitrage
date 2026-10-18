"""Independent read-only rendering challenges against archived production records."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import date, datetime
import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
import ic_im_fear_grid_v1 as feed
import poe_ic_im_mainline_v1_4_bot as bot
import run_ic_im_v1_4_github_digest as runner

CLOUD = ROOT / 'quant_research_runs/20261016_fear_single_source_delivery_v1/cloud'
sys.path.insert(0, str(CLOUD / 'scripts'))
import build_ic_im_v1_4_digest as mail

OUT = Path(__file__).resolve().parent
CLOCK = datetime(2026, 10, 16, 13, 15, tzinfo=feed.BEIJING)
REFERENCE = dict(jiu_fear_greed_index=2.28, jiu_data_date='2026-10-15',
                 jiu_status='reference_only', jiu_reference_only=True,
                 jiu_retrieved_at=CLOCK.isoformat(), jiu_page_url=feed.JIU_PAGE_URL)
OBSERVATION = dict(fear_greed_index=48.33, fear_data_date='2026-10-16',
                   fear_data_status='intraday_provisional', fear_source_policy_revision=feed.SOURCE_POLICY_REVISION,
                   fear_selected_source='csv', fear_cross_validation='single_source',
                   fear_source_warning='仅取得CSV单侧数据，未完成双侧验证。')


def test_failure_runner_still_collects_jiu_and_cloud_displays_it(monkeypatch):
    calls = []
    monkeypatch.setattr(feed, 'fetch_score', lambda *a: deepcopy(OBSERVATION))
    monkeypatch.setattr(feed, 'fetch_jiu_reference', lambda *a: calls.append(a) or deepcopy(REFERENCE))
    target = OUT / 'failure_runner'
    runner.write_failure(target, CLOCK, RuntimeError('injected missing market quote'),
                         mode='realtime', expected_market_date='2026-10-16')
    result = json.loads((target / 'result.json').read_text(encoding='utf8'))
    subject, plain = mail.build_failure(result, '', '')
    html = mail.build_failure_html(result, '')
    (target / 'mail.txt').write_text(subject + '\n' + plain, encoding='utf8')
    (target / 'mail.html').write_text(html, encoding='utf8')
    assert calls, 'failure runner never attempted reference collection despite available mocked 9圈'
    assert '9圈' in plain and '9圈' in html
    assert '未完成双侧验证' in plain and '未完成双侧验证' in html


def test_poe_market_failure_still_shows_reference_and_source_warning(monkeypatch):
    chunks = []
    calls = []
    class Capture:
        @contextmanager
        def start_message(self):
            class Message:
                def write(self, value): chunks.append(str(value))
            yield Message()
    def broken(*args): raise RuntimeError('injected missing market quote')
    monkeypatch.setattr(bot, 'poe', Capture())
    monkeypatch.setattr(bot, '_now_beijing', lambda: CLOCK)
    monkeypatch.setattr(bot, 'build_live_signal_with_transport_retry', broken)
    monkeypatch.setattr(bot, '_write_last_verified_snapshot', lambda *a: None)
    monkeypatch.setattr(feed, 'fetch_score', lambda *a: deepcopy(OBSERVATION))
    monkeypatch.setattr(feed, 'fetch_jiu_reference', lambda *a: calls.append(a) or deepcopy(REFERENCE))
    bot.ICIMMainlinesBot()._handle_signal(('IC', 'IM'), 'intraday')
    text = ''.join(chunks)
    (OUT / 'poe_failure.md').write_text(text, encoding='utf8')
    assert calls, 'Poe failure path never attempted available 9圈 reference'
    assert '9圈' in text and '未完成双侧验证' in text


def test_success_mail_reference_merge_is_display_only(monkeypatch):
    fixture = CLOUD / 'tests/fixtures/icim/readiness_20261009/result.json'
    payload = json.loads(fixture.read_text(encoding='utf8'))
    original_signals = deepcopy(payload['signals'])
    payload['jiu_reference'] = deepcopy(REFERENCE)
    original = deepcopy(payload)
    subject, plain, _ = mail.build_success(payload, '', '')
    html = mail.build_success_html(payload, '')
    (OUT / 'success_reference.txt').write_text(subject + '\n' + plain, encoding='utf8')
    (OUT / 'success_reference.html').write_text(html, encoding='utf8')
    assert '9圈恐慌贪婪指数：2.28' in plain and '9圈恐慌贪婪指数：2.28' in html
    assert '仅供参考，不参与网格信号' in plain and '仅供参考，不参与网格信号' in html
    assert '2026-10-15' in plain and feed.JIU_PAGE_URL in plain
    assert payload == original and payload['signals'] == original_signals


def test_stored_report_missing_fear_masks_actual_archived_targets(monkeypatch):
    payload = json.loads((ROOT / 'runtime/ic_im_v1_4_r1/latest.json').read_text(encoding='utf8'))
    original = deepcopy(payload)
    for signal in payload['signals'].values():
        signal.update(fear_source_policy_revision=feed.SOURCE_POLICY_REVISION,
                      fear_greed_index=None, fear_data_date=None, fear_data_status='unavailable',
                      fear_source_warning='百分位两侧未取得，网格未确认', **REFERENCE)
    after_input = deepcopy(payload)
    text = runner.render_stored_close_report(payload)
    (OUT / 'stored_partial.md').write_text(text, encoding='utf8')
    assert text.count('N/A（未确认）') >= 2
    assert text.count('`UNCONFIRMED`') >= 2
    assert '9圈恐慌贪婪指数：2.28' in text
    assert payload == after_input


def test_old_cloud_mail_bytes_do_not_change():
    fixture = CLOUD / 'tests/fixtures/icim/readiness_20261009/result.json'
    payload = json.loads(fixture.read_text(encoding='utf8'))
    backup = ROOT / 'quant_research_runs/20261016_fear_single_source_delivery_v1/backups/cloud/scripts/build_ic_im_v1_4_digest.py'
    spec = importlib.util.spec_from_file_location('old_mail_renderer_audit', backup)
    old = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(old)
    assert old.build_success(payload, '', '') == mail.build_success(payload, '', '')
    assert old.build_success_html(payload, '') == mail.build_success_html(payload, '')
