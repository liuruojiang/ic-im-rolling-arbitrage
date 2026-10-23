"""Independent counterexamples for the external review; injected prices are synthetic."""
from copy import deepcopy
from datetime import date, datetime
from pathlib import Path
import json
from types import SimpleNamespace

import pytest
import requests

import ic_im_put_execution_v1 as execution
import ic_im_v1_4_policy as policy
import poe_ic_im_mainline_v1_4_bot as bot
import poe_ic_im_v1_4_state as state
import test_ic_im_put_execution_repairs_20261016 as fixture

DAY = date(2026, 10, 21)
PREVIOUS = date(2026, 10, 20)
CLOCK = datetime(2026, 10, 21, 15, 40, tzinfo=bot.BEIJING)
RETRY = "awaiting_retry_open_evidence"


def ordinary_anchor(product):
    anchor = fixture.anchor(product)
    anchor["last_verified_day"] = PREVIOUS
    old = anchor["v14_core_put_contract"] if product == "IC" else anchor["post_core_put_contract"]
    security = anchor.get("v14_core_put_security_id") if product == "IC" else None
    qty = float(anchor["v14_core_put_qty"] if product == "IC" else anchor["verified_core_put_qty_normalized"])
    anchor["v14_ordinary_put_pending"] = dict(product=product, signal_day=PREVIOUS,
        execution_day=DAY, core_delta=.25, momentum_delta=0., core_driver="mom120",
        momentum_driver="valuation", parent_puts=3, legs={
            "core": dict(old_contract=old, new_contract=old, old_qty=qty, new_qty=qty+1,
                         old_security_id=security, new_security_id=security, changed=True),
            "momentum": dict(old_contract=None, new_contract=None, old_qty=0., new_qty=0.,
                             old_security_id=None, new_security_id=None, changed=False)})
    return anchor


def short_anchor(product):
    out = policy.apply_policy(product,
        fixture.signal(product, PREVIOUS, next_trade_date=DAY), fixture.anchor(product),
        candidate=fixture.candidate(product))
    out["last_verified_day"] = PREVIOUS
    return out


def profit_anchor(product):
    anchor = fixture.anchor(product)
    anchor.update(last_verified_day=PREVIOUS, v14_profit_pending=True,
        v14_profit_trigger_day=PREVIOUS, v14_profit_execution_day=DAY,
        v14_profit_old_contract=anchor["v14_core_put_contract"] if product == "IC" else anchor["post_core_put_contract"],
        v14_profit_old_security_id="10012000" if product == "IC" else None,
        v14_profit_old_qty=5. if product == "IC" else 1.5,
        v14_profit_reentry_contract="510500P2701M06800" if product == "IC" else "MO2701-P-6800",
        v14_profit_reentry_security_id="10014000" if product == "IC" else None,
        v14_profit_reentry_qty=5. if product == "IC" else 1.5)
    return anchor


@pytest.mark.parametrize("product", ["IC", "IM"])
@pytest.mark.parametrize("error", [requests.Timeout("temporary"), RuntimeError("budget exhausted"),
                                   ValueError("malformed"), KeyError("openprice")])
def test_ordinary_transient_failure_retains_original_plan(monkeypatch, product, error):
    anchor = ordinary_anchor(product)
    original = deepcopy(anchor)
    monkeypatch.setitem(bot.LIVE_CONTINUATION_ANCHOR, product, anchor)
    def unavailable(*args, **kwargs):
        raise error
    monkeypatch.setattr(bot, "fetch_option_dated_open", unavailable)
    monkeypatch.setattr(bot, "fetch_cffex_quotes", unavailable)
    event = bot._v14_ordinary_open_evidence(product, DAY, CLOCK, {})
    assert event["status"] == RETRY
    assert event["plan"] == original["v14_ordinary_put_pending"]
    assert anchor == original
    signal = fixture.signal(product, DAY)
    bot._v14_schedule_ordinary_put(product, signal, anchor, event)
    assert signal["v14_ordinary_put_pending"] == original["v14_ordinary_put_pending"]
    assert signal["v14_action"] == "HOLD_FAIL_CLOSED"


@pytest.mark.parametrize("product", ["IC", "IM"])
@pytest.mark.parametrize("kind", ["short_put", "profit3x"])
def test_committed_failure_cannot_roll_back_or_cancel_plan(monkeypatch, product, kind):
    anchor = short_anchor(product) if kind == "short_put" else profit_anchor(product)
    original = deepcopy(anchor)
    monkeypatch.setitem(bot.LIVE_CONTINUATION_ANCHOR, product, anchor)
    def unavailable(*args):
        raise requests.ConnectionError("synthetic transport failure")
    monkeypatch.setattr(bot, "_v14_committed_open_prices", unavailable)
    events = bot._v14_project_committed_opens(product, DAY, CLOCK, {})
    assert events[0]["status"] == RETRY
    assert bot.LIVE_CONTINUATION_ANCHOR[product] == original


