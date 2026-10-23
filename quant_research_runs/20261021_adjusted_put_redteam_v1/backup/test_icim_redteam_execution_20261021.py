"""Independent red-team execution boundaries; every quote and ledger is synthetic.

No network or official ledger access. Tests assert the independent invariants,
not the cumulative suite's result. The legacy complete ledger fixture is reused
only to supply the schema; the new guard's date is advanced explicitly in those
tests so unrelated Fear provenance cannot mask the execution boundary.
"""
from copy import deepcopy
from datetime import date, datetime, timedelta

import pytest
import requests

import ic_im_put_execution_v1 as execution
import ic_im_v1_4_policy as policy
import poe_ic_im_mainline_v1_4_bot as bot
import poe_ic_im_v1_4_state as state
import test_ic_im_external_review_20261021 as seed
import test_ic_im_ordinary_put_route_fix_20261013 as ledger
import test_ic_im_put_execution_repairs_20261016 as fixture

DAY = date(2026, 10, 21)
CLOCK = datetime(2026, 10, 21, 15, 40, tzinfo=bot.BEIJING)
PRODUCTS = ["IC", "IM"]


def ordinary_prices(plan, day):
    required = {(leg[f"{side}_contract"], leg.get(f"{side}_security_id"))
                for leg in plan["legs"].values() for side in ("old", "new")
                if leg["changed"] and float(leg[f"{side}_qty"]) > 0}
    return [dict(contract=c, security_id=s, price_day=day, openprice=.3,
                 source="redteam_synthetic") for c, s in required]


def with_independent_momentum(anchor, product):
    """Keep fixed-core identity unchanged while committing a momentum opening."""
    out = deepcopy(anchor)
    p = seed.ordinary_anchor(product)["v14_ordinary_put_pending"]
    p["legs"]["core"]["new_qty"] = p["legs"]["core"]["old_qty"]
    p["legs"]["core"]["changed"] = False
    if out["v14_route_state"] != "future":
        p["legs"]["core"] = dict(old_contract=None, new_contract=None,
            old_security_id=None, new_security_id=None, old_qty=0., new_qty=0., changed=False)
        p["core_delta"] = 0.
    p["legs"]["momentum"].update(
        new_contract="510500P2612M07000" if product == "IC" else "MO2612-P-7000",
        new_security_id="10017000" if product == "IC" else None,
        new_qty=1. if product == "IC" else .5, changed=True)
    out["v14_ordinary_put_pending"] = p
    return out


@pytest.mark.parametrize("product", PRODUCTS)
@pytest.mark.parametrize("kind", ["ordinary", "short_put", "profit3x"])
@pytest.mark.parametrize("outcome", ["omitted", "closed_monthly_priority", "closed_missed_execution_day", "closed_missing_open_price"])
def test_nonmonthly_due_plan_cannot_be_disguised_as_closed(product, kind, outcome):
    a = {"ordinary": seed.ordinary_anchor, "short_put": seed.short_anchor,
         "profit3x": seed.profit_anchor}[kind](product)
    signal = {"v14_opening_events": []}
    if outcome != "omitted":
        if kind == "ordinary":
            signal.update(v14_ordinary_put_open_status=outcome,
                          v14_ordinary_put_open_plan=a["v14_ordinary_put_pending"])
        else:
            p = a["v14_short_put_open_pending"] if kind == "short_put" else execution.profit_plan(product, a, DAY)
            signal["v14_opening_events"] = [dict(kind=kind, status=outcome, plan=p)]
    before = deepcopy(a)
    with pytest.raises(RuntimeError, match="unresolved opening evidence"):
        execution.require_due_openings(product, a, signal, DAY, False)
    assert a == before


@pytest.mark.parametrize("product", PRODUCTS)
@pytest.mark.parametrize("kind", ["ordinary", "short_put", "profit3x"])
def test_new_omission_guard_is_forward_only_but_old_closes_remain_readable(product, kind):
    day = date(2026, 10, 20)
    a = {"ordinary": seed.ordinary_anchor, "short_put": seed.short_anchor,
         "profit3x": seed.profit_anchor}[kind](product)
    if kind == "profit3x": a["v14_profit_execution_day"] = day
    elif kind == "ordinary": a["v14_ordinary_put_pending"]["execution_day"] = day
    else: a["v14_short_put_open_pending"]["execution_day"] = day
    execution.require_due_openings(product, a, {}, day, False)
    # Advance the signal date only: legacy prior-day obligations cannot be
    # erased by the new producer, even though old records remain readable.
    with pytest.raises(RuntimeError):
        execution.require_due_openings(product, a, {}, DAY, False)


