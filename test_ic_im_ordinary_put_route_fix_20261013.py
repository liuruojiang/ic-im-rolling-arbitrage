"""Synthetic lifecycle events through the real ordinary planner and hash ledger.

The tracked accepted checkpoint supplies real schemas. Injected event conditions
and quote prices are test inputs, not historical-performance observations.
"""
from contextlib import ExitStack
from copy import deepcopy
from datetime import date, datetime, timedelta
import json
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

import ic_im_v1_4_policy as policy
import poe_ic_im_mainline_v1_4_bot as bot
import poe_ic_im_v1_4_state as state


DAY = date(2026, 9, 30)
PREVIOUS = date(2026, 9, 29)
FIXTURE = Path(__file__).parent / "tests/fixtures/icim_adversarial/confirmed.json"


def _record():
    record = json.loads(FIXTURE.read_text(encoding="utf-8"))
    record.update(schema_version=4, strategy_version="1.4", strategy_revision="r1",
                  sequence=0, previous_digest=None, verified_day=PREVIOUS.isoformat(),
                  signals={}, source="synthetic_ordinary_put_route_test")
    for product, anchor in record["products"].items():
        anchor.update(policy.default_extension(product))
        anchor.update(last_verified_day=PREVIOUS.isoformat(), post_core_contract=product + "2612",
                      verified_momentum_weight=0.0, verified_next_momentum_weight=0.0,
                      verified_grid_units=0.0, verified_next_grid_units=0.0,
                      verified_call_contracts_normalized=0.0)
    record["products"]["IC"].update(
        verified_core_put_qty=14, verified_momentum_put_qty=0,
        v14_core_put_contract="510500P2612M07500", v14_core_put_security_id="10012099",
        v14_core_put_qty=14, v14_core_put_entry_premium=.4, v14_core_put_profit3x_eligible=True)
    record["products"]["IM"].update(
        post_core_put_contract="MO2612-P-7200", post_put_contract="MO2612-P-7200",
        post_momentum_put_contract=None, verified_parent_puts=3,
        verified_core_put_qty_normalized=1.5, verified_momentum_put_qty_normalized=0.0,
        verified_total_put_qty_normalized=1.5, verified_put_qty_normalized=1.5,
        post_core_put_equivalent_units=.75, post_momentum_put_equivalent_units=0.0,
        post_put_equivalent_units=.75, verified_call_contract=None,
        verified_call_expiry=None, verified_call_strike=None,
        v14_core_put_entry_premium=400.0, v14_core_put_profit3x_eligible=True)
    record["digest"] = state._digest(record)
    state._validate_record(record)
    return record


def _empty_core(anchor, product):
    anchor.update(v14_core_put_contract=None, v14_core_put_security_id=None,
                  v14_core_put_qty=0.0, v14_core_put_entry_premium=None,
                  v14_core_put_profit3x_eligible=False)
    if product == "IC":
        anchor.update(verified_core_put_qty=0, verified_core_put_delta=0.0,
                      verified_total_put_delta=0.0, verified_put_qty_normalized=0.0, post_put_qty=0.0)
    else:
        anchor.update(verified_core_put_qty_normalized=0.0, verified_parent_puts=0,
                      verified_total_put_qty_normalized=0.0, verified_put_qty_normalized=0.0,
                      post_put_contract=None, post_core_put_contract=None,
                      post_core_put_equivalent_units=0.0, post_put_equivalent_units=0.0)


def _route(anchor, product, route):
    _empty_core(anchor, product)
    anchor.update(v14_route_state=route, v14_cycle_id="synthetic-cycle",
                  v14_last_event_id="synthetic-prior-event", v14_recovery_net_pnl=-.1)
    if route == "short_put":
        anchor.update(v14_short_put_contract="510500P2610M07500" if product == "IC" else "MO2610-P-7200",
                      v14_short_put_security_id="10012098" if product == "IC" else None,
                      v14_short_put_qty_normalized=10.0 if product == "IC" else 1.5,
                      v14_short_put_entry_premium=.4 if product == "IC" else 400.0,
                      v14_short_put_expiry="2026-10-28" if product == "IC" else "2026-10-16")


