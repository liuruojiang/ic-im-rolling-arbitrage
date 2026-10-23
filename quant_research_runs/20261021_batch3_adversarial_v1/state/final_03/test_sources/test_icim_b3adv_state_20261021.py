"""Independent prepared-signal/persistent-boundary adversaries; disposable stores only."""
from copy import deepcopy
from datetime import date, datetime, timedelta
import pytest

import poe_ic_im_mainline_v1_4_bot as bot
import poe_ic_im_v1_4_state as state
import test_ic_im_ordinary_put_route_fix_20261013 as ledger
import test_icim_round2_audit_lifecycle_20261021 as prior

DAY = date(2026, 10, 21)


def prepare(tmp_path, monkeypatch, extra=None, *, momentum=False, anchor_update=None,
            day=DAY, product='IM'):
    clock = datetime.combine(day, datetime.min.time(), tzinfo=bot.BEIJING).replace(hour=15, minute=40)
    class FrozenCloseClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock.astimezone(tz) if tz else clock.replace(tzinfo=None)
    for module in [state, bot, bot.fear_grid]:
        monkeypatch.setattr(module, 'datetime', FrozenCloseClock)
    previous = bot._roll_backward_exchange_day(day-timedelta(days=1))
    record = ledger._record()
    record.update(verified_day=previous.isoformat(), signals={})
    for anchor in record['products'].values():
        anchor.update(last_verified_day=previous.isoformat(), verified_grid_entry_source='none',
                      verified_next_grid_entry_source='none')
    if anchor_update:
        record['products'][product].update(state._jsonable(anchor_update))
    record['digest'] = state._digest(record)
    state._validate_record(record)
    store = state.StateStore(tmp_path/'isolated_ledger')
    store._atomic_write(store.journal_dir/f'000000-{previous}.json', record)
    store._atomic_write(store.latest_path, record)
    record = store.load_latest()
    anchors = state.anchors_from_record(record)
    signals = {p: prior.close_evidence(ledger._signal(
        p, anchors[p], day, momentum=(momentum and p==product),
        extra=extra if p==product else None)) for p in ('IC', 'IM')}
    for signal in signals.values():
        signal['v14_opening_events'] = []
        signal.update(fear_data_date=day, fear_page_updated_at=day.isoformat()+'T15:01:00+08:00',
                      fear_retrieved_at=day.isoformat()+'T15:30:00+08:00', signal_generated_at=clock.isoformat())
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


@pytest.mark.parametrize('field,value', [('post_core_contract','IM2611'),
                                       ('verified_core_put_qty_normalized',.5),
                                       ('verified_next_momentum_weight',1.)])
def test_recovered_journal_products_must_match_its_revalidated_signals(tmp_path, monkeypatch, field, value):
    store, record, signals = prepare(tmp_path, monkeypatch)
    # Model an obsolete producer's complete, hash-valid journal plus a crash
    # before the head replacement. This is a consistency check, not a claim
    # that the current producer or an unsigned partial write creates it.
    written = store.append_confirmed_signals(record, signals)
    obsolete = deepcopy(written)
    obsolete['products']['IM'][field] = value
    if field == 'verified_core_put_qty_normalized':
        # Preserve all existing within-record quantity invariants; the attack
        # is disagreement with the journal's signals, not a malformed total.
        obsolete['products']['IM'].update(verified_total_put_qty_normalized=.5,
            verified_put_qty_normalized=.5, verified_parent_puts=1,
            post_core_put_equivalent_units=.25, post_put_equivalent_units=.25)
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


def test_recovered_journal_sequence_must_match_the_append_path(tmp_path, monkeypatch):
    store, record, signals = prepare(tmp_path, monkeypatch)
    written = store.append_confirmed_signals(record, signals)
    obsolete = deepcopy(written)
    obsolete['sequence'] = 2
    obsolete['digest'] = state._digest(obsolete)
    state._validate_record(obsolete)
    store._atomic_write(store.journal_dir/f"000001-{DAY}.json", obsolete)
    store._atomic_write(store.latest_path, record)
    before = prior.snapshot(store.root)
    with pytest.raises(RuntimeError, match='序号|sequence|恢复|冲突'):
        store.append_confirmed_signals(record, signals)
    assert prior.snapshot(store.root) == before


def test_nonexpired_changed_core_plan_preserves_actual_contract(tmp_path, monkeypatch):
    target = 'MO2612-P-7000'
    store, record, signals = prepare(tmp_path, monkeypatch, dict(
        core_put_target_contract=target, put_target_contract=target))
    result = store.append_confirmed_signals(record, signals)
    assert result['products']['IM']['v14_ordinary_put_pending']['legs']['core']['new_contract'] == target