@pytest.mark.parametrize("product", ["IC", "IM"])
@pytest.mark.parametrize("kind", ["ordinary", "short_put", "profit3x"])
def test_policy_and_new_signal_boundary_reject_unresolved_open(product, kind):
    anchor = fixture.anchor(product)
    signal = fixture.signal(product, DAY)
    if kind == "ordinary":
        signal["v14_ordinary_put_open_status"] = RETRY
    else:
        signal["v14_opening_events"] = [dict(kind=kind, status=RETRY)]
    with pytest.raises(RuntimeError, match="unresolved opening evidence"):
        policy.apply_policy(product, signal, anchor, candidate={"tradable": False})
    with pytest.raises(RuntimeError, match="unresolved opening evidence"):
        state.validate_v14_new_signal(signal, product)


@pytest.mark.parametrize("product", ["IC", "IM"])
def test_failed_query_never_reaches_new_close_target(monkeypatch, product):
    anchor = ordinary_anchor(product)
    original = deepcopy(anchor)
    monkeypatch.setitem(bot.LIVE_CONTINUATION_ANCHOR, product, anchor)
    monkeypatch.setattr(bot, "live_proxy", lambda *args: {"history_date": DAY})
    monkeypatch.setattr(bot, "_validate_signal_market_date", lambda *args: True)
    def unavailable(*args, **kwargs):
        raise requests.Timeout("synthetic timeout")
    monkeypatch.setattr(bot, "fetch_option_dated_open", unavailable)
    monkeypatch.setattr(bot, "fetch_cffex_quotes", unavailable)
    with pytest.raises(RuntimeError, match="unresolved opening evidence"):
        bot.build_live_trade_signal(product, CLOCK, "close")
    assert bot.LIVE_CONTINUATION_ANCHOR[product] == original


def sina_responses(last="0.1234", ask="0.2345", low="0.0101", extra="0.3456"):
    fields = ["0"] * 47
    for index, value in {1:"0.1200", 2:last, 3:ask, 7:"6.5", 8:"0.13", 32:"2026-10-21 13:05:00",
                         35:"EBS", 36:"510500", 39:"0.4", 40:low, 41:"123", 43:"M",
                         44:extra, 45:"P", 46:"2026-11-25"}.items():
        fields[index] = value
    def response(content):
        return SimpleNamespace(content=content.encode("gbk"), headers={}, raise_for_status=lambda: None)
    return iter([response('var hq_str_OP_DOWN_5105002611="CON_OP_10013000,";'),
                 response('var hq_str_CON_OP_10013000="'+",".join(fields)+'";')])


def test_sina_last_is_trade_not_ask_and_unknown_settlement_is_not_low(monkeypatch):
    responses = sina_responses()
    monkeypatch.setattr(bot.requests, "get", lambda *a, **k: next(responses))
    chain, stamp = bot.fetch_sina_510500_chain("202611")
    assert chain.iloc[0]["last"] == .1234
    assert chain.iloc[0]["pre_settle"] is None
    assert chain.iloc[0]["security_id"] == "10013000"
    assert stamp["date"] == "20261021"


@pytest.mark.parametrize("last", ["nan", "inf", "-0.1"])
def test_sina_nonfinite_or_negative_trade_cannot_enter_chain(monkeypatch, last):
    responses = sina_responses(last=last)
    monkeypatch.setattr(bot.requests, "get", lambda *a, **k: next(responses))
    with pytest.raises(RuntimeError):
        bot.fetch_sina_510500_chain("202611")


def test_sina_valid_last_outside_current_spread_is_accepted(monkeypatch):
    responses = sina_responses(last="0.3", ask="0.2")
    monkeypatch.setattr(bot.requests, "get", lambda *a, **k: next(responses))
    chain, _ = bot.fetch_sina_510500_chain("202611")
    assert chain.iloc[0]["last"] == .3


