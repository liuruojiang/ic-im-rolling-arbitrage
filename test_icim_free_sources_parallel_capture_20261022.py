"""Independent bounded-parallel collector tests; all market inputs are synthetic.

These tests exercise real worker threads and the real CaptureSession. They
never contact a provider or read/write a formal ledger. Virtual deadlines are
absolute shared instants, not serial sums of concurrent task durations.
"""
from collections import Counter
from contextlib import nullcontext
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import threading
from types import SimpleNamespace

import pandas as pd
import pytest
import requests

import ic_im_evidence_adapter_v1 as adapter
import ic_im_signal_evidence_v1 as evidence
import poe_ic_im_mainline_v1_4_bot as bot

DAY = date(2026, 10, 22)
CLOCK = datetime(2026, 10, 22, 18, tzinfo=bot.BEIJING)


def fake_strategy(monkeypatch, *, held=True, held_month='2612', shared_roles=False, failed_role=None, expire=False):
    lock = threading.RLock()
    state = SimpleNamespace(events=[], source_counts=Counter(), active=Counter(), max_threads=0,
                            deadlines={}, first_counts=Counter(), tick=0., finishes=0, submitted_months=[])
    original_pool = adapter.ThreadPoolExecutor
    class ObservedPool(original_pool):
        def submit(self, fn, *args, **kwargs):
            # Observe submission priority, which is deterministic. Thread
            # scheduling/start order after submission intentionally is not.
            defaults = getattr(args[-1], '__defaults__', None) if args else None
            if defaults and len(defaults) == 1 and defaults[0] in {'2610','2611','2612','2703','2609'}:
                state.submitted_months.append(defaults[0])
            return super().submit(fn, *args, **kwargs)
    monkeypatch.setattr(adapter, 'ThreadPoolExecutor', ObservedPool)
    barriers = {p: threading.Barrier(4) for p in ['IC','IM']}
    expired_barriers = {p: threading.Barrier(4) for p in ['IC','IM']}
    if expire:
        monkeypatch.setattr(bot.time_module, 'monotonic', lambda: state.tick)
    monkeypatch.setattr(evidence, '_now', lambda: CLOCK + timedelta(seconds=state.tick))
    class Wall(datetime):
        @classmethod
        def now(cls, tz=None):
            return CLOCK.astimezone(tz) if tz else CLOCK.replace(tzinfo=None)
    monkeypatch.setattr(adapter, 'datetime', Wall)
    original_finish = evidence.CaptureSession.finish
    def finish(session):
        with lock:
            assert not state.active, 'session sealed while a provider writer remains active'
            state.finishes += 1
        return original_finish(session)
    monkeypatch.setattr(evidence.CaptureSession, 'finish', finish)
    monkeypatch.setattr(bot.requests, 'get', lambda *a, **k: pytest.fail('unexpected network'))

    def source(role, key, value, *, embedded=False):
        session = evidence.current_session.get()
        assert session is not None
        product = evidence.decode(session.metadata)['product']
        def load():
            assert bot._RUNTIME_CLOCK.get() == CLOCK
            assert bot._COLLECTION_CLOCK_ANCHOR.get() is not None
            deadline = bot._NETWORK_DEADLINE.get()
            assert deadline is not None
            tid = threading.get_ident()
            signature = (product, role, json.dumps(evidence.encode(key), sort_keys=True))
            with lock:
                state.source_counts[signature] += 1
                state.active[tid] += 1
                state.max_threads = max(state.max_threads, len(state.active))
                state.events.append((product, role, key, tid, state.tick))
                state.deadlines.setdefault(product, set()).add(deadline)
                state.first_counts[product] += 1
                first_four = state.first_counts[product] <= 4
            try:
                if first_four:
                    barriers[product].wait(timeout=3)
                    if expire:
                        with lock:
                            state.tick = deadline
                        expired_barriers[product].wait(timeout=3)
                if role == failed_role:
                    raise RuntimeError('injected provider validation failure')
                if embedded:
                    source('fear.score', {'day': DAY, 'mode': 'close'}, {'fear_greed_index':30.})
                    source('rate.resolve', {'product':product}, .02)
                # This is an actual stored raw test response, not a new real
                # market fixture; its source is explicitly synthetic.
                packet = requests.Response()
                packet.status_code = 200
                packet.encoding = 'utf-8'
                packet._content = json.dumps({'synthetic_role':role}).encode()
                packet._content_consumed = True
                session.raw_response('https://example.test/' + role, key, packet)
                return value
            finally:
                with lock:
                    state.active[tid] -= 1
                    if not state.active[tid]:
                        del state.active[tid]
        return session.call(role, key, load)

    months = ['202610','202611','202612','202703']
    frame = pd.DataFrame([{'synthetic_context_probe':1}])
    frame.attrs['source'] = '东方财富'
    strategy = SimpleNamespace(
        BEIJING=bot.BEIJING, BUILD_ID=bot.BUILD_ID, SIGNAL_PRODUCT_NETWORK_BUDGET_SECONDS=45,
        STRATEGY_QUERY_LOCK=threading.RLock(), runtime_clock=bot.runtime_clock,
        _network_budget=bot._network_budget, _bounded_timeout=bot._bounded_timeout,
        _require_official_exchange_calendar=lambda *a: None,
        _is_exchange_trading_day=lambda *a: True, _market_phase=lambda *a: '收盘后',
        LIVE_CONTINUATION_ANCHOR={p:{'last_verified_day':DAY,
            'v14_core_put_contract':'510500P' + held_month + 'M06500' if held else None,
            'post_put_contract':'510500P' + held_month + 'M06500' if held else None} for p in ['IC','IM']},
        fetch_sse_510500_expiries=lambda: source('ic.months',{},months),
        fetch_sse_510500_quote=lambda: source('ic.etf',{}, {'last':6.684}),
        _fetch_510500_chain_for_day=lambda month, day: source('ic.chain',{'month':month,'day':day},frame),
        fetch_cffex_quotes=lambda product, clock: source('cffex.quotes',{'product':product},frame),
        live_proxy=lambda product, clock: source('market.live',{'product':product}, {}, embedded=shared_roles),
        _fear_score_evidence=lambda day, mode: source('fear.score',{'day':day,'mode':mode},{'fear_greed_index':30.}),
        _fear_reference_evidence=lambda day: source('fear.reference',{'day':day},{'jiu_reference_only':True}),
        fear_grid=SimpleNamespace(fetch_score=lambda *a: pytest.fail('wrong direct fear adapter')),
        _gov10y_for_day=lambda product, day: source('rate.resolve',{'product':product},.02),
        _prefetch_mo_replay_verification=lambda quotes, clock: source('mo.replay_verification',{'day':DAY},{'complete':True}))
    strategy.fetch_510500_expiries_with_failover = strategy.fetch_sse_510500_expiries
    strategy.fetch_510500_quote_with_failover = strategy.fetch_sse_510500_quote
    return strategy, state