def _persist(tmp_path, record):
    record["digest"] = state._digest(record)
    store = state.StateStore(tmp_path)
    store._atomic_write(store.journal_dir / "000000-2026-09-29.json", record)
    store._atomic_write(store.latest_path, record)
    return store, store.load_latest()


def _signal(product, anchor, day=DAY, *, momentum=False, candidate=None, opening=None, extra=None,
            live_quotes=None):
    current = deepcopy(anchor)
    if opening and opening["status"] == "confirmed_open_research_price":
        current = policy.project_ordinary_open(product, current, opening["plan"], opening["prices"])
    qty = (float(current["v14_core_put_qty"]) if product == "IC"
           else float(current["verified_core_put_qty_normalized"]))
    mom = (float(current["verified_momentum_put_qty"]) if product == "IC"
           else float(current["verified_momentum_put_qty_normalized"]))
    target_mom = (3.0 if product == "IC" else 1.5) if momentum else mom
    next_weight = 1.0 if target_mom > 0 else 0.0
    target_core = qty if current["v14_route_state"] == "future" else (14.0 if product == "IC" else 1.5)
    plan = bot.quarter_roll.roll_state(
        product, current["post_core_contract"], [product + "2612", product + "2703"], day, True,
        lambda c: bot._third_friday(*bot._contract_month(c)), bot._is_exchange_trading_day)
    signal = dict(plan, product=product, market_date=day, market_phase="收盘后", close_confirmed=True,
                  next_trade_date=bot._roll_forward_exchange_day(day + timedelta(days=1)),
                  state_anchor_day=date.fromisoformat(str(anchor["last_verified_day"])[:10]),
                  momentum_current_weight=float(anchor["verified_next_momentum_weight"]),
                  momentum_next_weight=next_weight, momentum_120=-.1,
                  grid_current=0.0, grid_target=0.0, grid_policy_revision=bot.grid_policy_revision(day),
                  option_monthly_reset_due=False, core_units_current=.5, core_units_target=.5,
                  total_units_current=.5, total_units_target=.5 + .5 * next_weight,
                  call_current_contract=None, call_target_contract=None, call_target_qty_normalized=0.0,
                  call_action="HOLD", call_target_expiry=None, call_target_strike=None)
    if product == "IC":
        core_delta = .25 if target_core else 0.0
        mom_delta = .125 if target_mom else 0.0
        signal.update(v14_core_put_contract=current["v14_core_put_contract"],
                      v14_core_put_security_id=current["v14_core_put_security_id"],
                      v14_core_put_qty=qty, put_current_contract=current["post_put_contract"],
                      put_current_core_qty=qty, put_current_momentum_qty=mom,
                      put_target_contract=current["post_put_contract"],
                      put_target_security_id=current["post_put_security_id"],
                      put_target_core_qty=int(target_core), put_target_momentum_qty=int(target_mom),
                      put_target_total_qty=int(target_core + target_mom),
                      core_put_target_delta=core_delta, momentum_put_target_delta=mom_delta,
                      total_put_target_delta=core_delta + mom_delta,
                      total_put_current_delta=float(current["verified_total_put_delta"]),
                      core_put_driver="synthetic core", momentum_put_driver="synthetic momentum",
                      valuation_tier=0, put_action="HOLD")
    else:
        signal.update(core_put_current_contract=current["post_core_put_contract"],
                      core_put_target_contract=current["post_core_put_contract"] or "MO2612-P-7200",
                      put_target_contract=current["post_core_put_contract"] or "MO2612-P-7200",
                      momentum_put_current_contract=current["post_momentum_put_contract"],
                      momentum_put_target_contract=(current["post_momentum_put_contract"] or "MO2612-P-8200") if target_mom else None,
                      core_put_current_qty_normalized=qty, core_put_target_qty_normalized=target_core,
                      momentum_put_current_qty_normalized=mom, momentum_put_target_qty_normalized=target_mom,
                      total_put_current_qty_normalized=qty + mom,
                      total_put_target_qty_normalized=target_core + target_mom,
                      v13_parent_puts_per_full_core=int(2 * target_core),
                      valuation_puts_per_full_core=0, put_reference_price=8000.0,
                      put_reference_future=current["post_core_contract"],
                      im_put_policy_revision=bot.IM_PUT_POLICY_REVISION,
                      im_put_execution_revision=bot.IM_PUT_EXECUTION_REVISION,
                      im_execution_fix_revision=bot.IM_EXECUTION_FIX_REVISION,
                      im_put_target_moneyness=1.02, core_put_action="HOLD",
                      momentum_put_action="RESIZE_OR_ROLL" if target_mom != mom else "HOLD")
    signal.update(extra or {})
    if live_quotes is None:
        signal = policy.apply_policy(product, signal, current, candidate=candidate or {"tradable": False})
    else:
        saved = bot.LIVE_CONTINUATION_ANCHOR[product]
        bot.LIVE_CONTINUATION_ANCHOR[product] = current
        try:
            signal = bot._apply_v14_live_policy(product, signal, mo_quotes=live_quotes)
        finally:
            bot.LIVE_CONTINUATION_ANCHOR[product] = saved
    bot._v14_schedule_ordinary_put(product, signal, current, opening)
    return signal


