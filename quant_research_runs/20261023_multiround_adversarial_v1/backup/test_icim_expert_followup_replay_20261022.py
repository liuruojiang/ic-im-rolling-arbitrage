"""Expert follow-up through real capture/builder/store, synthetic market inputs.

The HTTP bodies, future clocks and temporary 2027 calendar below are fixtures.
These tests do not claim that a real original-session archive was obtained.
"""
from copy import deepcopy
from datetime import date, timedelta
import json

import pytest
import requests

import ic_im_evidence_adapter_v1 as adapter
import ic_im_signal_evidence_v1 as evidence
import poe_ic_im_mainline_v1_4_bot as bot
import poe_ic_im_v1_4_state as state
import test_icim_round3_recovery_audit_20261021 as seed
import test_icim_round4_recovery_20261021 as r4
import test_icim_round5_recording_semantics_20261021 as r5


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(bot, 'LIVE_CONTINUATION_ANCHOR', deepcopy(bot.LIVE_CONTINUATION_ANCHOR))
    monkeypatch.setenv('ICIM_STATE_DIR', str(tmp_path / 'import_only'))
    monkeypatch.delenv('ICIM_EVIDENCE_DIR', raising=False)
    monkeypatch.setattr(bot.requests, 'get', lambda *a, **k: pytest.fail('network escaped synthetic fixture'))
    monkeypatch.setattr(bot, 'build_iv_warning', lambda *a, **k: {'status': 'synthetic_monitor_omitted'})


def raw_chain_provider(monkeypatch, day, samples, real_chain):
    calls = []
    def get(url, params=None, **kwargs):
        assert evidence.current_session.get() is not None
        month = str(url).rsplit('_', 1)[-1]
        matched = [value for ym, value in samples['IC'][4].items() if ym.endswith(month)]
        assert len(matched) == 1, url
        frame, _ = matched[0]
        columns = ['contract', 'last', 'chg_rate', 'pre_settle', 'strike', 'security_id']
        rows = [[0 if value is None else value for value in row] for row in frame[columns].values.tolist()]
        body = {'date': day.strftime('%Y%m%d'), 'time': 150000, 'total': len(rows),
                'begin': 0, 'end': len(rows), 'list': rows}
        response = requests.Response()
        response.status_code, response.encoding = 200, 'utf-8'
        response._content = json.dumps(body).encode('utf-8')
        calls.append((str(url), deepcopy(params)))
        return response
    monkeypatch.setattr(bot.requests, 'get', get)
    # Keep the actual installed chain adapter and actual SSE parser in this test.
    monkeypatch.setattr(bot, '_fetch_510500_chain_for_day', real_chain)
    return calls


