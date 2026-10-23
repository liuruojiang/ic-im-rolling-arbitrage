"""Dated identity and cash preservation tests; never open production state."""
from copy import deepcopy
from dataclasses import replace
from datetime import date
from decimal import Decimal
import json
from pathlib import Path

import pytest

import ic_im_adjusted_put_migration_v1 as migration
import ic_im_put_execution_v1 as execution

FIXTURE = Path(__file__).parent / "tests/fixtures/icim_external_review_round2_20261021"
DAY = date(2026, 10, 21)
ADJUST = date(2026, 7, 15)
OLD, NEW, SID = "510500P2612M07500", "510500P2612A07500", "10011470"


def raw(name):
    return json.loads((FIXTURE / name).read_text(encoding="utf-8"))


def terms():
    return migration.adjustment_terms(raw("sse_adjustment_20260715.json"), effective_date=ADJUST)[SID]


def basis(entry=None, current=None, **changes):
    before, after = terms()
    values = dict(premium="0.4", contracts=5, entry_day="2026-07-14", as_of=DAY,
                  cost_evidence_hash="a"*64)
    values.update(changes)
    return migration.cash_basis_preview(entry or before, current or after, **values)


def test_real_adjustment_has_exact_before_after_boundaries():
    before, after = terms()
    assert (before.contract, after.contract) == (OLD, NEW)
    assert (before.unit, after.unit) == (10000, 10180)
    assert (before.strike, after.strike) == (Decimal("7.500"), Decimal("7.367"))
    assert before.valid_through == date(2026, 7, 14)
    assert after.valid_from == ADJUST
    before.on("2026-07-14")
    after.on(ADJUST)
    with pytest.raises(RuntimeError):
        before.on(ADJUST)
    with pytest.raises(RuntimeError):
        after.on("2026-07-14")


def test_all_real_14_adjusted_puts_agree_with_master_and_quote():
    adjusted = migration.adjustment_terms(raw("sse_adjustment_20260715.json"), effective_date=ADJUST)
    master = migration.master_terms(raw("sse_contract_master.json"), as_of="2026-10-20")
    quote = raw("sse_december_identity.json")
    count = 0
    for sid, (_, value) in adjusted.items():
        if value.contract.startswith("510500P2612"):
            bound = migration.bind_daily_quote(value, quote, as_of=DAY)
            official = master[sid]
            assert (bound.contract, bound.unit, bound.strike) == (official.contract, official.unit, official.strike)
            assert bound.availability_day == DAY
            bound.require_available(DAY)
            count += 1
    assert count == 14


def test_same_named_new_m_has_different_security_and_cannot_take_old_cost():
    before, _ = terms()
    master = migration.master_terms(raw("sse_contract_master.json"), as_of="2026-10-20")
    new_m = master["10012099"]
    assert new_m.contract == OLD and new_m.security_id != SID
    with pytest.raises(RuntimeError, match="identity/date/evidence"):
        basis(entry=before, current=new_m, as_of="2026-10-20")


@pytest.mark.parametrize("wrong", ["2026-10-19", "2026-10-21", "2026-07-14"])
def test_master_uses_timesave_not_requested_date(wrong):
    payload = raw("sse_contract_master.json")
    payload["queryDate"] = wrong.replace("-", "")
    with pytest.raises(RuntimeError, match="date/identity/listing"):
        migration.master_terms(payload, as_of=wrong)


def test_a_master_start_date_cannot_be_mistaken_for_adjustment_date():
    value = migration.master_terms(raw("sse_contract_master.json"), as_of="2026-10-20")[SID]
    assert value.valid_from == date(2026, 10, 20)
    with pytest.raises(RuntimeError):
        value.on("2026-07-14")
    with pytest.raises(RuntimeError):
        value.on("2026-10-21")


@pytest.mark.parametrize("field,value", [("isPagination", 0), ("isPagination", 0.0),
    ("actionErrors", ["failed"]), ("fieldErrors", {"queryDate": "bad"}),
    ("sqlId", "wrong"), ("result", [])])
def test_master_rejects_bad_response_scope(field, value):
    payload = raw("sse_contract_master.json")
    payload[field] = value
    with pytest.raises(RuntimeError):
        migration.master_terms(payload, as_of="2026-10-20")


@pytest.mark.parametrize("field,value", [("CONTRACT_UNIT", True), ("CONTRACT_UNIT", "0"),
    ("CONTRACT_UNIT", "10180.5"), ("CONTRACT_UNIT", "NaN"),
    ("SECURITY_ID", "１００１１４７０"), ("TIMESAVE", "20261021"),
    ("DELISTFLAG", "是"), ("EXERCISE_PRICE", "Infinity"),
    ("EXERCISE_DATE", "20261224"), ("END_DATE", "20261224"),
    ("START_DATE", "20261021"), ("CALL_OR_PUT", "认沽")])