def _append(store, record, product, signal):
    anchors = state.anchors_from_record(record)
    other = "IM" if product == "IC" else "IC"
    signals = {product: signal, other: _signal(other, anchors[other], signal["market_date"])}
    result = store.append_confirmed_signals(record, signals)
    reopened = state.StateStore(store.root).load_latest()
    assert reopened["digest"] == result["digest"]
    return reopened


def _opening(product, anchor, day, *, missing=False):
    frame = pd.DataFrame([dict(instrument="MO2612-P-8200", openprice=0.0 if missing else 350.0),
                          dict(instrument="MO2612-P-7200", openprice=0.0 if missing else 350.0)])
    frame.attrs.update(source="synthetic_dated_open", source_date=day)
    saved = bot.LIVE_CONTINUATION_ANCHOR[product]
    bot.LIVE_CONTINUATION_ANCHOR[product] = anchor
    try:
        with patch.object(bot, "fetch_cffex_quotes", return_value=frame), patch.object(
            bot, "fetch_option_dated_open", return_value=(0.0 if missing else .35, "synthetic_dated_open")
        ):
            return bot._v14_ordinary_open_evidence(product, day, datetime.combine(day, datetime.min.time(), tzinfo=bot.BEIJING).replace(hour=18))
    finally:
        bot.LIVE_CONTINUATION_ANCHOR[product] = saved


@pytest.mark.parametrize("product", ["IC", "IM"])
def test_new_core_basis_survives_new_momentum_plan_and_restart(tmp_path, product):
    record = _record()
    anchor = record["products"][product]
    _empty_core(anchor, product)
    initial = _signal(product, state._decode_anchor(anchor), PREVIOUS)
    if product == "IC":
        initial.update(put_target_core_qty=14, core_put_target_delta=.25,
                       total_put_target_delta=.25, v14_core_put_qty=0)
    else:
        initial.update(core_put_target_qty_normalized=1.5, v13_parent_puts_per_full_core=3)
    bot._v14_schedule_ordinary_put(product, initial, anchor, None)
    anchor["v14_ordinary_put_pending"] = initial["v14_ordinary_put_pending"]
    store, record = _persist(tmp_path, record)
    current = state.anchors_from_record(record)[product]
    opening = _opening(product, current, DAY)
    signal = _signal(product, current, momentum=True, opening=opening)
    assert signal["v14_ordinary_put_pending"]["legs"]["core"]["changed"] is False
    reopened = _append(store, record, product, signal)
    held = reopened["products"][product]
    assert held["v14_core_put_entry_premium"] == (.35 if product == "IC" else 350.0)
    assert held["v14_core_put_profit3x_eligible"] is True


@pytest.mark.parametrize("product,route", [
    ("IC", "short_put"), ("IC", "assigned_etf"), ("IC", "recovery_future"),
    ("IM", "short_put"), ("IM", "cash_wait"), ("IM", "recovery_future")])