@pytest.mark.parametrize('held', [False, True])
def test_real_threads_inherit_session_clock_deadline_and_capture_every_observed_month(monkeypatch, tmp_path, held):
    strategy, state = fake_strategy(monkeypatch, held=held)
    with bot.collection_clock(CLOCK):
        result = adapter.capture_daily(strategy, tmp_path, CLOCK)
    assert result['status'] == 'captured', result
    assert state.max_threads == 4
    assert state.finishes == 2 and not state.active
    assert all(len(values) == 1 for values in state.deadlines.values())
    for product in ['IC','IM']:
        reader = evidence.load_session(tmp_path, DAY, metadata_filter={'product':product})
        annotation = evidence.decode(reader.context['annotations']['replay_completeness'])
        assert annotation['parallel_workers'] == 4
        assert annotation['input_capture_complete'] is True
    months = [key['month'] for product, role, key, _, _ in state.events if product == 'IC' and role == 'ic.chain']
    assert sorted(months) == ['2610','2611','2612','2703']
    if held:
        assert state.submitted_months == ['2612','2610','2611','2703']
    else:
        assert state.submitted_months == ['2610','2611','2612','2703']
    assert any(product == 'IM' and role == 'mo.replay_verification' for product, role, *_ in state.events)


def test_nested_and_explicit_provider_jobs_fetch_each_shared_role_only_once(monkeypatch, tmp_path):
    strategy, state = fake_strategy(monkeypatch, shared_roles=True)
    with bot.collection_clock(CLOCK):
        result = adapter.capture_daily(strategy, tmp_path, CLOCK)
    assert result['status'] == 'captured', result
    assert all(count == 1 for count in state.source_counts.values())
    assert len([key for key in state.source_counts if key[1] == 'fear.score']) == 2
    assert len([key for key in state.source_counts if key[1] == 'rate.resolve']) == 2


def test_shared_absolute_deadline_prevents_queued_jobs_from_starting_or_extending_budget(monkeypatch, tmp_path):
    strategy, state = fake_strategy(monkeypatch, expire=True)
    with bot.collection_clock(CLOCK):
        result = adapter.capture_daily(strategy, tmp_path, CLOCK)
    assert result['status'] == 'incomplete'
    assert all(row['status'] == 'incomplete' for row in result['products'].values())
    assert state.deadlines == {'IC':{45.}, 'IM':{90.}}
    assert state.first_counts == Counter({'IC':4,'IM':4})
    assert all(tick < next(iter(state.deadlines[product])) for product, _, _, _, tick in state.events)
    assert state.tick == 90 and not state.active and state.finishes == 2
    assert '预算' in result['products']['IC']['reason']


def test_provider_failure_keeps_other_roles_and_finishes_after_every_writer(monkeypatch, tmp_path):
    strategy, state = fake_strategy(monkeypatch, failed_role='ic.etf')
    with bot.collection_clock(CLOCK):
        result = adapter.capture_daily(strategy, tmp_path, CLOCK)
    assert result['products']['IC']['status'] == 'incomplete'
    assert result['products']['IM']['status'] == 'captured'
    path = Path(result['products']['IC']['manifest'])
    body = json.loads(path.read_text(encoding='utf-8'))['body']
    roles = {row['role'] for row in body['entries'].values()}
    assert {'market.live','cffex.quotes','fear.score','rate.resolve','ic.months','ic.chain'} <= roles
    assert 'ic.etf' not in roles
    assert state.finishes == 2 and not state.active


def test_frozen_expired_anchor_does_not_require_a_delisted_month(monkeypatch, tmp_path):
    strategy, state = fake_strategy(monkeypatch, held_month='2609')
    with bot.collection_clock(CLOCK):
        result = adapter.capture_daily(strategy, tmp_path, CLOCK)
    assert result['status'] == 'captured', result
    assert state.submitted_months == ['2610','2611','2612','2703']
    assert all(key['month'] != '2609' for product, role, key, *_ in state.events if role == 'ic.chain')


def test_failed_observed_listing_cannot_fall_back_to_unobserved_anchor_months(monkeypatch, tmp_path):
    strategy, state = fake_strategy(monkeypatch, failed_role='ic.months')
    with bot.collection_clock(CLOCK):
        result = adapter.capture_daily(strategy, tmp_path, CLOCK)
    assert result['products']['IC']['status'] == 'incomplete'
    assert result['products']['IM']['status'] == 'captured'
    assert state.submitted_months == []
