"""Historical identity binding must survive failures and reject same-name swaps."""
import importlib.util
import threading
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
import numpy as np
import json

ROOT = Path(__file__).resolve().parent
PATH = ROOT / 'quant_research_runs/20261022_historical_calendar_repair_v1/history/rebuild_history.py'
SPEC = importlib.util.spec_from_file_location('historical_repair_adapter_tests', PATH)
ADAPTER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ADAPTER)
CONTRACT = '510500P2303M06000'


def setup_formal(resize=None):
    original = {'post_put_contract': '510500P2612M07500', 'post_put_security_id': '10012099',
                'untouched_plan': {'day': '2026-10-22'}}
    formal = SimpleNamespace(STRATEGY_QUERY_LOCK=threading.RLock(),
        LIVE_CONTINUATION_ANCHOR={'IC': original},
        _ic_put_selected_security_id=lambda row: str(row.security_id),
        _size_existing_ic_put=resize or (lambda *a, **k: {'qty': 3}))
    return formal, original, ADAPTER.HistoricalICBinding(formal)


def selected(binding, security='10000001'):
    return binding.selected_security(pd.Series({'contract': CONTRACT, 'security_id': security}))


def quote(security='10000001', contract=CONTRACT):
    return pd.Series({'security_id': security, 'last': 0.1}, name=contract)


def test_rejects_security_id_taken_only_from_current_resize_quote():
    formal, original, binding = setup_formal()
    with pytest.raises(RuntimeError, match='not established'):
        binding.resize(CONTRACT, quote())
    assert formal.LIVE_CONTINUATION_ANCHOR['IC'] is original


def test_relisted_same_m_name_cannot_replace_entry_security():
    formal, original, binding = setup_formal()
    selected(binding)
    with pytest.raises(RuntimeError, match='identity changed'):
        binding.resize(CONTRACT, quote('10000002'))
    assert formal.LIVE_CONTINUATION_ANCHOR['IC'] is original


def test_quote_index_keeps_actual_contract_identity():
    formal, original, binding = setup_formal()
    selected(binding)
    assert binding.resize(CONTRACT, quote()) == {'qty': 3}
    assert formal.LIVE_CONTINUATION_ANCHOR['IC'] is original


def test_same_security_with_wrong_contract_is_rejected():
    formal, original, binding = setup_formal()
    selected(binding)
    with pytest.raises(RuntimeError, match='identity changed'):
        binding.resize(CONTRACT, quote(contract='510500P2306M06000'))
    assert formal.LIVE_CONTINUATION_ANCHOR['IC'] is original


def test_original_guard_reads_entry_bound_id_and_then_anchor_is_restored():
    formal, original, binding = setup_formal()
    def guard(contract, row):
        anchor = formal.LIVE_CONTINUATION_ANCHOR['IC']
        assert anchor['post_put_security_id'] == '10000001'
        assert anchor['post_put_contract'] == CONTRACT
        return {'qty': 7}
    binding.original_resize = guard
    selected(binding)
    assert binding.resize(CONTRACT, quote()) == {'qty': 7}
    assert formal.LIVE_CONTINUATION_ANCHOR['IC'] is original


def test_failure_restores_anchor_and_deeply_isolates_nested_state():
    formal, original, binding = setup_formal()
    def fail(*a, **k):
        formal.LIVE_CONTINUATION_ANCHOR['IC']['untouched_plan']['day'] = 'corrupted'
        raise ValueError('valuation failed')
    binding.original_resize = fail
    selected(binding)
    with pytest.raises(ValueError, match='valuation failed'):
        binding.resize(CONTRACT, quote())
    assert formal.LIVE_CONTINUATION_ANCHOR['IC'] is original
    assert original['untouched_plan']['day'] == '2026-10-22'