@pytest.mark.parametrize("missing", [False, True])
def test_nonfuture_momentum_plan_survives_restart_and_requires_exact_open(tmp_path, product, route, missing):
    record = _record()
    _route(record["products"][product], product, route)
    store, record = _persist(tmp_path, record)
    current = state.anchors_from_record(record)[product]
    signal = _signal(product, current, momentum=True)
    plan = signal["v14_ordinary_put_pending"]
    assert plan["legs"]["core"]["new_qty"] == 0.0
    assert plan["legs"]["core"]["changed"] is False
    record = _append(store, record, product, signal)
    current = state.anchors_from_record(record)[product]
    field = "verified_momentum_put_qty" if product == "IC" else "verified_momentum_put_qty_normalized"
    assert current[field] == 0.0
    next_day = bot._roll_forward_exchange_day(DAY + timedelta(days=1))
    opening = _opening(product, current, next_day, missing=missing)
    assert opening["status"] == ("closed_missing_open_price" if missing else "confirmed_open_research_price")
    # Test the second append before fix10's new Fear requirements by retaining
    # the real next session Oct 8 and adding the shared explicit evidence below.
    signal = _signal(product, current, next_day, momentum=True, opening=opening)
    other = "IM" if product == "IC" else "IC"
    pair = {product: signal, other: _signal(other, state.anchors_from_record(record)[other], next_day)}
    for item in pair.values():
        item.update(score=3.0, grid_action="HOLD", grid_entry_source_current="none", grid_entry_source_target="none",
                    grid_transition_reason="hold_flat", fear_greed_index=40.0, fear_data_date=next_day,
                    fear_data_status="same_day_post_close", fear_source_url=bot.fear_grid.CSV_URL,
                    fear_csv_sha256="a" * 64, fear_page_updated_at=f"{next_day}T15:10:00+08:00",
                    fear_retrieved_at=f"{next_day}T18:00:00+08:00")
    record = store.append_confirmed_signals(record, pair)
    reopened = state.StateStore(store.root).load_latest()
    expected = 0.0 if missing else (3.0 if product == "IC" else 1.5)
    assert reopened["products"][product][field] == expected
    assert reopened["products"][product]["v14_route_state"] == route


@pytest.mark.parametrize("product", ["IC", "IM"])
def test_enter_short_route_does_not_restore_core_due_to_momentum_pending(tmp_path, product):
    record = _record()
    store, record = _persist(tmp_path, record)
    anchor = state.anchors_from_record(record)[product]
    candidate = dict(tradable=True, iv=.6, contract="510500P2610M07500" if product == "IC" else "MO2610-P-7200",
                     security_id="10012098" if product == "IC" else None,
                     qty_normalized=10 if product == "IC" else 1.5,
                     premium=.4 if product == "IC" else 400.0,
                     expiry=date(2026, 10, 28) if product == "IC" else date(2026, 10, 16))
    # Existing momentum Put exits when MOM120 turns nonnegative on IM; the
    # independently changed IC momentum leg is still legitimate.
    if product == "IM":
        anchor.update(post_momentum_put_contract="MO2612-P-8200", verified_momentum_put_qty_normalized=1.5,
                      verified_total_put_qty_normalized=3.0, verified_put_qty_normalized=3.0,
                      post_momentum_put_equivalent_units=.75, post_put_equivalent_units=1.5)
        record["products"][product] = state._jsonable(anchor)
        store, record = _persist(tmp_path, record)
    signal = _signal(product, anchor, momentum=product == "IC", candidate=candidate,
                     extra=dict(momentum_120=.1, momentum_put_target_qty_normalized=0.0,
                                momentum_put_target_contract=None, total_put_target_qty_normalized=1.5) if product == "IM" else dict(momentum_120=.1))
    assert signal["v14_action"] == "ENTER_SHORT_PUT"
    result = _append(store, record, product, signal)
    held = result["products"][product]
    core_field = "verified_core_put_qty" if product == "IC" else "verified_core_put_qty_normalized"
    assert held[core_field] == 0.0
    assert held["v14_route_state"] == "short_put"
    assert held["v14_ordinary_put_pending"]["legs"]["core"]["old_qty"] == 0.0