def test_closing_only_old_expired_long_leg_does_not_require_future_expiry(tmp_path, monkeypatch):
    old = 'MO2609-P-7200'
    store, record, signals = prepare(tmp_path, monkeypatch, dict(
        core_put_target_contract=None, put_target_contract=None, core_put_target_qty_normalized=0.,
        total_put_target_qty_normalized=0., v13_parent_puts_per_full_core=0),
        anchor_update=dict(post_core_put_contract=old, post_put_contract=old))
    leg = signals['IM']['v14_ordinary_put_pending']['legs']['core']
    assert leg['old_contract'] == old and leg['old_qty'] > 0 and leg['new_qty'] == 0
    result = store.append_confirmed_signals(record, signals)
    assert result['products']['IM']['v14_ordinary_put_pending']['legs']['core']['new_contract'] is None


def test_monthly_same_day_reset_accepts_nonexpired_selected_put(tmp_path, monkeypatch):
    day = date(2026, 10, 16)
    target = 'MO2612-P-7000'
    store, record, signals = prepare(tmp_path, monkeypatch, dict(
        option_monthly_reset_due=True, put_monthly_reset_execution_date=day,
        option_monthly_reset_execution_date=day,
        core_put_target_contract=target, put_target_contract=target,
        core_put_action='RESIZE_OR_ROLL'), day=day)
    result = store.append_confirmed_signals(record, signals)
    assert result['products']['IM']['verified_put_monthly_reset_date'] == day.isoformat()


@pytest.mark.parametrize('contract', ['MO2601-P-8200', 'MO2609-P-8200'])
def test_expired_momentum_target_cannot_create_new_positive_open_leg(tmp_path, monkeypatch, contract):
    store, record, signals = prepare(tmp_path, monkeypatch,
        dict(momentum_put_target_contract=contract), momentum=True)
    assert signals['IM']['v14_ordinary_put_pending']['legs']['momentum']['new_qty'] > 0
    before = prior.snapshot(store.root)
    with pytest.raises(RuntimeError, match='expir|到期|expired'):
        store.append_confirmed_signals(record, signals)
    assert prior.snapshot(store.root) == before


def test_im_short_expiry_model_branches_are_not_a_new_short_open(monkeypatch):
    anchor = ledger._record()['products']['IM']
    ledger._route(anchor, 'IM', 'short_put')
    signal = ledger._signal('IM', anchor, date(2026,10,16))
    assert signal['v14_action'] == 'PUBLISH_SHORT_PUT_EXPIRY_BRANCHES'
    assert signal['v14_short_put_expiry'] == date(2026,10,16) or str(signal['v14_short_put_expiry']) == '2026-10-16'
    state.validate_v14_new_signal(signal, 'IM')


@pytest.mark.parametrize('digits', ['٢٧٠٣', '２７０３'])
def test_non_ascii_month_cannot_skip_calendar_and_enter_persistent_plan(tmp_path, monkeypatch, digits):
    contract = f'MO{digits}-P-7200'
    store, record, signals = prepare(tmp_path, monkeypatch, dict(
        core_put_target_contract=contract, put_target_contract=contract))
    assert signals['IM']['v14_ordinary_put_pending']['legs']['core']['new_contract'] == contract
    before = prior.snapshot(store.root)
    with pytest.raises(RuntimeError, match='2027|identity|contract|身份|合约'):
        store.append_confirmed_signals(record, signals)
    assert prior.snapshot(store.root) == before


def profit_pending(signals, execution, *, trigger=DAY):
    signals['IM'].update(v14_profit_pending=True, v14_profit_trigger_day=trigger,
        v14_profit_execution_day=execution, v14_profit_old_contract='MO2612-P-7200',
        v14_profit_old_security_id=None, v14_profit_old_qty=1.5,
        v14_profit_reentry_contract='MO2612-P-7000', v14_profit_reentry_security_id=None,
        v14_profit_reentry_qty=1.5, v14_action='CORE_PUT_PROFIT3X_REENTER',
        v14_last_event_id='synthetic-committed-profit-20261021')


@pytest.mark.parametrize('execution', [date(2026,10,23), date(2026,10,24)])
def test_new_profit_plan_cannot_skip_next_session_or_use_closed_day(tmp_path, monkeypatch, execution):
    store, record, signals = prepare(tmp_path, monkeypatch)
    profit_pending(signals, execution)
    before = prior.snapshot(store.root)
    with pytest.raises(RuntimeError, match='下一|交易日|执行|profit|3倍|三倍'):
        store.append_confirmed_signals(record, signals)
    assert prior.snapshot(store.root) == before


def test_new_profit_plan_on_actual_next_session_can_persist(tmp_path, monkeypatch):
    store, record, signals = prepare(tmp_path, monkeypatch)
    profit_pending(signals, date(2026,10,22))
    result = store.append_confirmed_signals(record, signals)
    assert result['products']['IM']['v14_profit_execution_day'] == '2026-10-22'


def test_prepared_new_profit_plan_cannot_backdate_uncommitted_trigger(tmp_path, monkeypatch):
    store, record, signals = prepare(tmp_path, monkeypatch)
    assert not record['products']['IM']['v14_profit_pending']
    profit_pending(signals, DAY, trigger=date(2026,10,20))
    before = prior.snapshot(store.root)
    with pytest.raises(RuntimeError, match='计划|trigger|承诺|信号日|3倍|三倍'):
        store.append_confirmed_signals(record, signals)
    assert prior.snapshot(store.root) == before