def test_explicit_new_selection_can_bind_new_leg_but_old_quote_cannot_follow():
    formal, original, binding = setup_formal()
    selected(binding)
    selected(binding, '10000002')
    with pytest.raises(RuntimeError, match='identity changed'):
        binding.resize(CONTRACT, quote('10000001'))
    assert binding.resize(CONTRACT, quote('10000002')) == {'qty': 3}
    assert formal.LIVE_CONTINUATION_ANCHOR['IC'] is original


def test_snapshot_reader_waits_for_original_anchor():
    entered, release, read_done = threading.Event(), threading.Event(), threading.Event()
    formal, original, binding = setup_formal()
    observed = []
    def resize(*a, **k):
        entered.set()
        assert release.wait(5)
        return {'qty': 3}
    def reader():
        with formal.STRATEGY_QUERY_LOCK:
            observed.append(formal.LIVE_CONTINUATION_ANCHOR['IC'])
        read_done.set()
    binding.original_resize = resize
    selected(binding)
    a = threading.Thread(target=lambda: binding.resize(CONTRACT, quote()))
    b = threading.Thread(target=reader)
    a.start()
    try:
        assert entered.wait(5)
        b.start()
        assert not read_done.wait(0.05)
    finally:
        release.set()
        a.join(5)
        if b.ident is not None:
            b.join(5)
    assert not a.is_alive() and not b.is_alive()
    assert observed == [original]
    assert formal.LIVE_CONTINUATION_ANCHOR['IC'] is original


def test_rebuilt_current_ic_admission_uses_mom120_for_every_historical_day():
    import poe_ic_im_mainline_v1_4_bot as bot
    folder = ROOT / 'quant_research_runs/20261022_historical_calendar_repair_v1/rebuilt_02/inputs'
    risk = pd.read_csv(folder / 'native_fix4_ic_risk_signals_v1.csv.gz').set_index('signal_date')
    candidates = pd.read_csv(folder / 'native_fix4_seller_candidates_v1.csv.gz')
    candidates = candidates.loc[candidates['product'].eq('IC')]
    assert len(candidates) == 944
    threshold = bot.v14_policy.PRODUCT_RULES['IC']['iv_threshold']
    expected = (candidates.candidate_tradable.astype(bool)
                & candidates.candidate_iv.gt(threshold)
                & candidates.signal_date.map(risk.valuation_tier).isin([0, 1])
                & candidates.signal_date.map(risk.momentum_120).ge(0))
    assert candidates.seller_permission.astype(bool).tolist() == expected.tolist()


def test_rebuilt_positive_put_quantities_always_have_actual_contract_targets():
    folder = ROOT / 'quant_research_runs/20261022_historical_calendar_repair_v1/rebuilt_02/inputs'
    for product, version, count in [('ic', 'v2', 945), ('im', 'v3', 986)]:
        targets = pd.read_csv(folder / f'native_fix4_{product}_noseller_targets_{version}.csv.gz')
        assert len(targets) == count
        assert targets.signal_date.is_unique
        assert targets.loc[targets.core_put_qty_target.gt(0), 'core_put_contract_target'].notna().all()
        if product == 'ic':
            assert (targets.core_put_qty_target + targets.momentum_put_qty_target == targets.total_put_qty_target).all()
        else:
            assert targets.loc[targets.momentum_put_qty_target.gt(0), 'momentum_put_contract_target'].notna().all()
            assert targets.call_qty_normalized_target.eq(0).all()


def test_rebuilt_receipt_does_not_hide_waiting_evidence_or_mutate_anchors():
    path = ROOT / 'quant_research_runs/20261022_historical_calendar_repair_v1/rebuilt_02/receipt.json'
    result = json.loads(path.read_text(encoding='utf-8'))
    assert result['status'] == 'PASS'
    assert result['anchors_unchanged'] is True
    assert result['network_attempts'] == []
    assert result['counterfactual_effective_dates'] == {'IC_momentum': '1900-01-01', 'IC_seller_MOM120': '1900-01-01'}
    for product in ['IC', 'IM']:
        assert result['phases'][product + '_targets']['fail_closed_events'] == []