@pytest.mark.parametrize("product", ["IC", "IM"])
def test_exit_recovery_core_remains_unfilled_until_ordinary_open(tmp_path, product):
    record = _record()
    _route(record["products"][product], product, "recovery_future")
    store, record = _persist(tmp_path, record)
    anchor = state.anchors_from_record(record)[product]
    signal = _signal(product, anchor, extra={"v14_recovery_net_pnl_mark": 0.0})
    assert signal["v14_action"] == "EXIT_RECOVERY_AT_BREAKEVEN"
    result = _append(store, record, product, signal)
    held = result["products"][product]
    assert held["v14_route_state"] == "future"
    assert held["v14_ordinary_put_pending"]["legs"]["core"]["new_qty"] > 0
    assert held["v14_core_put_qty"] == 0.0
    if product == "IM":
        assert held["verified_core_put_qty_normalized"] == 0.0
        assert held["verified_parent_puts"] == 0


@pytest.mark.parametrize("product", ["IC", "IM"])
def test_nonfuture_plan_cannot_reintroduce_core_or_conflict_with_profit(product):
    record = _record()
    _route(record["products"][product], product, "recovery_future")
    record["digest"] = state._digest(record)
    anchor = state.anchors_from_record(record)[product]
    signal = _signal(product, anchor, momentum=True)
    policy.validate_extension(product, signal)
    broken = deepcopy(signal)
    broken["v14_ordinary_put_pending"]["legs"]["core"].update(
        new_qty=1.0, new_contract="510500P2612M07500" if product == "IC" else "MO2612-P-7200",
        new_security_id="10012099" if product == "IC" else None, changed=True)
    with pytest.raises(RuntimeError, match="may only change momentum"):
        policy.validate_extension(product, broken)
    broken = deepcopy(signal)
    broken.update(v14_profit_pending=True, v14_profit_trigger_day=DAY)
    with pytest.raises(RuntimeError, match="conflicts with 3x"):
        policy.validate_extension(product, broken)


@pytest.mark.parametrize("product", ["IC", "IM"])
def test_exit_recovery_with_only_momentum_close_keeps_core_empty(tmp_path, product):
    record = _record()
    anchor = record["products"][product]
    _route(anchor, product, "recovery_future")
    if product == "IC":
        anchor.update(verified_momentum_put_qty=3, verified_put_qty_normalized=3.0,
                      post_put_qty=3.0, verified_momentum_put_delta=.125,
                      verified_total_put_delta=.125)
        extra = dict(put_target_core_qty=0, put_target_momentum_qty=0, put_target_total_qty=0,
                     core_put_target_delta=0.0, momentum_put_target_delta=0.0,
                     total_put_target_delta=0.0, v14_recovery_net_pnl_mark=0.0)
    else:
        anchor.update(post_momentum_put_contract="MO2612-P-8200", verified_momentum_put_qty_normalized=1.5,
                      verified_total_put_qty_normalized=1.5, verified_put_qty_normalized=1.5,
                      post_momentum_put_equivalent_units=.75, post_put_equivalent_units=.75,
                      verified_parent_puts=3)
        extra = dict(core_put_target_qty_normalized=0.0, core_put_target_contract=None,
                     put_target_contract=None, momentum_put_target_qty_normalized=0.0,
                     momentum_put_target_contract=None, total_put_target_qty_normalized=0.0,
                     v13_parent_puts_per_full_core=0, v14_recovery_net_pnl_mark=0.0)
    extra.update(momentum_120=.1, momentum_next_weight=0.0)
    store, record = _persist(tmp_path, record)
    signal = _signal(product, state.anchors_from_record(record)[product], extra=extra)
    assert signal["v14_action"] == "EXIT_RECOVERY_AT_BREAKEVEN"
    plan = signal["v14_ordinary_put_pending"]
    assert not plan["legs"]["core"]["changed"]
    assert plan["legs"]["momentum"]["changed"]
    result = _append(store, record, product, signal)
    held = result["products"][product]
    assert held["v14_route_state"] == "future"
    assert held["v14_core_put_qty"] == 0.0
    if product == "IM":
        assert held["verified_parent_puts"] == 0
        assert held["verified_momentum_put_qty_normalized"] == 1.5
    else:
        assert held["verified_momentum_put_qty"] == 3