@pytest.mark.parametrize("product", PRODUCTS)
def test_monthly_priority_does_not_suppress_due_short_open(product):
    a = seed.short_anchor(product)
    with pytest.raises(RuntimeError, match="short_put plan is still due"):
        execution.require_due_openings(product, a, {}, DAY, True)


@pytest.mark.parametrize("product", PRODUCTS)
def test_overdue_profit_requires_replay_even_under_monthly_priority(product):
    a = seed.profit_anchor(product)
    with pytest.raises(RuntimeError, match="replay the original 3x execution day"):
        execution.require_due_openings(product, a, {}, DAY + timedelta(days=1), True)


@pytest.mark.parametrize("product", PRODUCTS)
@pytest.mark.parametrize("broken", ["ordinary", "profit3x", None])
def test_independent_momentum_and_profit_open_are_both_required(monkeypatch, product, broken):
    a = with_independent_momentum(seed.profit_anchor(product), product)
    policy.validate_extension(product, a)
    before = deepcopy(a)
    monkeypatch.setitem(bot.LIVE_CONTINUATION_ANCHOR, product, a)
    ordinary = a["v14_ordinary_put_pending"]
    p = execution.profit_plan(product, a, DAY)
    opening = dict(status="confirmed_open_research_price", plan=ordinary,
                   prices=ordinary_prices(ordinary, DAY))
    if broken == "ordinary": opening["status"] = execution.RETRY_OPEN_STATUS
    if opening["status"] == "confirmed_open_research_price":
        projected = policy.project_ordinary_open(product, a, ordinary, opening["prices"])
        monkeypatch.setitem(bot.LIVE_CONTINUATION_ANCHOR, product, projected)
    def fetch(*args):
        if broken == "profit3x": raise requests.Timeout("synthetic second-leg interruption")
        return fixture.prices(p, day=DAY, new_price=17.)
    monkeypatch.setattr(bot, "_v14_committed_open_prices", fetch)
    events = bot._v14_project_committed_opens(product, DAY, CLOCK, {})
    signal = dict(v14_ordinary_put_open_status=opening["status"],
                  v14_ordinary_put_open_plan=ordinary, v14_opening_events=events)
    if broken:
        with pytest.raises(RuntimeError, match="unresolved opening evidence"):
            execution.require_due_openings(product, a, signal, DAY, False)
    else:
        execution.require_due_openings(product, a, signal, DAY, False)
        held = bot.LIVE_CONTINUATION_ANCHOR[product]
        assert held["v14_core_put_entry_premium"] == 17.
        assert not held["v14_profit_pending"]
        momentum_key = "verified_momentum_put_qty" if product == "IC" else "verified_momentum_put_qty_normalized"
        assert held[momentum_key] == ordinary["legs"]["momentum"]["new_qty"]
    assert a == before


@pytest.mark.parametrize("product", PRODUCTS)
@pytest.mark.parametrize("damage", ["missing", "duplicate", "wrong_day", "empty_source", "infinite", "negative", "wrong_security"])
def test_committed_short_exact_price_evidence_is_not_an_adapter_trust_boundary(product, damage):
    a = seed.short_anchor(product)
    p = a["v14_short_put_open_pending"]
    quotes = fixture.prices(p, day=DAY)
    if damage == "missing": quotes.pop()
    elif damage == "duplicate": quotes.append(deepcopy(quotes[0]))
    elif damage == "wrong_day": quotes[0]["price_day"] = DAY - timedelta(days=1)
    elif damage == "empty_source": quotes[0]["source"] = " "
    elif damage == "infinite": quotes[0]["openprice"] = float("inf")
    elif damage == "negative": quotes[0]["openprice"] = -.1
    else: quotes[0]["security_id"] = "forged"
    original = deepcopy(a)
    with pytest.raises(RuntimeError):
        execution.project_short_open(product, a,
            dict(plan=p, status="confirmed_open_research_price", prices=quotes), DAY)
    assert a == original


