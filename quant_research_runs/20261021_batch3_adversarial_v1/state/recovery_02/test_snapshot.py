"""Independent prepared-signal/persistent-boundary adversaries; disposable stores only."""
from copy import deepcopy
from datetime import date, datetime
import pytest

import poe_ic_im_mainline_v1_4_bot as bot
import poe_ic_im_v1_4_state as state
import test_ic_im_ordinary_put_route_fix_20261013 as ledger
import test_icim_round2_audit_lifecycle_20261021 as prior

DAY = date(2026, 10, 21)


def prepare(tmp_path, monkeypatch, extra=None, *, momentum=False):
    for module in [state, bot, bot.fear_grid]:
        monkeypatch.setattr(module, 'datetime', prior.SyntheticCloseClock)
    store, record = prior.recent_store(tmp_path/'isolated_ledger')
    anchors = state.anchors_from_record(record)
    signals = {p: prior.close_evidence(ledger._signal(
        p, anchors[p], DAY, momentum=(momentum and p=='IM'),
        extra=extra if p=='IM' else None)) for p in ('IC', 'IM')}
    for signal in signals.values():
        signal['v14_opening_events'] = []
    return store, record, signals


def test_valid_prepared_same_day_pair_persists_and_reopens(tmp_path, monkeypatch):
    store, record, signals = prepare(tmp_path, monkeypatch)
    result = store.append_confirmed_signals(record, signals)
    assert result['sequence'] == 1
    assert state.StateStore(store.root).load_latest()['digest'] == result['digest']


@pytest.mark.parametrize('contract', ['MO2601-P-7200', 'MO2609-P-7200'])
def test_expired_adopted_long_put_cannot_be_committed_as_next_open_plan(tmp_path, monkeypatch, contract):
    store, record, signals = prepare(tmp_path, monkeypatch, dict(
        core_put_target_contract=contract, put_target_contract=contract))
    assert signals['IM']['v14_ordinary_put_pending']['legs']['core']['new_contract'] == contract
    before = prior.snapshot(store.root)
    with pytest.raises(RuntimeError, match='expir|到期|expired'):
        store.append_confirmed_signals(record, signals)
    assert prior.snapshot(store.root) == before


def test_unknown_year_adopted_long_put_remains_rejected(tmp_path, monkeypatch):
    store, record, signals = prepare(tmp_path, monkeypatch, dict(
        core_put_target_contract='MO2703-P-7200', put_target_contract='MO2703-P-7200'))
    before = prior.snapshot(store.root)
    with pytest.raises(RuntimeError, match='2027'):
        store.append_confirmed_signals(record, signals)
    assert prior.snapshot(store.root) == before


@pytest.mark.parametrize('field', ['next_trade_date', 'market_date'])
def test_in_year_closed_signal_or_next_session_does_not_append(tmp_path, monkeypatch, field):
    store, record, signals = prepare(tmp_path, monkeypatch)
    for signal in signals.values():
        signal[field] = date(2026, 10, 24)  # Saturday, year alone is insufficient.
    before = prior.snapshot(store.root)
    with pytest.raises(RuntimeError):
        store.append_confirmed_signals(record, signals)
    assert prior.snapshot(store.root) == before


@pytest.mark.parametrize('field', ['option_monthly_reset_execution_date', 'put_monthly_reset_execution_date'])
def test_unused_unknown_year_maintenance_diagnostic_does_not_block(tmp_path, monkeypatch, field):
    store, record, signals = prepare(tmp_path, monkeypatch)
    signals['IM'][field] = date(2027, 3, 19)
    assert signals['IM']['option_monthly_reset_due'] is False
    result = store.append_confirmed_signals(record, signals)
    assert result['sequence'] == 1


def test_prepared_short_expiry_month_identity_mismatch_rejected(monkeypatch):
    # Full new-signal validator must enforce the code/expiry identity, even
    # when both dates lie within the one covered official year.
    anchor = ledger._record()['products']['IM']
    ledger._route(anchor, 'IM', 'short_put')
    anchor['v14_short_put_expiry'] = '2026-12-18'
    signal = ledger._signal('IM', anchor, DAY)
    with pytest.raises(RuntimeError, match='到期日|月份|identity'):
        state.validate_v14_new_signal(signal, 'IM')


def test_recovered_journal_products_must_match_its_revalidated_signals(tmp_path, monkeypatch):
    store, record, signals = prepare(tmp_path, monkeypatch)
    # Model an obsolete producer's complete, hash-valid journal plus a crash
    # before the head replacement. This is a consistency check, not a claim
    # that the current producer or an unsigned partial write creates it.
    written = store.append_confirmed_signals(record, signals)
    obsolete = deepcopy(written)
    obsolete['products']['IM']['post_core_contract'] = 'IM2611'
    obsolete['digest'] = state._digest(obsolete)
    state._validate_record(obsolete)
    store._atomic_write(store.journal_dir/f"000001-{DAY}.json", obsolete)
    store._atomic_write(store.latest_path, record)
    before = prior.snapshot(store.root)
    with pytest.raises(RuntimeError, match='一致|products|anchor|锚点|恢复'):
        store.append_confirmed_signals(record, signals)
    assert prior.snapshot(store.root) == before


def test_valid_complete_journal_recovery_keeps_exact_original_bytes(tmp_path, monkeypatch):
    store, record, signals = prepare(tmp_path, monkeypatch)
    written = store.append_confirmed_signals(record, signals)
    journal = store.journal_dir/f"000001-{DAY}.json"
    frozen = journal.read_bytes()
    store._atomic_write(store.latest_path, record)
    recovered = store.append_confirmed_signals(record, signals)
    assert recovered['digest'] == written['digest']
    assert journal.read_bytes() == frozen
    assert state.StateStore(store.root).load_latest()['digest'] == written['digest']