def test_real_saved_sina_payload_matches_independent_field_oracle(monkeypatch):
    root = Path(__file__).parent / "tests/fixtures/icim_external_review_20261021"
    # The saved quote request deliberately captured only the first three IDs.
    # Project the request list to those captured IDs; keep raw quotes unchanged.
    capture = json.loads((root / "capture.json").read_text(encoding="utf-8"))
    listing = ('var hq_str_OP_DOWN_5105002611="' +
               ','.join('CON_OP_' + value for value in capture["contracts"]) + ',";').encode()
    responses = iter([SimpleNamespace(content=content, headers={},
                                      raise_for_status=lambda: None)
                      for content in [listing, (root / "sina_quotes_literal.bin").read_bytes()]])
    monkeypatch.setattr(bot.requests, "get", lambda *a, **k: next(responses))
    chain, _ = bot.fetch_sina_510500_chain("202611")
    expected = {"10012482": .1669, "10012483": .2885, "10012484": .4563}
    assert dict(zip(chain.security_id, chain["last"])) == expected
    assert chain.pre_settle.isna().all()
    # Last=0.4563 is below the simultaneously observed bid=0.4568.
    assert chain.loc[chain.security_id.eq("10012484"), "last"].item() == .4563


@pytest.mark.parametrize("product", ["IC", "IM"])
def test_failed_evidence_preserves_hash_ledger_then_original_day_recovers(tmp_path, monkeypatch, product):
    import test_ic_im_ordinary_put_route_fix_20261013 as ledger_fixture
    # Exercise the new gate against an existing complete pre-Fear fixture;
    # dates and prices are explicitly synthetic, not historical return evidence.
    monkeypatch.setattr(execution, "OPEN_EVIDENCE_EFFECTIVE_DATE", ledger_fixture.DAY)
    record = ledger_fixture._record()
    decoded = state.anchors_from_record(record)[product]
    previous = ledger_fixture._signal(product, decoded, ledger_fixture.PREVIOUS, momentum=True)
    record["products"][product]["v14_ordinary_put_pending"] = previous["v14_ordinary_put_pending"]
    store, record = ledger_fixture._persist(tmp_path, record)
    before = {p.relative_to(tmp_path).as_posix():p.read_bytes() for p in tmp_path.rglob("*.json")}
    anchor = state.anchors_from_record(record)[product]
    signal = ledger_fixture._signal(product, anchor, momentum=True)
    signal.update(v14_ordinary_put_open_status=RETRY,
                  v14_ordinary_put_open_plan=anchor["v14_ordinary_put_pending"])
    other = "IM" if product == "IC" else "IC"
    signals = {product: signal, other: ledger_fixture._signal(other, state.anchors_from_record(record)[other])}
    with pytest.raises(RuntimeError, match="unresolved opening evidence"):
        store.append_confirmed_signals(record, signals)
    assert {p.relative_to(tmp_path).as_posix():p.read_bytes() for p in tmp_path.rglob("*.json")} == before
    # Disguising the failure as the old closed status or omitting it must not
    # bypass the new-write check against the actual persisted plan.
    for status in ["closed_missing_open_price", None]:
        signals[product]["v14_ordinary_put_open_status"] = status
        with pytest.raises(RuntimeError, match="unresolved opening evidence"):
            store.append_confirmed_signals(record, signals)
    opening = ledger_fixture._opening(product, anchor, ledger_fixture.DAY)
    assert opening["status"] == "confirmed_open_research_price"
    signals[product] = ledger_fixture._signal(product, anchor, momentum=True, opening=opening)
    updated = store.append_confirmed_signals(record, signals)
    reopened = state.StateStore(tmp_path).load_latest()
    assert reopened["digest"] == updated["digest"]
    assert reopened["sequence"] == record["sequence"] + 1
    assert state.anchors_from_record(reopened)[product]["v14_ordinary_put_pending"] is None
    with pytest.raises(RuntimeError):
        store.append_confirmed_signals(reopened, signals)
    assert state.StateStore(tmp_path).load_latest()["digest"] == reopened["digest"]


def test_real_adjusted_rows_are_quarantined_without_inventing_trading_codes(monkeypatch):
    root = Path(__file__).parent / "tests/fixtures/icim_external_review_20261021"
    responses = iter([SimpleNamespace(content=(root/name).read_bytes(), headers={},
                                      raise_for_status=lambda: None)
                      for name in ["sina_december_listing.bin", "sina_december_quotes.bin"]])
    monkeypatch.setattr(bot.requests, "get", lambda *a, **k: next(responses))
    chain, stamp = bot.fetch_sina_510500_chain("202612")
    assert len(chain) == 15
    assert chain.contract.str.contains("M").all()
    assert len(chain.attrs["unresolved_adjusted_contracts"]) == 14
    # Official SSE code keeps the original strike digits, whereas the
    # independent actual strike is adjusted. Rebuilding A07367 is false.
    official = json.loads((root/"sse_december_chain.json").read_bytes())
    assert [r for r in official["list"] if r[0] == "510500P2612A07500"][0][4] == 7.367
    assert "510500P2612A07367" not in set(chain.contract)