@pytest.mark.parametrize('day,previous,month', [
    (date(2026, 10, 21), date(2026, 10, 20), '2612'),
    (date(2026, 11, 23), date(2026, 11, 20), '2703'),
])
def test_public_ic_dedicated_core_replay_has_zero_misses_and_no_new_url(
        tmp_path, monkeypatch, day, previous, month):
    clock = r4.wall(monkeypatch, day)
    if month == '2612':
        store, original = seed.temporary_store(tmp_path / 'state', previous)
        bot.install_runtime_anchors(state.anchors_from_record(original))
    else:
        store, original = r5.held_record(tmp_path / 'state', previous, month)
    actual_chain = bot._fetch_510500_chain_for_day
    samples = r4.providers(monkeypatch, day)
    calls = raw_chain_provider(monkeypatch, day, samples, actual_chain)
    root = tmp_path / 'evidence'
    assert adapter.capture_daily(bot, root, clock)['status'] == 'captured'
    reader, _ = adapter.load_original(bot, root, 'IC', day)
    original_urls = {row['url'] for row in reader.context['raw']}
    assert len(original_urls) == 4  # Nonempty actual HTTP-boundary recording.
    assert original_urls == {url for url, _ in calls}
    if month == '2703':
        r4.oracle(monkeypatch)  # Synthetic only; do not modify production calendar.
    later = r4.wall(monkeypatch, day + timedelta(days=1))
    monkeypatch.setattr(bot.requests, 'get', lambda *a, **k: pytest.fail('replay attempted real HTTP'))
    requested, misses, roles, held = [], [], [], []
    http = evidence.EvidenceReader.http_response
    typed = evidence.EvidenceReader.call
    held_quote = bot.fetch_sse_existing_put_historical_quote
    def trace_http(self, url, params):
        requested.append(url)
        try:
            return http(self, url, params)
        except evidence.EvidenceUnavailable as exc:
            misses.append(str(exc))
            raise
    def trace_typed(self, role, key):
        roles.append(role)
        try:
            return typed(self, role, key)
        except evidence.EvidenceUnavailable as exc:
            misses.append(str(exc))
            raise
    def trace_held(contract, security_id, held_day):
        assert bot._HISTORICAL_REPLAY_DAY.get() == day
        held.append((contract, security_id, held_day))
        return held_quote(contract, security_id, held_day)
    monkeypatch.setattr(evidence.EvidenceReader, 'http_response', trace_http)
    monkeypatch.setattr(evidence.EvidenceReader, 'call', trace_typed)
    monkeypatch.setattr(bot, 'fetch_sse_existing_put_historical_quote', trace_held)
    with adapter.evidence_location(root), bot.historical_replay(day):
        result = bot.build_live_trade_signal('IC', seed.clock(day), 'close')
        assert bot._HISTORICAL_REPLAY_DAY.get() == day
    assert result.get('signal_complete') and result.get('persistence_eligible'), result.get('diagnostic_reason')
    assert result['v14_core_put_contract'] == '510500P' + month + 'M07500'
    assert held and all(item[0] == result['v14_core_put_contract'] for item in held)
    assert {'ic.chain', 'cffex.quotes', 'ic.etf', 'market.live', 'fear.score'} <= set(roles)
    assert 'rate.resolve' in {entry['role'] for entry in reader.context['entries'].values()}
    assert result['original_evidence']['unrecorded_inputs'] == []
    assert misses == []  # Includes caught attempts, not just the final result flag.
    assert set(requested) <= original_urls
    # Installed typed adapters serve inputs upstream, so public replay needs no
    # lower HTTP requests. Explicitly record this instead of faking a network hit.
    assert requested == []
    assert store.load_latest()['digest'] == original['digest']
    assert result['signal_generation_timing'] == 'retrospective_replay'
    assert result['signal_generated_at'][:10] == str(later.date())


def test_collector_annotation_roundtrip_is_exactly_one_encoding(tmp_path, monkeypatch):
    day = date(2026, 10, 21)
    clock = r4.wall(monkeypatch, day)
    r4.providers(monkeypatch, day)
    values = {}
    original = evidence.CaptureSession.annotate
    def annotate(self, name, value):
        assert evidence.current_session.get() is self
        assert isinstance(value, dict) and '__type__' not in value
        values[evidence.decode(self.metadata)['product']] = deepcopy(value)
        return original(self, name, value)
    monkeypatch.setattr(evidence.CaptureSession, 'annotate', annotate)
    assert adapter.capture_daily(bot, tmp_path, clock)['status'] == 'captured'
    assert set(values) == {'IC', 'IM'}
    for product, expected in values.items():
        reader, _ = adapter.load_original(bot, tmp_path, product, day)
        actual = evidence.decode(reader.context['annotations']['replay_completeness'])
        assert actual == expected
        assert actual['input_capture_complete'] is True


@pytest.mark.parametrize('recording', [False, True])
def test_signal_calendar_2703_target_is_strict_even_with_recording(tmp_path, recording):
    from contextlib import nullcontext
    day = date(2026, 11, 23)
    ctx = {'expiries': [], 'failures': []}
    token = bot._SIGNAL_CALENDAR_DIAGNOSTICS.set(ctx)
    try:
        with (evidence.capture_session(tmp_path, day, seed.clock(day), {}) if recording else nullcontext()):
            with pytest.raises(bot.CalendarCoverageError):
                bot.require_signal_calendar('IC', {'market_date': day, 'next_trade_date': date(2026, 11, 24),
                    'core_target': 'IC2612', 'v14_core_put_contract': '510500P2703M07500',
                    'v14_core_put_qty': 14})
        assert ctx['failures']
    finally:
        bot._SIGNAL_CALENDAR_DIAGNOSTICS.reset(token)


def test_same_december_url_cannot_alias_2612_and_2712_bodies(tmp_path):
    url = 'https://yunhq.sse.com.cn:32042/v1/sho/list/tstyle/510500_12'
    rows = [[url, r5.PARAMS, json.dumps({'contract': '510500P' + month + 'M07500'}).encode(),
             {}, 200, 'utf-8'] for month in ['2612', '2712']]
    reader = r5.raw_archive(tmp_path, rows)
    with pytest.raises(evidence.EvidenceUnavailable, match='ambiguous'):
        reader.http_response(url, r5.PARAMS)
    assert reader.missing_inputs[-1]['reason'] == 'ambiguous_recorded_responses'