@pytest.mark.parametrize("product", ["IC", "IM"])
def test_partial_open_evidence_never_projects_any_leg(product):
    record = _record()
    anchor = state.anchors_from_record(record)[product]
    _empty_core(anchor, product)
    plan = dict(product=product, signal_day=PREVIOUS, execution_day=DAY,
                core_delta=.25, momentum_delta=.125, core_driver="test", momentum_driver="test",
                parent_puts=3, legs={
                    "core": dict(old_contract=None, old_security_id=None, old_qty=0.0,
                                 new_contract="510500P2612M07500" if product == "IC" else "MO2612-P-7200",
                                 new_security_id="10012099" if product == "IC" else None,
                                 new_qty=14.0 if product == "IC" else 1.5, changed=True),
                    "momentum": dict(old_contract=None, old_security_id=None, old_qty=0.0,
                                     new_contract="510500P2612M07400" if product == "IC" else "MO2612-P-8200",
                                     new_security_id="10012097" if product == "IC" else None,
                                     new_qty=3.0 if product == "IC" else 1.5, changed=True)})
    anchor["v14_ordinary_put_pending"] = plan
    before = deepcopy(anchor)
    frame = pd.DataFrame([dict(instrument="MO2612-P-7200", openprice=350.0),
                          dict(instrument="MO2612-P-8200", openprice=0.0)])
    frame.attrs.update(source="synthetic_dated_open", source_date=DAY)
    saved = bot.LIVE_CONTINUATION_ANCHOR[product]
    bot.LIVE_CONTINUATION_ANCHOR[product] = anchor
    try:
        with patch.object(bot, "fetch_cffex_quotes", return_value=frame), patch.object(
            bot, "fetch_option_dated_open", side_effect=lambda security, day: (.35 if security == "10012099" else 0.0, "synthetic")
        ):
            opening = bot._v14_ordinary_open_evidence(product, DAY, datetime(2026, 9, 30, 18, tzinfo=bot.BEIJING))
    finally:
        bot.LIVE_CONTINUATION_ANCHOR[product] = saved
    assert opening["status"] == "closed_missing_open_price"
    assert anchor == before
    assert "prices" not in opening


def _profit_trigger(product, anchor, *, momentum=True):
    """Only the synthetic 3x threshold/selected identity is injected."""
    return _signal(product, anchor, momentum=momentum, extra=dict(
        v14_core_put_mark=1.3 if product == "IC" else 1200.0,
        v14_profit_plan_contract="510500P2612M07000" if product == "IC" else "MO2612-P-7000",
        v14_profit_plan_security_id="10012100" if product == "IC" else None,
        v14_profit_plan_qty=14 if product == "IC" else 1.5))


def _profit_append(store, record, product, signal):
    anchors = state.anchors_from_record(record)
    day = signal["market_date"]
    other = "IM" if product == "IC" else "IC"
    pair = {product: signal, other: _signal(other, anchors[other], day)}
    if day >= policy.FEAR_GRID_EFFECTIVE_SIGNAL_DATE:
        for item in pair.values():
            item.update(score=3.0, grid_action="HOLD", grid_entry_source_current="none", grid_entry_source_target="none",
                        grid_transition_reason="hold_flat", fear_greed_index=40.0, fear_data_date=day,
                        fear_data_status="same_day_post_close", fear_source_url=bot.fear_grid.CSV_URL,
                        fear_csv_sha256="a" * 64, fear_page_updated_at=f"{day}T15:10:00+08:00",
                        fear_retrieved_at=f"{day}T18:00:00+08:00")
    result = store.append_confirmed_signals(record, pair)
    reopened = state.StateStore(store.root).load_latest()
    assert result["digest"] == reopened["digest"]
    return reopened