def test_adjusted_held_core_cannot_use_standard_multiplier_or_m_quote(monkeypatch):
    anchor = fixture.anchor("IC")
    anchor["v14_core_put_contract"] = "510500P2612A07500"
    with pytest.raises(RuntimeError, match="adjusted contract master"):
        bot._v14_ic_core_overlay(fixture.signal("IC", DAY), anchor)


def test_adjusted_guard_survives_candidate_exception_fallback(monkeypatch):
    anchor = fixture.anchor("IC")
    anchor["v14_core_put_contract"] = "510500P2612A07500"
    monkeypatch.setitem(bot.LIVE_CONTINUATION_ANCHOR, "IC", anchor)
    with pytest.raises(RuntimeError, match="adjusted contract master"):
        bot._apply_v14_live_policy("IC", fixture.signal("IC", DAY))
    with pytest.raises(RuntimeError, match="adjusted contract master"):
        policy.apply_policy("IC", fixture.signal("IC", DAY), anchor, candidate={"tradable": False})
    with pytest.raises(RuntimeError, match="adjusted contract master"):
        state.validate_v14_new_signal({**fixture.signal("IC", DAY),
                                      "v14_core_put_contract":"510500P2612A07500"}, "IC")


@pytest.mark.parametrize("product", ["IC", "IM"])
@pytest.mark.parametrize("kind", ["short_put", "profit3x"])
def test_due_committed_plan_cannot_be_hidden_by_closed_or_missing_event(product, kind):
    anchor = short_anchor(product) if kind == "short_put" else profit_anchor(product)
    plan = anchor["v14_short_put_open_pending"] if kind == "short_put" else execution.profit_plan(product, anchor, DAY)
    for status in ["closed_missing_open_price", RETRY, None]:
        signal = dict(v14_opening_events=[dict(kind=kind, plan=plan, status=status)])
        with pytest.raises(RuntimeError, match="unresolved opening evidence"):
            execution.require_due_openings(product, anchor, signal, DAY, False)


@pytest.mark.parametrize("product", ["IC", "IM"])
def test_short_evidence_recovers_original_contract_and_cost_after_retry(monkeypatch, product):
    anchor = short_anchor(product)
    original = deepcopy(anchor)
    monkeypatch.setitem(bot.LIVE_CONTINUATION_ANCHOR, product, anchor)
    def unavailable(*args):
        raise requests.Timeout("temporary")
    monkeypatch.setattr(bot, "_v14_committed_open_prices", unavailable)
    events = bot._v14_project_committed_opens(product, DAY, CLOCK, {})
    assert events[0]["status"] == RETRY
    monkeypatch.setattr(bot, "_v14_committed_open_prices", lambda product, plan, day, clock, cache:
                        fixture.prices(plan, day=DAY, new_price=12.))
    events = bot._v14_project_committed_opens(product, DAY, CLOCK, {})
    held = bot.LIVE_CONTINUATION_ANCHOR[product]
    assert events[0]["status"] == "confirmed_open_research_price"
    assert held["v14_short_put_contract"] == original["v14_short_put_contract"]
    assert held["v14_short_put_entry_premium"] == 12.
    assert held["v14_short_put_entry_price_day"] == DAY
    assert bot._v14_project_committed_opens(product, DAY, CLOCK, {}) == []


@pytest.mark.parametrize("product,field", [("IC","put_target_total_qty"),
                                          ("IC","put_target_core_qty"),
                                          ("IM","core_put_target_qty_normalized")])
def test_incomplete_put_target_never_advances_real_ledger(tmp_path, product, field):
    import test_ic_im_ordinary_put_route_fix_20261013 as ledger_fixture
    record=ledger_fixture._record()
    store,record=ledger_fixture._persist(tmp_path,record)
    before={p.relative_to(tmp_path).as_posix():p.read_bytes() for p in tmp_path.rglob("*.json")}
    anchors=state.anchors_from_record(record)
    signals={p:ledger_fixture._signal(p,anchors[p]) for p in state.PRODUCTS}
    signals[product][field]=None
    with pytest.raises((RuntimeError,TypeError)):
        store.append_confirmed_signals(record,signals)
    assert {p.relative_to(tmp_path).as_posix():p.read_bytes() for p in tmp_path.rglob("*.json")}==before
