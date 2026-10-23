"""Independent acceptance-contract attacks using explicit copies of real_02.

The normalized control is a test fixture, not a newly accepted real bundle:
the old run's IV output, context note and resolved-failure annotation are
explicitly repaired in copies to test the proposed contract. Source files,
original manifests, formal runtime and market values are never rewritten.
"""
from copy import deepcopy
from datetime import date, datetime, timedelta
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import ic_im_signal_evidence_v1 as evidence

ROOT = Path(__file__).resolve().parent
RUN = ROOT / 'quant_research_runs/20261022_free_source_integration_v1'
SPEC = importlib.util.spec_from_file_location('free_source_replay_contract', RUN / 'replay_contract.py')
contract = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(contract)


@pytest.fixture
def control():
    source = RUN / 'real_02'
    original = json.loads((source / 'original_ic.json').read_text(encoding='utf-8'))
    replayed = json.loads((source / 'replayed_ic.json').read_text(encoding='utf-8'))
    actual = evidence.load_session(source / 'evidence', date(2026, 10, 22), metadata_filter={'product': 'IC'})
    reader = SimpleNamespace(context=deepcopy(actual.context), metadata=deepcopy(actual.metadata),
        day=actual.day, decision_at=actual.decision_at, closed_at=actual.closed_at,
        manifest_sha256=actual.manifest_sha256, missing_inputs=[])
    # Explicit fixture-only expected producer fixes. All prices/targets and
    # source quote times remain the actual real_02 observed values.
    original['data_notes'][0] = replayed['data_notes'][0] = '市场输入来源、日期及回退情况见下列审计记录。'
    replayed['iv_warning'] = deepcopy(original['iv_warning'])
    annotation = evidence.decode(reader.context['annotations']['replay_completeness'])
    annotation['input_capture_complete'] = True
    reader.context['annotations']['replay_completeness'] = evidence.encode(annotation)
    replayed['original_evidence']['replay_completeness'] = deepcopy(annotation)
    return original, replayed, reader


def test_all_original_signal_facts_equal_with_separate_verified_replay_context(control):
    result = contract.compare_original_signal(*control)
    assert result['pass'], result
    assert not result['facts_differences'] and not result['audit_errors']
    assert result['rawdiff']  # Full raw objects still differ; never claim otherwise.
    assert set(result['calculation_clock_fields']) == {'fetch_time','signal_calculation_started_at','signal_generated_at'}


@pytest.mark.parametrize('field,value', [
    ('future_last', 999999.), ('etf_price', .01), ('v14_action', 'OPEN'),
    ('put_target_core_qty', 999), ('put_target_contract', '510500P2612M09000'),
    ('grid_target', .5), ('next_trade_date', '2026-10-26'),
    ('etf_quote_time', '150000'), ('future_quote_date', '2026-10-21'),
    ('fear_retrieved_at', '2026-10-22T15:00:00+08:00'),
])
def test_trade_facts_quote_times_and_source_acquisition_times_are_not_exempt(control, field, value):
    original, replayed, reader = control
    assert original[field] != value
    replayed[field] = value
    result = contract.compare_original_signal(original, replayed, reader)
    assert not result['pass']
    assert any(row['path'] == '$.' + field for row in result['facts_differences'])


@pytest.mark.parametrize('index', [0, 1, -1])
def test_any_source_note_change_is_a_fact_difference(control, index):
    original, replayed, reader = control
    replayed['data_notes'][index] += ' changed'
    result = contract.compare_original_signal(original, replayed, reader)
    assert not result['pass'] and any(row['path'].startswith('$.data_notes[') for row in result['facts_differences'])


@pytest.mark.parametrize('field,value', [('text','changed reason'),('snapshot_time','150000'),
    ('validation_clock','2026-10-22T15:00:00+08:00'),('iv',.99),('contract','510500P2612M09000')])
def test_complete_iv_monitor_object_is_strict_even_its_original_clock(control, field, value):
    original, replayed, reader = control
    replayed['iv_warning'][field] = value
    result = contract.compare_original_signal(original, replayed, reader)
    assert not result['pass']
    assert any(row['path'].startswith('$.iv_warning.') for row in result['facts_differences'])


