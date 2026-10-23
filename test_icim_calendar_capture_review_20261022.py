"""Official ETF expiry oracle plus synthetic independent-capture adversaries."""
from copy import deepcopy
from datetime import date
import hashlib
import ast
import importlib.util
import json
from pathlib import Path

import pytest
import requests

import ic_im_evidence_adapter_v1 as adapter
import ic_im_signal_evidence_v1 as evidence
import poe_ic_im_mainline_v1_4_bot as bot
import test_icim_round3_recovery_audit_20261021 as seed
import test_icim_round4_recovery_20261021 as r4
from test_icim_expert_followup_replay_20261022 import raw_chain_provider


ROOT = Path(__file__).resolve().parent
RUN = ROOT / 'quant_research_runs/20261022_calendar_capture_review_v1'
DAY = date(2026, 10, 21)


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(bot, 'LIVE_CONTINUATION_ANCHOR', deepcopy(bot.LIVE_CONTINUATION_ANCHOR))
    monkeypatch.setenv('ICIM_STATE_DIR', str(tmp_path / 'isolated'))
    monkeypatch.delenv('ICIM_EVIDENCE_DIR', raising=False)
    monkeypatch.setattr(bot.requests, 'get', lambda *a, **k: pytest.fail('real network forbidden'))
    monkeypatch.setattr(bot, 'build_iv_warning', lambda *a, **k: {'status': 'synthetic_monitor_omitted'})


def test_510500_2301_expiry_comes_from_official_option_announcements():
    oracle = json.loads((ROOT / 'tests/fixtures/icim_sse_2301_expiry_20261022.json').read_text(encoding='utf-8'))
    assert oracle['underlying'] == '510500' and oracle['contract_month'] == '2301'
    assert len(oracle['sources']) == 2
    assert {s['announced_expiry_day'] for s in oracle['sources']} == {'2023-01-30'}
    assert all(s['url'].startswith('https://www.sse.com.cn/assortment/options/disclo/update/') for s in oracle['sources'])
    assert str(bot._fourth_wednesday(2023, 1)) == oracle['expiry_day'] == '2023-01-30'
    assert oracle['exercise_settlement_day'] == '2023-01-31'


def test_calendar_extension_precedes_last_followup_and_this_round():
    name = 'poe_ic_im_mainline_v1_4_bot.py'
    def bound(folder, stage):
        p = ROOT / 'quant_research_runs' / folder / stage / 'source_after.json'
        return json.loads(p.read_text())[name]
    # This assertion describes the already-reviewed calendar round, whose
    # bytes now live in the pre-integration backup. Later transport fixes must
    # not overwrite its historical receipt or pretend current code is identical.
    previous_source = ROOT / 'quant_research_runs/20261022_free_source_integration_v1/backup' / name
    digest = hashlib.sha256(previous_source.read_bytes()).hexdigest()
    def calendars(path):
        tree = ast.parse(path.read_text(encoding='utf-8-sig'))
        names = {'_OFFICIAL_EXCHANGE_CALENDAR_YEARS', '_EXCHANGE_CLOSURES'}
        return [ast.dump(node, include_attributes=False) for node in tree.body
                if isinstance(node, (ast.Assign, ast.AnnAssign))
                and any(isinstance(target, ast.Name) and target.id in names
                        for target in (node.targets if isinstance(node, ast.Assign) else [node.target]))]
    assert calendars(ROOT / name) == calendars(previous_source)
    assert digest == bound('20261022_historical_calendar_repair_v1', 'accepted_02')
    assert digest == bound('20261022_expert_followup_replay_audit_v1', 'accepted_01')
    assert digest == '0075c16aec68cb56b49569d03c1b89bb2707873fbd309971461082b623d0d19f'


@pytest.mark.parametrize('day,hour,minute', [(DAY, 9, 15), (DAY, 14, 59), (date(2026, 10, 24), 18, 0)])
def test_collector_never_opens_original_close_session_before_close_or_on_weekend(monkeypatch, tmp_path, day, hour, minute):
    now = r4.wall(monkeypatch, day).replace(hour=hour, minute=minute)
    monkeypatch.setattr(evidence, 'capture_session', lambda *a, **k: pytest.fail('opened an original close capture'))
    result = adapter.capture_daily(bot, tmp_path / 'absent', now)
    assert result['status'] == 'waiting_for_close' and result['products'] == {}
    assert not (tmp_path / 'absent').exists()


def test_existing_core_and_short_identity_use_one_chain_record_without_assuming_stable_http(monkeypatch, tmp_path):
    clock = r4.wall(monkeypatch, DAY)
    _, record = seed.temporary_store(tmp_path / 'state', date(2026, 10, 20))
    import poe_ic_im_v1_4_state as state
    bot.install_runtime_anchors(state.anchors_from_record(record))
    real_chain = bot._fetch_510500_chain_for_day
    samples = r4.providers(monkeypatch, DAY)
    calls = raw_chain_provider(monkeypatch, DAY, samples, real_chain)
    anchor = deepcopy(bot.LIVE_CONTINUATION_ANCHOR['IC'])
    anchor.update(v14_short_put_contract=anchor['v14_core_put_contract'],
                  v14_short_put_security_id=anchor['v14_core_put_security_id'])
    signal = {'market_date': DAY, 'core_put_target_delta': .25, 'etf_price': 7.8, 'future_last': 7800.,
              'put_target_momentum_qty': 0, 'option_monthly_reset_due': False}
    with bot.runtime_clock(clock), evidence.capture_session(tmp_path / 'evidence', DAY, clock, {}) as session:
        bot._fetch_510500_chain_for_day('2612', DAY)
        bot._v14_ic_core_overlay(signal, anchor)
        bot._v14_require_held_ic_short_identity(signal, anchor)
    assert len(calls) == 1 and len(session.raw) == 1
    assert session.context['complete'] is True
    assert signal['v14_core_put_contract'] == anchor['v14_core_put_contract']