@pytest.mark.parametrize("product", PRODUCTS)
@pytest.mark.parametrize("damage", ["missing", "duplicate", "wrong_day", "empty_source", "infinite", "wrong_identity"])
def test_ordinary_forged_prices_fail_before_any_hash_ledger_write(tmp_path, monkeypatch, product, damage):
    monkeypatch.setattr(execution, "OPEN_EVIDENCE_EFFECTIVE_DATE", ledger.DAY)
    r = ledger._record()
    previous = ledger._signal(product, state.anchors_from_record(r)[product], ledger.PREVIOUS, momentum=True)
    r["products"][product]["v14_ordinary_put_pending"] = previous["v14_ordinary_put_pending"]
    store, r = ledger._persist(tmp_path, r)
    anchors = state.anchors_from_record(r)
    opening = ledger._opening(product, anchors[product], ledger.DAY)
    signal = ledger._signal(product, anchors[product], momentum=True, opening=opening)
    prices = signal["v14_ordinary_put_open_prices"]
    if damage == "missing": prices.pop()
    elif damage == "duplicate": prices.append(deepcopy(prices[0]))
    elif damage == "wrong_day": prices[0]["price_day"] = ledger.PREVIOUS
    elif damage == "empty_source": prices[0]["source"] = ""
    elif damage == "infinite": prices[0]["openprice"] = float("inf")
    else: prices[0]["contract"] = "FAKE-PUT"
    signals = {p: signal if p == product else ledger._signal(p, anchors[p]) for p in PRODUCTS}
    before = {p.relative_to(tmp_path).as_posix(): p.read_bytes() for p in tmp_path.rglob("*.json")}
    with pytest.raises(RuntimeError): store.append_confirmed_signals(r, signals)
    assert {p.relative_to(tmp_path).as_posix(): p.read_bytes() for p in tmp_path.rglob("*.json")} == before


@pytest.mark.parametrize("product", PRODUCTS)
def test_journal_crash_recovery_and_repeat_append_preserve_one_execution(tmp_path, monkeypatch, product):
    monkeypatch.setattr(execution, "OPEN_EVIDENCE_EFFECTIVE_DATE", ledger.DAY)
    r = ledger._record()
    s = ledger._signal(product, state.anchors_from_record(r)[product], ledger.PREVIOUS, momentum=True)
    r["products"][product]["v14_ordinary_put_pending"] = s["v14_ordinary_put_pending"]
    store, r = ledger._persist(tmp_path, r)
    anchors = state.anchors_from_record(r)
    opening = ledger._opening(product, anchors[product], ledger.DAY)
    signals = {p: ledger._signal(p, anchors[p], momentum=(p == product),
                opening=opening if p == product else None) for p in PRODUCTS}
    write = store._atomic_write
    def crash(path, record):
        if path == store.latest_path: raise OSError("synthetic crash after journal commit")
        return write(path, record)
    monkeypatch.setattr(store, "_atomic_write", crash)
    with pytest.raises(OSError): store.append_confirmed_signals(r, signals)
    monkeypatch.setattr(store, "_atomic_write", write)
    completed = state.StateStore(tmp_path).append_confirmed_signals(r, signals)
    assert completed["sequence"] == r["sequence"] + 1
    assert completed["products"][product]["v14_ordinary_put_pending"] is None
    assert len(list(store.journal_dir.glob("*.json"))) == 2
    with pytest.raises(RuntimeError): store.append_confirmed_signals(completed, signals)
    assert state.StateStore(tmp_path).load_latest()["digest"] == completed["digest"]


@pytest.mark.parametrize("product", PRODUCTS)
@pytest.mark.parametrize("failed", ["ordinary", "short_put"])
def test_public_query_restores_original_anchor_when_coexisting_openings_fail(monkeypatch, product, failed):
    a = with_independent_momentum(seed.short_anchor(product), product)
    policy.validate_extension(product, a)
    original = deepcopy(a)
    monkeypatch.setitem(bot.LIVE_CONTINUATION_ANCHOR, product, a)
    monkeypatch.setattr(bot, "_require_official_exchange_calendar", lambda *args: None)
    monkeypatch.setattr(bot, "live_proxy", lambda *args: {"history_date": DAY})
    monkeypatch.setattr(bot, "_validate_signal_market_date", lambda *args: True)
    p = a["v14_ordinary_put_pending"]
    opening = dict(status=execution.RETRY_OPEN_STATUS if failed == "ordinary" else "confirmed_open_research_price",
                   plan=p, prices=ordinary_prices(p, DAY))
    monkeypatch.setattr(bot, "_v14_ordinary_open_evidence", lambda *args: deepcopy(opening))
    calls = []
    def failed_short(*args):
        calls.append(args)
        raise requests.Timeout("synthetic short failure after independent momentum projection")
    monkeypatch.setattr(bot, "_v14_committed_open_prices", failed_short)
    with pytest.raises(RuntimeError, match="unresolved opening evidence"):
        bot.build_live_trade_signal(product, CLOCK, "close")
    assert len(calls) == (0 if failed == "ordinary" else 1)
    assert bot.LIVE_CONTINUATION_ANCHOR[product] is a
    assert bot.LIVE_CONTINUATION_ANCHOR[product] == original