def test_master_rejects_bad_rows(field, value):
    payload = raw("sse_contract_master.json")
    payload["result"][0][field] = value
    with pytest.raises(RuntimeError):
        migration.master_terms(payload, as_of="2026-10-20")


def test_master_rejects_duplicate_identity():
    payload = raw("sse_contract_master.json")
    payload["result"].append(deepcopy(payload["result"][0]))
    with pytest.raises(RuntimeError, match="duplicate"):
        migration.master_terms(payload, as_of="2026-10-20")


def test_economic_terms_do_not_prove_daily_availability():
    _, value = terms()
    with pytest.raises(RuntimeError, match="availability"):
        value.require_available(DAY)
    bound = migration.bind_daily_quote(value, raw("sse_december_identity.json"), as_of=DAY)
    with pytest.raises(RuntimeError, match="availability"):
        bound.require_available("2026-10-20")


@pytest.mark.parametrize("kind", ["date", "code", "strike", "security", "missing"])
def test_daily_quote_must_match_terms(kind):
    _, value = terms()
    quote = raw("sse_december_identity.json")
    row = next(row for row in quote["list"] if row[5] == SID)
    if kind == "date": quote["date"] = "20261020"
    if kind == "code": row[0] = OLD
    if kind == "strike": row[4] = 7.5
    if kind == "security": row[5] = "10099999"
    if kind == "missing": quote["list"].remove(row)
    with pytest.raises(RuntimeError):
        migration.bind_daily_quote(value, quote, as_of=DAY)


def test_cash_preservation_and_nonrounded_rebased_cost():
    result = basis()
    assert result["contracts"] == 5
    assert Decimal(result["cash_per_contract"]) == 4000
    assert Decimal(result["total_entry_cash"]) == 20000
    assert Decimal(result["equivalent_current_unit_premium"]) != Decimal("0.3929")
    assert result["engine_supported"] is result["ledger_write_authorized"] is False


def test_three_times_must_compare_cash_after_unit_change():
    _, current = terms()
    assert Decimal("1.19") < Decimal("0.4")*3  # incorrect old unit-price comparison
    assert migration.cash_threshold_hit(basis(), current, premium="1.19", multiple=3, comparison="at_least")


def test_short_decay_must_not_roll_on_spurious_unit_price_reduction():
    _, current = terms()
    assert Decimal("0.198") <= Decimal("0.4")/2  # incorrect old unit-price comparison
    assert not migration.cash_threshold_hit(basis(), current, premium="0.198", multiple="0.5", comparison="at_most")


def test_already_adjusted_entry_must_not_be_rebased_again():
    _, current = terms()
    result = basis(entry=current, current=current, entry_day="2026-07-16")
    assert Decimal(result["cash_per_contract"]) == Decimal("4072")
    assert Decimal(result["equivalent_current_unit_premium"]) == Decimal("0.4")
    assert result == basis(entry=current, current=current, entry_day="2026-07-16")


@pytest.mark.parametrize("key,value", [("premium", None), ("premium", "NaN"),
    ("premium", "Infinity"), ("premium", True), ("premium", 0),
    ("contracts", True), ("contracts", 1.5), ("contracts", 0),
    ("entry_day", "2026-07-15"), ("entry_day", "2026-10-22"),
    ("cost_evidence_hash", ""), ("cost_evidence_hash", "a"*63)])
def test_unproved_cost_cannot_enable_thresholds(key, value):
    with pytest.raises(RuntimeError):
        basis(**{key: value})


@pytest.mark.parametrize("value", [True, "２０２６-１０-２１", "2026-10-21T15:00:00", "2026-02-30", None])
def test_dates_are_exact_ascii(value):
    with pytest.raises(RuntimeError):
        migration.day(value)


def test_frozen_plan_is_only_previewed_never_rewritten_or_postponed():
    _, current = terms()
    plan = dict(execution_day=DAY.isoformat(), signal_day="2026-10-20", required_quotes=[
        dict(contract=OLD, security_id=SID, kind="option"),
        dict(contract="IC2612", security_id=None, kind="future")])
    original = deepcopy(plan)
    result = migration.preview_plan_identities(plan, {SID: current}, execution_day=DAY)
    assert result["quote_bindings"][0]["status"] == "requires_explicit_plan_migration"
    assert result["original_plan"] == plan == original
    assert not result["engine_supported"] and not result["ledger_write_authorized"]
    result["original_plan"]["required_quotes"][0]["contract"] = "changed"
    assert plan == original
    with pytest.raises(RuntimeError, match="execution date"):
        migration.preview_plan_identities(plan, {SID: current}, execution_day="2026-10-22")


def test_production_a_gate_remains_closed_even_with_preview_attached():
    with pytest.raises(RuntimeError, match="adjusted contract master"):
        execution.require_supported_ic_contracts("IC", dict(v14_core_put_contract=NEW,
                                                           migration_preview=basis()))