def changing_raw_providers(monkeypatch, tmp_path, difference):
    clock = r4.wall(monkeypatch, DAY)
    _, record = seed.temporary_store(tmp_path / 'state', date(2026, 10, 20))
    import poe_ic_im_v1_4_state as state
    bot.install_runtime_anchors(state.anchors_from_record(record))
    samples = r4.providers(monkeypatch, DAY)
    counts = {}
    def get(url, params=None, **kwargs):
        ym = str(url).rsplit('_', 1)[-1]
        counts[ym] = counts.get(ym, 0) + 1
        second = ym == '12' and counts[ym] % 2 == 0
        payload = {'month': ym, 'time': 150000, 'price': .4}
        if second and difference == 'body':
            payload['price'] = .41
        if second and difference == 'time':
            payload['time'] = 150001
        response = requests.Response()
        response.status_code = 201 if second and difference == 'status' else 200
        response.encoding = 'gbk' if second and difference == 'encoding' else 'utf-8'
        response.headers['ETag'] = 'second' if second and difference == 'headers' else 'first'
        response._content = json.dumps(payload).encode('utf-8')
        return response
    monkeypatch.setattr(bot.requests, 'get', get)
    def chain(month, day):
        url = 'https://yunhq.sse.com.cn:32042/v1/sho/list/tstyle/510500_' + month[-2:]
        for _ in range(2 if month == '2612' else 1):
            bot._evidence_http_get(url, params={'select': 'synthetic_recording_probe'})
        return deepcopy(samples['IC'][4][month])
    monkeypatch.setattr(bot, '_fetch_510500_chain_for_day', chain)
    adapter._keyed(bot, bot, '_fetch_510500_chain_for_day', 'ic.chain', lambda month, day: {'month': month, 'day': day})
    return clock


@pytest.mark.parametrize('difference', ['identical', 'body', 'time', 'headers', 'status', 'encoding'])
def test_ambiguous_capture_is_incomplete_and_does_not_break_normal_query(monkeypatch, tmp_path, difference):
    clock = changing_raw_providers(monkeypatch, tmp_path, difference)
    root = tmp_path / 'evidence'
    result = adapter.capture_daily(bot, root, clock)
    if difference == 'identical':
        assert result['status'] == 'captured'
        reader, _ = adapter.load_original(bot, root, 'IC', DAY)
        assert evidence.decode(reader.context['annotations']['replay_completeness'])['input_capture_complete'] is True
    else:
        assert result['products']['IC']['status'] == 'incomplete'
        assert result['products']['IM']['status'] == 'captured'
        assert 'raw_http_conflict' in result['products']['IC']['reason']
        with pytest.raises(evidence.EvidenceUnavailable, match='incomplete|unresolved'):
            adapter.load_original(bot, root, 'IC', DAY)
    assert evidence.current_session.get() is None
    live = bot.build_live_trade_signal('IC', clock, 'close')
    assert live.get('signal_complete') and live.get('persistence_eligible'), live.get('diagnostic_reason')


def test_archive_io_failure_stays_in_independent_collector_and_signal_still_returns(monkeypatch, tmp_path):
    clock = changing_raw_providers(monkeypatch, tmp_path, 'identical')
    def fail(*args, **kwargs):
        raise OSError('synthetic full evidence disk')
    monkeypatch.setattr(evidence.CaptureSession, 'raw_response', fail)
    result = adapter.capture_daily(bot, tmp_path / 'evidence', clock)
    assert result['products']['IC']['status'] == 'incomplete'
    assert evidence.current_session.get() is None
    live = bot.build_live_trade_signal('IC', clock, 'close')
    assert live.get('signal_complete') and live.get('persistence_eligible'), live.get('diagnostic_reason')


def comparator():
    spec = importlib.util.spec_from_file_location('review_strict_comparator', RUN / 'replay_acceptance.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.compare_signal_values


@pytest.mark.parametrize('field,new_value', [('qty', 2), ('price', .5), ('reason', 'different'),
                                            ('source', 'different'), ('signal_complete', False)])
def test_real_package_comparator_does_not_silently_ignore_strategy_or_source_changes(field, new_value):
    original = {'qty': 1, 'price': .4, 'reason': 'original', 'source': 'SSE', 'signal_complete': True}
    replayed = {**original, field: new_value}
    differences = comparator()(original, replayed)
    assert [r['path'] for r in differences] == ['$.' + field]


def test_real_package_comparator_allows_only_explicit_generation_timestamp():
    a = {'signal_generated_at': '2026-10-21T18:00:00+08:00', 'market_date': '2026-10-21'}
    b = {**a, 'signal_generated_at': '2026-10-22T18:00:00+08:00'}
    assert comparator()(a, b) == []
    b['market_date'] = '2026-10-22'
    assert comparator()(a, b)[0]['path'] == '$.market_date'


def test_real_package_comparator_rejects_extra_replay_fields():
    assert comparator()({'qty': 1}, {'qty': 1, 'replay_only': True})[0]['reason'] == 'field_missing'
