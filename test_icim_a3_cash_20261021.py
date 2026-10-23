"""Independent cash/plan audit; examples are synthetic costs on real SSE terms.

All outputs are preview-only. Failures here do not establish production position
contamination, and no StateStore is opened by this module.
"""
from copy import deepcopy
from dataclasses import replace
from datetime import date
from decimal import Decimal
import hashlib
import json
from pathlib import Path

import pytest
import ic_im_adjusted_put_migration_v1 as migration
import ic_im_put_execution_v1 as execution

ROOT = Path(__file__).resolve().parent
FIXTURE = ROOT / 'tests/fixtures/icim_external_review_round2_20261021'
SID = '10011470'
NEW_M_ID = '10012099'
DAY = date(2026, 10, 21)


def payload(name):
    return json.loads((FIXTURE / name).read_text(encoding='utf-8'))


def pair():
    return migration.adjustment_terms(payload('sse_adjustment_20260715.json'),
                                     effective_date='2026-07-15')[SID]


def basis(entry=None, current=None, **kwargs):
    old, new = pair()
    values = dict(premium='0.4', contracts=5, entry_day='2026-07-14',
                  as_of=DAY, cost_evidence_hash='a'*64)
    values.update(kwargs)
    return migration.cash_basis_preview(entry or old, current or new, **values)


def plan(**changes):
    old, _ = pair()
    value = dict(signal_day='2026-10-20', execution_day=DAY.isoformat(),
                 required_quotes=[dict(kind='option', contract=old.contract,
                                       security_id=SID)])
    value.update(changes)
    return value


def test_all_114_real_events_have_separate_terms_and_expiry_intervals():
    values = migration.adjustment_terms(payload('sse_adjustment_20260715.json'),
                                       effective_date='2026-07-15')
    assert len(values) == 114
    for security, (old, new) in values.items():
        assert old.security_id == new.security_id == security
        assert old.contract[:11]+old.contract[12:] == new.contract[:11]+new.contract[12:]
        assert old.unit == 10000 and new.unit == 10180
        assert old.expiry == new.expiry
        assert old.valid_through == date(2026,7,14) and new.valid_from == date(2026,7,15)
        assert old.availability_day is new.availability_day is None


@pytest.mark.parametrize('premium,multiple,comparison,expected', [
    ('1.1787',3,'at_least',False),('1.1788',3,'at_least',True),
    ('0.1964','.5','at_most',True),('0.1965','.5','at_most',False),
    ('0.2750','.7','at_most',True),('0.2751','.7','at_most',False)])
def test_cash_threshold_uses_actual_contract_cash_not_old_unit_price(premium,multiple,comparison,expected):
    _, current = pair()
    assert migration.cash_threshold_hit(basis(),current,premium=premium,
                                        multiple=multiple,comparison=comparison) is expected


def test_cost_preview_is_read_only_and_cash_preserving():
    old, current = pair()
    before = (old.json(),current.json())
    result = basis(old,current)
    assert (old.json(),current.json()) == before
    assert Decimal(result['cash_per_contract']) == 4000
    assert Decimal(result['total_entry_cash']) == 20000
    assert result['engine_supported'] is result['ledger_write_authorized'] is False
    with pytest.raises(RuntimeError):
        execution.require_supported_ic_contracts('IC',{'v14_core_put_contract':current.contract,
                                                       'cash_basis_preview':result})


def test_already_adjusted_cost_does_not_receive_second_rebase():
    _, current = pair()
    result = basis(current,current,entry_day='2026-07-16')
    assert Decimal(result['cash_per_contract']) == Decimal('4072')
    assert Decimal(result['equivalent_current_unit_premium']) == Decimal('.4')


def test_same_named_new_m_security_cannot_borrow_old_security_cost():
    master = migration.master_terms(payload('sse_contract_master.json'),as_of='2026-10-20')
    old,_ = pair()
    with pytest.raises(RuntimeError):
        basis(old,master[NEW_M_ID],as_of='2026-10-20')