def _profit_session(product, anchor, day, *, missing=None, momentum=True):
    """Use both actual open-evidence adapters and the lifecycle error path."""
    quotes = pd.DataFrame([
        dict(instrument=contract, openprice=0.0 if missing == leg else price)
        for contract, leg, price in (("MO2612-P-7200", "old_core", 340.0),
                                     ("MO2612-P-7000", "new_core", 350.0),
                                     ("MO2612-P-8200", "momentum", 360.0))])
    quotes.attrs.update(source="synthetic_dated_open", source_date=day)
    def ic_open(security, requested_day):
        assert requested_day == day
        leg, price = {"10012099": ("old_core", .34), "10012100": ("new_core", .35),
                      "10012000": ("momentum", .36)}[security]
        return (0.0 if missing == leg else price), "synthetic_dated_open"
    extra = {}
    if not momentum:
        extra.update(momentum_120=.1, momentum_next_weight=0.0)
        if product == "IC":
            extra.update(put_target_momentum_qty=0, put_target_total_qty=14,
                         momentum_put_target_delta=0.0, total_put_target_delta=.25)
        else:
            extra.update(momentum_put_target_qty_normalized=0.0,
                         momentum_put_target_contract=None, total_put_target_qty_normalized=1.5)
    saved = bot.LIVE_CONTINUATION_ANCHOR[product]
    bot.LIVE_CONTINUATION_ANCHOR[product] = anchor
    try:
        with ExitStack() as stack:
            stack.enter_context(patch.object(bot, "fetch_cffex_quotes", return_value=quotes))
            stack.enter_context(patch.object(bot, "fetch_option_dated_open", side_effect=ic_open))
            stack.enter_context(patch.object(bot, "_v14_ic_core_overlay", return_value=None))
            stack.enter_context(patch.object(bot, "_v14_ic_short_put_candidate", return_value={"tradable": False}))
            stack.enter_context(patch.object(bot, "_v14_im_short_put_candidate", return_value={"tradable": False}))
            stack.enter_context(patch.object(bot, "_v14_option_mark", return_value=(None, None)))
            clock = datetime.combine(day, datetime.min.time(), tzinfo=bot.BEIJING).replace(hour=18)
            opening = bot._v14_ordinary_open_evidence(product, day, clock)
            return _signal(product, anchor, day, momentum=momentum, opening=opening,
                           extra=extra, live_quotes=quotes)
    finally:
        bot.LIVE_CONTINUATION_ANCHOR[product] = saved


def _profit_record(product):
    record = _record()
    if product == "IC":
        # Separate momentum and old/new core identities so missing-one-leg
        # assertions cannot accidentally share the same option quote.
        record["products"][product].update(post_put_contract="510500P2612M07400",
                                            post_put_security_id="10012000")
    record["digest"] = state._digest(record)
    return record


@pytest.mark.parametrize("product", ["IC", "IM"])
@pytest.mark.parametrize("missing", [None, "old_core", "new_core", "momentum"])
def test_profit_and_momentum_execute_independently_after_restart(tmp_path, product, missing):
    store, record = _persist(tmp_path, _profit_record(product))
    anchor = state.anchors_from_record(record)[product]
    initial = _profit_trigger(product, anchor)
    assert initial["v14_action"] == "CORE_PUT_PROFIT3X_REENTER"
    ordinary = initial["v14_ordinary_put_pending"]
    assert ordinary["legs"]["momentum"]["changed"] is True
    assert ordinary["legs"]["core"]["changed"] is False
    record = _profit_append(store, record, product, initial)
    anchor = state.anchors_from_record(record)[product]
    field = "verified_momentum_put_qty" if product == "IC" else "verified_momentum_put_qty_normalized"
    assert anchor[field] == 0
    assert anchor["v14_profit_pending"] is True
    day = bot._roll_forward_exchange_day(DAY + timedelta(days=1))
    signal = _profit_session(product, anchor, day, missing=missing)
    result = _profit_append(store, record, product, signal)
    held = state.anchors_from_record(result)[product]
    core_executed = missing not in {"old_core", "new_core"}
    momentum_executed = missing != "momentum"
    assert held[field] == ((3 if product == "IC" else 1.5) if momentum_executed else 0)
    assert held["v14_profit_pending"] is (not core_executed)
    assert held["v14_core_put_profit3x_eligible"] is True
    assert held["v14_core_put_entry_premium"] == ((.35 if product == "IC" else 350.0) if core_executed else (.4 if product == "IC" else 400.0))
    assert held["v14_core_put_contract" if product == "IC" else "post_core_put_contract"] == (
        ("510500P2612M07000" if product == "IC" else "MO2612-P-7000") if core_executed
        else ("510500P2612M07500" if product == "IC" else "MO2612-P-7200"))
    if missing == "momentum":
        assert signal["v14_action"] == "EXECUTE_CORE_PUT_PROFIT3X_REENTER"
        assert signal["v14_ordinary_put_open_status"] == "closed_missing_open_price"
        plan = held["v14_ordinary_put_pending"]
        assert plan["legs"]["core"]["old_contract"] == signal["v14_profit_open_executed_contract"]
        assert plan["legs"]["core"]["new_contract"] == signal["v14_profit_open_executed_contract"]
    elif not core_executed:
        assert signal["v14_action"] == "HOLD_FAIL_CLOSED"
        assert signal["v14_ordinary_put_open_status"] == "confirmed_open_research_price"