@pytest.mark.parametrize('mutation', ['new','missing','different_type'])
def test_unknown_new_fields_or_missing_fields_never_disappear(control, mutation):
    original, replayed, reader = control
    if mutation == 'new': replayed['unexpected_future_status'] = 'good'
    elif mutation == 'missing': replayed.pop('v14_action')
    else: replayed['put_target_core_qty'] = str(replayed['put_target_core_qty'])
    result = contract.compare_original_signal(original, replayed, reader)
    assert not result['pass'] and result['facts_differences']


@pytest.mark.parametrize('value', [None,False,{},'',0,[{'unrecorded':'request'}]])
def test_falsy_or_nonempty_unrecorded_input_claim_is_rejected(control, value):
    original, replayed, reader = control
    replayed['original_evidence']['unrecorded_inputs'] = value
    result = contract.compare_original_signal(original, replayed, reader)
    assert not result['pass'] and result['audit_errors']


@pytest.mark.parametrize('field,value', [('manifest_sha256','0'*64),('input_revision','other'),
    ('decision_at','2026-10-22T15:00:00+08:00'),('acquired_until','2026-10-22T15:00:00+08:00'),
    ('replay_runtime','2026-10-22T15:00:00+08:00'),('replay_completeness',{})])
def test_replay_audit_values_must_bind_the_independent_reader(control, field, value):
    original, replayed, reader = control
    replayed['original_evidence'][field] = value
    result = contract.compare_original_signal(original, replayed, reader)
    assert not result['pass'] and result['audit_errors']


@pytest.mark.parametrize('value', [None,False,{},'',0,[{'source':'late supplement'}]])
def test_exact_original_recovery_does_not_allow_late_supplements(control, value):
    original, replayed, reader = control
    replayed['historical_evidence_supplements'] = value
    result = contract.compare_original_signal(original, replayed, reader)
    assert not result['pass'] and result['audit_errors']


@pytest.mark.parametrize('kind', ['reader_incomplete','reader_miss','unresolved_failure','annotation_false','wrong_product','unknown_meta','missing_meta'])
def test_reader_and_metadata_completeness_are_independent_conditions(control, kind):
    original, replayed, reader = control
    if kind == 'reader_incomplete': reader.context['complete'] = False
    elif kind == 'reader_miss': reader.missing_inputs.append({'role':'missing'})
    elif kind == 'unresolved_failure': reader.context['failures'].append({'resolved':False})
    elif kind == 'annotation_false':
        annotation = evidence.decode(reader.context['annotations']['replay_completeness'])
        annotation['input_capture_complete'] = False
        reader.context['annotations']['replay_completeness'] = evidence.encode(annotation)
        replayed['original_evidence']['replay_completeness'] = annotation
    elif kind == 'wrong_product': reader.metadata['product'] = 'IM'
    elif kind == 'unknown_meta': replayed['original_evidence']['ignored_field'] = 'anything'
    else: replayed.pop('original_evidence')
    result = contract.compare_original_signal(original, replayed, reader)
    assert not result['pass'] and result['audit_errors']


@pytest.mark.parametrize('value', [None,False,{}, {'t':'unknown'}])
def test_malformed_annotation_returns_structured_rejection(control, value):
    original, replayed, reader = control
    reader.context['annotations']['replay_completeness'] = value
    result = contract.compare_original_signal(original, replayed, reader)
    assert not result['pass'] and result['audit_errors']


@pytest.mark.parametrize('field', ['fetch_time','signal_generated_at','signal_calculation_started_at'])
@pytest.mark.parametrize('value', [None,'','not a time'])
def test_calculation_clock_exceptions_still_require_explicit_valid_times(control, field, value):
    original, replayed, reader = control
    replayed[field] = value
    result = contract.compare_original_signal(original, replayed, reader)
    assert not result['pass'] and result['audit_errors']


@pytest.mark.parametrize('kind', ['fetch_wrong_original_end','original_outside_capture','replay_before_capture','reversed_replay'])
def test_calculation_clock_order_and_original_acquisition_are_bound(control, kind):
    original, replayed, reader = control
    if kind == 'fetch_wrong_original_end': replayed['fetch_time'] = '2026-10-22 15:00:00'
    elif kind == 'original_outside_capture': original['signal_generated_at'] = (reader.closed_at + timedelta(hours=1)).isoformat()
    elif kind == 'replay_before_capture': replayed['signal_calculation_started_at'] = '2026-10-22T14:00:00+08:00'
    else: replayed['signal_generated_at'] = replayed['fetch_time'].replace(' ','T') + '+08:00'
    result = contract.compare_original_signal(original, replayed, reader)
    assert not result['pass'] and result['audit_errors']