@pytest.mark.parametrize('changed', [
    {'contract':'510500P2612A07750','strike':Decimal('7.613')},
    {'expiry':date(2026,12,24)},
])
def test_same_id_cannot_migrate_to_different_original_strike_series_or_expiry(changed):
    old,current = pair()
    wrong = replace(current,**changed)
    with pytest.raises(RuntimeError):
        basis(old,wrong)


def test_adjusted_entry_cannot_revert_to_old_standard_m_identity():
    old,current = pair()
    fake_future_m = replace(old,valid_from=date(2026,7,16),valid_through=current.valid_through)
    with pytest.raises(RuntimeError):
        basis(current,fake_future_m,entry_day='2026-07-16')


@pytest.mark.parametrize('changed', [{'unit':10181},{'strike':Decimal('7.368')}])
def test_a_to_a_terms_change_requires_a_new_proven_adjustment_event(changed):
    _,current = pair()
    with pytest.raises(RuntimeError):
        basis(current,replace(current,**changed),entry_day='2026-07-16')


@pytest.mark.parametrize('field,value', [
    ('cash_per_contract','999999'),('total_entry_cash','1'),('contracts',6),
    ('original_premium','0.5'),('original_unit',10001),
    ('equivalent_current_unit_premium','0.5'),('cost_evidence_hash',None),
    ('entry_terms_hash','invalid'),
])
def test_threshold_rejects_a_self_inconsistent_mutated_preview(field,value):
    _,current = pair()
    value_before = basis()
    value_before[field] = value
    with pytest.raises(RuntimeError):
        migration.cash_threshold_hit(value_before,current,premium='1.19',multiple=3,
                                     comparison='at_least')


@pytest.mark.parametrize('kind', ['option','future'])
def test_plan_preview_never_shares_nested_mutable_input(kind):
    _,current = pair()
    row = dict(kind=kind,contract=current.contract if kind=='option' else 'IC2612',
               security_id=SID if kind=='option' else None,extra={'stamp':['original']})
    value = plan(required_quotes=[row])
    result = migration.preview_plan_identities(value,{SID:current},execution_day=DAY)
    result['quote_bindings'][0]['original']['extra']['stamp'][0] = 'mutated'
    assert value['required_quotes'][0]['extra']['stamp'] == ['original']


def test_plan_terms_mapping_key_cannot_hide_different_real_security():
    # The real master contains newly listed M07500/10012099. The original
    # security is 10011470 and has become A07500. A malformed lookup map must
    # never report the original row as identity_unchanged.
    master = migration.master_terms(payload('sse_contract_master.json'),as_of='2026-10-20')
    value = plan(execution_day='2026-10-20',signal_day='2026-10-19')
    with pytest.raises(RuntimeError):
        migration.preview_plan_identities(value,{SID:master[NEW_M_ID]},execution_day='2026-10-20')


def test_valid_frozen_plan_retains_identity_and_dates_but_does_not_authorize_engine():
    _,current = pair()
    value = plan()
    before = deepcopy(value)
    result = migration.preview_plan_identities(value,{SID:current},execution_day=DAY)
    assert value == before == result['original_plan']
    assert result['quote_bindings'][0]['status'] == 'requires_explicit_plan_migration'
    assert result['engine_supported'] is result['ledger_write_authorized'] is False


@pytest.mark.parametrize('bad', [False,True,'NaN','Infinity','-1','0','1.5'])
def test_nonphysical_contract_count_is_rejected(bad):
    with pytest.raises(RuntimeError):
        basis(contracts=bad)


@pytest.mark.parametrize('bad', ['2026-07-15','2026-10-22','2026-02-30','２０２６-０７-１４'])
def test_entry_date_must_lie_in_original_proven_terms(bad):
    with pytest.raises(RuntimeError):
        basis(entry_day=bad)