@pytest.mark.parametrize("product", ["IC", "IM"])
@pytest.mark.parametrize("trigger_momentum", [False, True])
def test_profit_execution_day_new_momentum_plan_preserves_new_core_cost(tmp_path, product, trigger_momentum):
    store, record = _persist(tmp_path, _profit_record(product))
    initial = _profit_trigger(product, state.anchors_from_record(record)[product], momentum=trigger_momentum)
    record = _profit_append(store, record, product, initial)
    day = bot._roll_forward_exchange_day(DAY + timedelta(days=1))
    signal = _profit_session(product, state.anchors_from_record(record)[product], day, momentum=not trigger_momentum)
    assert signal["v14_action"] == "EXECUTE_CORE_PUT_PROFIT3X_REENTER"
    plan = signal["v14_ordinary_put_pending"]
    assert plan["legs"]["core"]["changed"] is False
    assert plan["legs"]["core"]["old_contract"] == signal["v14_profit_open_executed_contract"]
    assert plan["legs"]["core"]["new_contract"] == signal["v14_profit_open_executed_contract"]
    record = _profit_append(store, record, product, signal)
    anchor = state.anchors_from_record(record)[product]
    assert anchor["v14_core_put_entry_premium"] == (.35 if product == "IC" else 350.0)
    field = "verified_momentum_put_qty" if product == "IC" else "verified_momentum_put_qty_normalized"
    qty = 3 if product == "IC" else 1.5
    assert anchor[field] == (qty if trigger_momentum else 0)
    next_day = bot._roll_forward_exchange_day(day + timedelta(days=1))
    final = _profit_session(product, anchor, next_day, momentum=not trigger_momentum)
    result = _profit_append(store, record, product, final)
    held = state.anchors_from_record(result)[product]
    assert held[field] == (0 if trigger_momentum else qty)
    assert held["v14_core_put_entry_premium"] == (.35 if product == "IC" else 350.0)
    assert held["v14_core_put_profit3x_eligible"] is True


@pytest.mark.parametrize("product", ["IC", "IM"])
@pytest.mark.parametrize("field", ["contract", "security_id", "qty"])
@pytest.mark.parametrize("executed", [False, True])
def test_profit_overlap_rejects_unchanged_core_with_wrong_committed_identity(tmp_path, product, field, executed):
    store, record = _persist(tmp_path, _profit_record(product))
    signal = _profit_trigger(product, state.anchors_from_record(record)[product])
    if executed:
        record = _profit_append(store, record, product, signal)
        day = bot._roll_forward_exchange_day(DAY + timedelta(days=1))
        signal = _profit_session(product, state.anchors_from_record(record)[product], day, momentum=False)
        assert signal["v14_action"] == "EXECUTE_CORE_PUT_PROFIT3X_REENTER"
    core = signal["v14_ordinary_put_pending"]["legs"]["core"]
    for side in ("old", "new"):
        core[f"{side}_{field}"] = 1.0 if field == "qty" else "uncommitted-identity"
    with pytest.raises(RuntimeError):
        _profit_append(store, record, product, signal)
    assert state.StateStore(store.root).load_latest()["digest"] == record["digest"]


@pytest.mark.parametrize("product", ["IC", "IM"])
def test_profit_trigger_cannot_hide_missing_momentum_open_plan(tmp_path, product):
    store, record = _persist(tmp_path, _profit_record(product))
    signal = _profit_trigger(product, state.anchors_from_record(record)[product])
    signal["v14_ordinary_put_pending"] = None
    signal.pop("v14_ordinary_put_plan_status")
    with pytest.raises(RuntimeError, match="禁止提前记账"):
        _profit_append(store, record, product, signal)
    assert state.StateStore(store.root).load_latest()["digest"] == record["digest"]
