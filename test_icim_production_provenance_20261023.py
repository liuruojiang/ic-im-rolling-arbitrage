"""Expert A–H follow-up: real threads and stores, offline market fixtures."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import date, datetime
import contextvars
import json
from pathlib import Path
import re
import threading
from urllib.parse import parse_qs

import pandas as pd
import pytest
import requests

import ic_im_evidence_adapter_v1 as adapter
import ic_im_signal_evidence_v1 as evidence
import poe_ic_im_mainline_v1_4_bot as bot
import poe_ic_im_v1_4_state as state

DAY = date(2026, 10, 22)
CLOCK = datetime(2026, 10, 22, 18, tzinfo=bot.BEIJING)
ROOT = Path(__file__).resolve().parent
SAVED = ROOT / 'quant_research_runs/20261022_free_ic_sources_v1'


def response(body):
    value = requests.Response()
    value.status_code, value.encoding = 200, 'gb18030'
    value._content, value._content_consumed = body, True
    return value


def frame():
    return pd.DataFrame([{'contract': '510500P2612M06500', 'last': .2614,
                          'strike': 6.5, 'chg_rate': 0., 'pre_settle': .25,
                          'security_id': '10012149'}])


def bare(name):
    return getattr(getattr(bot, name), '__wrapped__', getattr(bot, name))


def test_query_chain_cache_hit_revalidates_and_keeps_selected_row_time_gate(monkeypatch):
    tick, network, checks = [0.], [], []
    clock = CLOCK.replace(hour=10, minute=0)
    monkeypatch.setattr(bot.time_module, 'monotonic', lambda: tick[0])
    chain = frame()
    chain['source_stamp'] = clock
    def fetch(month):
        network.append(month)
        return chain, {'date': '20261022', 'time': '100000'}
    monkeypatch.setattr(bot, 'fetch_510500_chain_with_failover', fetch)
    validate = bot._validate_chain_stamp_matches
    def checked(*args):
        checks.append(bot._iv_observation_clock(args[-1]))
        return validate(*args)
    monkeypatch.setattr(bot, '_validate_chain_stamp_matches', checked)
    token = bot._IC_QUOTE_QUERY_CACHE.set({})
    try:
        with bot.runtime_clock(clock), bot.collection_clock(clock):
            first = bot._fetch_510500_chain_for_day('2612', DAY)
            bot._require_selected_510500_quote_time(first[0].iloc[0], clock, DAY)
            tick[0] = 21 * 60
            second = bot._fetch_510500_chain_for_day('2612', DAY)
            assert len(checks) == 2 and checks[-1] > checks[0]
            with pytest.raises(RuntimeError, match='20分钟'):
                bot._require_selected_510500_quote_time(second[0].iloc[0], clock, DAY)
    finally:
        bot._IC_QUOTE_QUERY_CACHE.reset(token)
    assert network == ['2612']


def test_capture_role_cache_hit_revalidates_consumed_chain(monkeypatch, tmp_path):
    calls, checks = [], []
    monkeypatch.setattr(bot, 'fetch_510500_chain_with_failover',
                        lambda month: (calls.append(month) or frame(), {'date': '20261022', 'time': '150000'}))
    validate = bot._validate_chain_stamp_matches
    def checked(*args):
        checks.append(args[0])
        return validate(*args)
    monkeypatch.setattr(bot, '_validate_chain_stamp_matches', checked)
    with bot.runtime_clock(CLOCK), evidence.capture_session(tmp_path, DAY, CLOCK, {}):
        bot._fetch_510500_chain_for_day('2612', DAY)
        before = len(checks)
        bot._fetch_510500_chain_for_day('2612', DAY)
        assert len(checks) > before
    assert calls == ['2612']


def test_security_id_direct_sina_chain_remains_outside_month_cache(monkeypatch):
    calls = []
    monkeypatch.setattr(bot, 'fetch_sina_510500_chain', lambda month: (calls.append(month) or frame(), {}))
    token = bot._IC_QUOTE_QUERY_CACHE.set({})
    try:
        for _ in range(2):
            assert bot.fetch_sina_510500_security_id('510500P2612M06500') == '10012149'
    finally:
        bot._IC_QUOTE_QUERY_CACHE.reset(token)
    assert calls == ['2612', '2612']


@pytest.mark.parametrize('kind', ['etf', 'months'])
def test_raw_replay_missing_failed_primary_reaches_recorded_fallback(monkeypatch, tmp_path, kind):
    target = 'fetch_510500_quote_with_failover' if kind == 'etf' else 'fetch_510500_expiries_with_failover'
    packet = SAVED / ('probe_02/raw/tencent_etf.bin' if kind == 'etf' else 'probe_02/raw/sina_listed_months.bin')
    def get(url, **kwargs):
        if 'yunhq.sse.com.cn' in url:
            raise requests.Timeout('original primary had no response')
        return response(packet.read_bytes())
    monkeypatch.setattr(bot.requests, 'get', get)
    def run():
        return bare(target)(DAY) if kind == 'etf' else bare(target)()
    with bot.runtime_clock(CLOCK), evidence.capture_session(tmp_path, DAY, CLOCK, {}) as session:
        session.call('scope', {}, lambda: True)
        original = run()
    reader = evidence.load_session(tmp_path, DAY)
    assert all('yunhq.sse.com.cn' not in r['url'] for r in reader.context['raw'])
    monkeypatch.setattr(bot.requests, 'get', lambda *a, **k: pytest.fail('replay network request'))
    with bot.runtime_clock(CLOCK), evidence.replay_session(reader):
        replay = run()
    if kind == 'etf':
        assert {k: replay[k] for k in ['last','prev_close','date','time','source']} == {
            k: original[k] for k in ['last','prev_close','date','time','source']}
        assert any('EvidenceUnavailable' in s for s in replay['source_failures'])
    else:
        assert replay == original == ['202610','202611','202612','202703']
    # Failed-primary probes are visible in the audit log; do not call this zero misses.
    assert len(reader.missing_inputs) == 1 and 'yunhq.sse.com.cn' in reader.missing_inputs[0]['url']


@pytest.mark.parametrize('target,args', [
    ('fetch_510500_quote_with_failover', (DAY,)),
    ('fetch_510500_expiries_with_failover', ()),
    ('fetch_510500_chain_with_failover', ('2612',))])
def test_exhausted_raw_replay_preserves_evidence_unavailable(monkeypatch, tmp_path, target, args):
    with evidence.capture_session(tmp_path, DAY, CLOCK, {}) as session:
        session.call('scope', {}, lambda: True)
    reader = evidence.load_session(tmp_path, DAY)
    monkeypatch.setattr(bot.requests, 'get', lambda *a, **k: pytest.fail('replay network request'))
    with bot.runtime_clock(CLOCK), evidence.replay_session(reader):
        with pytest.raises(evidence.EvidenceUnavailable, match='原日|所有来源'):
            bare(target)(*args)


def test_public_original_replay_reports_missing_source_as_original_evidence_unavailable(monkeypatch, tmp_path):
    with evidence.capture_session(tmp_path, DAY, CLOCK, {}) as session:
        session.call('scope', {}, lambda: True)
    reader = evidence.load_session(tmp_path, DAY)
    monkeypatch.setattr(adapter, 'configured_root', lambda: tmp_path)
    monkeypatch.setattr(adapter, 'has_original', lambda *a: True)
    monkeypatch.setattr(adapter, 'load_original', lambda *a: (reader, CLOCK))
    monkeypatch.setattr(bot, '_build_live_trade_signal', lambda *a: bare('fetch_510500_quote_with_failover')(DAY))
    monkeypatch.setattr(bot.requests, 'get', lambda *a, **k: pytest.fail('replay network request'))
    with bot.historical_replay(DAY):
        result = bot.build_live_trade_signal('IC', CLOCK, 'close')
    assert result['diagnostic_status'] == 'original_evidence_unavailable'
    assert result['persistence_eligible'] is False


def identifiers(kind, count=None):
    return [str(10000000 + n) for n in range(count or 24)] if kind == 'ic' else [f'MO2612-P-{6000+n}' for n in range(count or 192)]


def run_batches(kind, ids):
    if kind == 'ic':
        return bot._sina_put_detail_batches(ids)
    return bot._prefetch_mo_replay_verification(pd.DataFrame({'instrument': ids}), CLOCK)


def symbols_from_request(url, kwargs):
    if 'etag.php' in url:
        params = kwargs['params']
        return (parse_qs(params)['list'][0] if isinstance(params, str) else params['list']).split(',')
    return url.split('list=')[1].split(',')


def wire(symbols):
    return b';'.join(('var hq_str_' + value + '="offline-synthetic"').encode() for value in symbols) + b';'


@pytest.mark.parametrize('kind', ['ic', 'mo'])
def test_batch_failure_cancels_unstarted_and_joins_active_writer(monkeypatch, tmp_path, kind):
    first, failure, third, release, returned = (threading.Event() for _ in range(5))
    calls, active, errors = [], set(), []
    lock = threading.Lock()
    def get(url, **kwargs):
        symbols = symbols_from_request(url, kwargs)
        with lock:
            index = len(calls); calls.append(symbols); active.add(threading.get_ident())
        try:
            assert evidence.current_session.get() is not None
            assert bot._NETWORK_DEADLINE.get() is not None
            if index == 0:
                first.set()
                assert release.wait(3)
            elif index == 1:
                assert first.wait(3)
                failure.set()
                raise RuntimeError('synthetic invalid batch')
            else:
                third.set()
            return response(wire(symbols))
        finally:
            with lock:
                active.remove(threading.get_ident())
    monkeypatch.setattr(bot.requests, 'get', get)
    def observer():
        try:
            assert failure.wait(3)
            assert not returned.is_set()
            third.wait(.1)  # deterministic blocked first job exposes eager queue draining.
        except BaseException as exc:
            errors.append(exc)
        finally:
            release.set()
    watcher = threading.Thread(target=observer)
    watcher.start()
    try:
        with pytest.raises(RuntimeError, match='synthetic invalid batch'):
            with evidence.capture_session(tmp_path, DAY, CLOCK, {}) as session:
                session.call('scope', {}, lambda: True)
                with bot._network_budget(45):
                    try:
                        run_batches(kind, identifiers(kind))
                    finally:
                        returned.set()
                        assert not active
    finally:
        watcher.join(4)
    assert not errors and not watcher.is_alive()
    assert len(calls) == 2 and not third.is_set()
    assert session.context['complete'] is False


@pytest.mark.parametrize('kind', ['ic', 'mo'])
def test_pool_shutdown_time_counts_against_product_budget(monkeypatch, kind):
    tick, deadlines = [0.], []
    monkeypatch.setattr(bot.time_module, 'monotonic', lambda: tick[0])
    class ShutdownAfterDeadline(ThreadPoolExecutor):
        def __exit__(self, *args):
            result = super().__exit__(*args)
            tick[0] = 46.  # model running-request/shutdown tail, not synthetic network speed.
            return result
    monkeypatch.setattr(bot, 'ThreadPoolExecutor', ShutdownAfterDeadline)
    def get(url, **kwargs):
        deadlines.append(bot._NETWORK_DEADLINE.get())
        return response(wire(symbols_from_request(url, kwargs)))
    monkeypatch.setattr(bot.requests, 'get', get)
    with bot._network_budget(45), pytest.raises(RuntimeError, match='预算'):
        run_batches(kind, identifiers(kind, 12 if kind == 'ic' else 128))
    assert deadlines and set(deadlines) == {45.}


@pytest.mark.parametrize('kind', ['ic', 'mo'])
def test_threaded_raw_capture_is_atomic_and_replay_is_order_independent(monkeypatch, tmp_path, kind):
    calls, workers = [], set()
    lock = threading.Lock()
    barrier = threading.Barrier(2)
    def get(url, **kwargs):
        symbols = symbols_from_request(url, kwargs)
        with lock:
            index = len(calls); calls.append((url, symbols)); workers.add(threading.get_ident())
        if index < 2:
            barrier.wait(3)
        assert evidence.current_session.get() is not None
        return response(wire(symbols))
    monkeypatch.setattr(bot.requests, 'get', get)
    ids = identifiers(kind, 18 if kind == 'ic' else 192)
    with evidence.capture_session(tmp_path, DAY, CLOCK, {}) as session:
        session.call('scope', {}, lambda: True)
        with bot._network_budget(45):
            result = run_batches(kind, ids)
    assert len(workers) == 2 and len(session.context['raw']) == 3 and session.context['complete']
    reader = evidence.load_session(tmp_path, DAY)
    assert len({(r['url'], evidence.canonical_http_params(r['params'])) for r in reader.context['raw']}) == 3
    for row in reversed(reader.context['raw']):
        replay = reader.http_response(row['url'], evidence.decode(row['params']))
        assert replay['body'] == evidence._verified_blob(tmp_path, row)
    assert reader.missing_inputs == []
    if kind == 'ic':
        assert [sid for sid, _ in result] == ids


@pytest.mark.parametrize('product', ['IC', 'IM'])
@pytest.mark.parametrize('status', ['recording', 'incomplete', 'inconsistent'])
def test_state_store_rejects_capture_integrity_without_advancing_day(monkeypatch, tmp_path, product, status):
    from test_icim_b3adv_state_20261021 import prepare
    from test_icim_round2_audit_lifecycle_20261021 import snapshot
    store, current, signals = prepare(tmp_path, monkeypatch)
    signals[product].update(persistence_eligible=True, signal_complete=True)
    signal = signals[product]
    clock = datetime(2026, 10, 21, 15, 40, tzinfo=bot.BEIJING)
    before = snapshot(store.root)
    with evidence.capture_session(tmp_path / 'evidence', clock.date(), clock, {}) as session:
        session.call('scope', {}, lambda: True)
        session.bind_signal(signal)
        if status == 'recording':
            signal['persistence_eligible'] = True  # active-session store gate is independent.
            with pytest.raises(RuntimeError, match='采集'):
                store.append_confirmed_signals(current, signals)
        elif status == 'incomplete':
            session.failures.append({'role': 'probe', 'resolved': False})
        else:
            session.raw_response('https://example.invalid/quote', None, response(b'a'))
            session.raw_response('https://example.invalid/quote', None, response(b'b'))
    if status != 'recording':
        assert signal['input_evidence_status'] == status
        signal['persistence_eligible'] = True  # forged boolean cannot override integrity marker.
        with pytest.raises(RuntimeError, match='采集'):
            store.append_confirmed_signals(current, signals)
    assert snapshot(store.root) == before
    latest = store.load_latest()
    assert latest['verified_day'] == current['verified_day'] and latest['digest'] == current['digest']


def test_sealed_complete_capture_allows_normal_state_store_advance(monkeypatch, tmp_path):
    from test_icim_b3adv_state_20261021 import prepare
    store, current, signals = prepare(tmp_path, monkeypatch)
    clock = datetime(2026, 10, 21, 15, 40, tzinfo=bot.BEIJING)
    with evidence.capture_session(tmp_path / 'evidence', clock.date(), clock, {}) as session:
        session.call('scope', {}, lambda: True)
        for signal in signals.values():
            signal.update(persistence_eligible=True, signal_complete=True)
            session.bind_signal(signal)
    assert all(s['input_evidence_status'] == 'complete' and s['persistence_eligible'] for s in signals.values())
    written = store.append_confirmed_signals(current, signals)
    assert written['sequence'] == current['sequence'] + 1


def test_collection_clock_nested_observation_batch_preserves_outer_fear_evidence():
    fear = bot.fear_grid
    with fear.observation_batch():
        outer = fear._OBSERVATION_BATCH.get()
        outer[('baifenwei', DAY, 'close')] = {'fear_greed_index': 30., 'source': 'synthetic outer evidence'}
        with bot.collection_clock(CLOCK), fear.observation_batch():
            assert fear._OBSERVATION_BATCH.get() is outer
            seen = fear.observed_in_batch('baifenwei', DAY, 'close')
            assert seen['fear_greed_index'] == 30.
            seen['fear_greed_index'] = 999
        assert fear.observed_in_batch('baifenwei', DAY, 'close')['fear_greed_index'] == 30.
    assert fear._OBSERVATION_BATCH.get() is None