@pytest.mark.parametrize("product", PRODUCTS)
def test_ordinary_and_short_coexist_without_losing_independent_momentum(monkeypatch, product):
    a = with_independent_momentum(seed.short_anchor(product), product)
    policy.validate_extension(product, a)
    p = a["v14_ordinary_put_pending"]
    opening = dict(plan=p, status="confirmed_open_research_price", prices=ordinary_prices(p, DAY))
    projected = policy.project_ordinary_open(product, a, p, opening["prices"])
    monkeypatch.setitem(bot.LIVE_CONTINUATION_ANCHOR, product, projected)
    monkeypatch.setattr(bot, "_v14_committed_open_prices",
        lambda product, plan, day, clock, cache: fixture.prices(plan, day=DAY, new_price=13.))
    events = bot._v14_project_committed_opens(product, DAY, CLOCK, {})
    execution.require_due_openings(product, a,
        dict(v14_ordinary_put_open_status=opening["status"], v14_ordinary_put_open_plan=p,
             v14_opening_events=events), DAY, False)
    held = bot.LIVE_CONTINUATION_ANCHOR[product]
    assert held["v14_short_put_entry_premium"] == 13.
    key = "verified_momentum_put_qty" if product == "IC" else "verified_momentum_put_qty_normalized"
    assert held[key] == p["legs"]["momentum"]["new_qty"]
    assert bot._v14_project_committed_opens(product, DAY, CLOCK, {}) == []


@pytest.mark.parametrize("product", PRODUCTS)
def test_calendar_monthly_priority_skips_only_ordinary_and_profit_not_short(monkeypatch, product):
    monthly = date(2026, 11, 20)
    assert monthly == bot._third_friday(2026, 11)
    clock = CLOCK.replace(month=11, day=20)
    ordinary = seed.ordinary_anchor(product)
    ordinary["v14_ordinary_put_pending"].update(signal_day=monthly - timedelta(days=1), execution_day=monthly)
    monkeypatch.setitem(bot.LIVE_CONTINUATION_ANCHOR, product, ordinary)
    def no_price(*args): raise AssertionError("monthly ordinary/profit must skip opening IO")
    monkeypatch.setattr(bot, "fetch_option_dated_open", no_price)
    monkeypatch.setattr(bot, "fetch_cffex_quotes", no_price)
    opening = bot._v14_ordinary_open_evidence(product, monthly, clock, {})
    assert opening["status"] == "closed_monthly_priority"
    profit = seed.profit_anchor(product)
    profit.update(v14_profit_trigger_day=monthly - timedelta(days=1), v14_profit_execution_day=monthly)
    monkeypatch.setitem(bot.LIVE_CONTINUATION_ANCHOR, product, profit)
    monkeypatch.setattr(bot, "_v14_committed_open_prices", no_price)
    assert bot._v14_project_committed_opens(product, monthly, clock, {}) == []
    execution.require_due_openings(product, profit, {"v14_opening_events": []}, monthly, True)
    short = seed.short_anchor(product)
    short["v14_short_put_open_pending"].update(signal_day=monthly - timedelta(days=1), execution_day=monthly)
    monkeypatch.setitem(bot.LIVE_CONTINUATION_ANCHOR, product, short)
    calls = []
    def prices(product, plan, day, clock, cache):
        calls.append(plan)
        return fixture.prices(plan, day=monthly, new_price=15.)
    monkeypatch.setattr(bot, "_v14_committed_open_prices", prices)
    events = bot._v14_project_committed_opens(product, monthly, clock, {})
    assert len(calls) == 1
    assert events[0]["status"] == "confirmed_open_research_price"
    execution.require_due_openings(product, short, {"v14_opening_events": events}, monthly, True)