def test_new_historical_url_is_counted_and_never_falls_through_to_network(tmp_path, monkeypatch):
    reader = r5.raw_archive(tmp_path, [[r5.URL, r5.PARAMS, b'original live body', {}, 200, 'utf-8']])
    url = 'https://www.cffex.com.cn/sj/historysj/202610/zip/202610.zip'
    with evidence.replay_session(reader), bot.historical_replay(r5.DAY):
        with pytest.raises(evidence.EvidenceUnavailable, match='no recorded HTTP'):
            bot._evidence_http_get(url)
    assert reader.missing_inputs == [{'role': 'http', 'url': url, 'params': None}]


def missing_fear(day, clock):
    return {'fear_source_policy_revision': bot.fear_grid.SOURCE_POLICY_REVISION,
        'fear_greed_index': None, 'fear_data_date': None, 'fear_data_status': 'unavailable',
        'fear_unavailable_reason': 'synthetic original source outage', 'fear_retrieved_at': clock.isoformat(),
        'fear_fetch_started_at': clock.isoformat(), 'fear_available_sources': [], 'fear_selected_source': None,
        'fear_cross_validation': 'unavailable', 'fear_source_warning': 'synthetic outage',
        'fear_fallback_applied': False, 'fear_csv_sha256': None, 'fear_page_sha256': None,
        'fear_page_updated_at': None, 'fear_page_data_date': None}


def test_missing_fear_day_replayed_then_following_day_bridge_and_real_store_resume(tmp_path, monkeypatch):
    first, second, previous = date(2026, 10, 21), date(2026, 10, 22), date(2026, 10, 20)
    store, original = seed.temporary_store(tmp_path / 'state', previous)
    bot.install_runtime_anchors(state.anchors_from_record(original))
    first_clock = r4.wall(monkeypatch, first)
    samples = r4.providers(monkeypatch, first)
    samples['IM'][3].clear()
    samples['IM'][3].update(missing_fear(first, first_clock))
    root = tmp_path / 'evidence'
    assert adapter.capture_daily(bot, root, first_clock)['status'] == 'captured'
    owner = r5.restore_owner(tmp_path, monkeypatch, store, root)
    _, _, observed = owner.execute_query('信号', first_clock)
    assert set(observed) == {'IC', 'IM'}
    assert all(s['persistence_eligible'] is False and s['signal_complete'] is False for s in observed.values())
    assert store.load_latest()['digest'] == original['digest']

    second_clock = r4.wall(monkeypatch, second)
    second_samples = r4.providers(monkeypatch, second)
    # Original collection continues even though the prior day is not verified.
    assert adapter.capture_daily(bot, root, second_clock)['status'] == 'captured'
    for product in ('IC', 'IM'):
        with pytest.raises(RuntimeError, match='审计账本仅逐腿核验至'):
            bot._validate_signal_market_date(product, second, second_clock, 'close')
    assert owner.catch_up_once(second_clock) is False
    assert store.load_latest()['digest'] == original['digest']

    def fear(day, mode):
        return r4.fear_history(first, second_clock) if day == first else deepcopy(second_samples['IM'][3])
    monkeypatch.setattr(bot, '_fear_score_evidence', fear)
    adapter._keyed(bot, bot, '_fear_score_evidence', 'fear.score', lambda day, mode: {'day': day, 'mode': mode})
    assert owner.catch_up_once(second_clock), owner.last_refresh_error
    middle = store.load_latest()
    assert middle['verified_day'] == str(first) and middle['sequence'] == 1
    assert middle['previous_digest'] == original['digest']
    for signal in middle['signals'].values():
        assert signal['signal_generation_timing'] == 'retrospective_replay'
        assert signal['persistence_eligible'] and signal['signal_complete']
        assert signal['original_evidence']['unrecorded_inputs'] == []
        supplement = signal['historical_evidence_supplements']
        assert any(item['role'] == 'fear.score' and item['original_close'] is False for item in supplement)
    bot.install_runtime_anchors(state.anchors_from_record(middle))
    for product in ('IC', 'IM'):
        assert bot._validate_signal_market_date(product, second, second_clock, 'close') is True
    assert owner.catch_up_once(second_clock), owner.last_refresh_error
    last = store.load_latest()
    assert last['verified_day'] == str(second) and last['sequence'] == 2
    assert last['previous_digest'] == middle['digest']
    assert all(s['persistence_eligible'] and s['signal_complete'] for s in last['signals'].values())