@pytest.mark.parametrize('value', ['close_confirmed','intraday_hypothetical_if_now_close','anything',None])
def test_generation_context_is_an_exact_transition_not_an_ignored_label(control, value):
    original, replayed, reader = control
    replayed['signal_generation_timing'] = value
    result = contract.compare_original_signal(original, replayed, reader)
    assert not result['pass'] and result['audit_errors']


def test_collector_repeated_cold_sessions_can_starve_later_required_roles(monkeypatch, tmp_path):
    """A virtual seven-second public response is enough to exceed IC's cohort budget.

    This deliberately confirms the current incomplete outcome; it is an
    evidence-backed liveness limitation, not an acceptance of a complete pack.
    No sleeping, real requests or production anchors are involved.
    """
    from contextlib import nullcontext
    import threading
    import pandas as pd
    import requests
    import poe_ic_im_mainline_v1_4_bot as bot
    import ic_im_evidence_adapter_v1 as adapter
    tick = [0.0]
    attempted = []
    day = date(2026, 10, 22)
    clock = datetime(2026, 10, 22, 18, tzinfo=bot.BEIJING)
    monkeypatch.setattr(bot.time_module, 'monotonic', lambda: tick[0])
    monkeypatch.setattr(evidence, '_now', lambda: clock + timedelta(seconds=tick[0]))
    class Wall(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock.astimezone(tz) if tz else clock.replace(tzinfo=None)
    monkeypatch.setattr(adapter, 'datetime', Wall)
    def fetch(role, key, value):
        def provider():
            attempted.append((role, key))
            allowed = bot._bounded_timeout(8)
            if allowed < 7:
                tick[0] += allowed
                raise requests.Timeout('virtual successful provider needs seven seconds')
            tick[0] += 7
            return value
        return evidence.current_session.get().call(role, key, provider)
    months = ['202610', '202611', '202612', '202703']
    frame = pd.DataFrame([{'synthetic_budget_probe': 1}])
    frame.attrs['source'] = 'synthetic_budget_probe'
    strategy = SimpleNamespace(
        BEIJING=bot.BEIJING, BUILD_ID=bot.BUILD_ID, SIGNAL_PRODUCT_NETWORK_BUDGET_SECONDS=45,
        STRATEGY_QUERY_LOCK=threading.RLock(), runtime_clock=lambda *a: nullcontext(),
        _network_budget=bot._network_budget, _require_official_exchange_calendar=lambda *a: None,
        _is_exchange_trading_day=lambda *a: True, _market_phase=lambda *a: '收盘后',
        LIVE_CONTINUATION_ANCHOR={product: {'last_verified_day': day} for product in ['IC','IM']},
        fetch_sse_510500_expiries=lambda: fetch('ic.months', {}, months),
        fetch_sse_510500_quote=lambda: fetch('ic.etf', {}, {'synthetic_budget_probe': 1}),
        _fetch_510500_chain_for_day=lambda month, day: fetch('ic.chain', {'month':month,'day':day}, frame),
        fetch_cffex_quotes=lambda product, clock: fetch('cffex.quotes', {'product':product}, frame),
        live_proxy=lambda product, clock: fetch('market.live', {'product':product}, {'synthetic_budget_probe': 1}),
        fear_grid=SimpleNamespace(fetch_score=lambda day, mode: fetch('fear.score', {'day':day,'mode':mode}, {'fear_greed_index':30})),
        _gov10y_for_day=lambda product, day: fetch('rate.resolve', {'product':product}, .02))
    strategy.fetch_510500_expiries_with_failover = strategy.fetch_sse_510500_expiries
    strategy.fetch_510500_quote_with_failover = strategy.fetch_sse_510500_quote
    for iteration in range(2):
        result = adapter.capture_daily(strategy, tmp_path / str(iteration), clock)
        assert result['products']['IC']['status'] == 'incomplete'
        assert 'market.live' in result['products']['IC']['reason']
        assert result['products']['IM']['status'] == 'captured'
    # A fresh session refetches the four chains rather than carrying the
    # already-observed values forward. The same budget exhaustion repeats.
    assert len([role for role, _ in attempted if role == 'ic.chain']) == 8
