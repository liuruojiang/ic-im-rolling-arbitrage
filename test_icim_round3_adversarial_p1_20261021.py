"""Independent round-three P1 adversaries; synthetic observations, no network."""
from copy import deepcopy
from datetime import date, datetime
import importlib
import importlib.util
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest

import poe_ic_im_mainline_v1_4_bot as bot
import poe_ic_im_v1_4_state as state
import ic_im_v1_4_policy as policy
from test_icim_b3adv_state_20261021 import prepare
from test_icim_round3_policy_repairs_20261021 import wire_adapter
from test_ic_im_v1_4_policy import _signal, _candidate
from test_icim_round3_grid_persistence_20261021 import unavailable_fear


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def reject(*args, **kwargs):
        raise AssertionError("audit must not access network")
    monkeypatch.setattr(bot.requests, "get", reject)


@pytest.mark.parametrize("query", ["查询", "信号", "实时信号"])
def test_coordinator_unknown_year_keeps_both_snapshots(tmp_path, monkeypatch, query):
    store, _current, _signals = prepare(tmp_path, monkeypatch)
    monkeypatch.setenv("ICIM_STATE_DIR", str(tmp_path / "import_only"))
    server = importlib.import_module("poe_ic_im_v1_4_server")
    coordinator = server.LedgerCoordinator(store)
    before = {p.relative_to(store.root).as_posix(): p.read_bytes()
              for p in store.root.rglob("*.json")}
    text, files, observed = coordinator.execute_query(
        query, datetime(2027, 1, 4, 16, tzinfo=bot.BEIJING))
    assert "查询失败" not in text
    assert text.count("最后逐腿核验快照") == 2
    assert {"IC", "IM"} == set(observed)
    assert all(s["diagnostic_only"] is True for s in observed.values())
    assert before == {p.relative_to(store.root).as_posix(): p.read_bytes()
                      for p in store.root.rglob("*.json")}


@pytest.mark.parametrize("product", ["IC", "IM"])
@pytest.mark.parametrize("forged", [True, "True", 1, np.bool_(True)])
def test_grid_confirmation_is_recomputed_when_producer_claims_complete(tmp_path, monkeypatch, product, forged):
    _store, _current, signals = prepare(tmp_path, monkeypatch)
    signal = unavailable_fear(signals[product])
    signal.update(signal_complete=True, persistence_eligible=True,
                  grid_target_confirmed=forged, total_units_target_confirmed=forged)
    with pytest.raises(RuntimeError, match="网格目标未确认"):
        state.require_persistence_eligible(signal, product)


@pytest.mark.parametrize("product", ["IC", "IM"])
def test_roll_wait_survives_disallowed_seller_conditions(monkeypatch, product):
    from test_icim_round3_policy_repairs_20261021 import short_anchor
    anchor, frame = wire_adapter(monkeypatch, product, short_anchor(product), mark=.10)
    signal = _signal(product, market_date=date(2026, 12, 1), next_trade_date=date(2026, 12, 2),
                     valuation_tier=2, valuation_puts_per_full_core=2, core_eod_contract=product+"2612")
    monkeypatch.setattr(bot, "_v14_ic_short_put_candidate", lambda *a, **k: pytest.fail("unused candidate"))
    monkeypatch.setattr(bot, "_v14_im_short_put_candidate", lambda *a, **k: pytest.fail("unused candidate"))
    before = deepcopy(anchor)
    out = bot._apply_v14_live_policy(product, signal, mo_quotes=frame)
    assert out["v14_action"] == "WAIT_SHORT_PUT_ROLL"
    assert out["v14_roll_pending"] is True
    assert out["v14_short_put_contract"] == anchor["v14_short_put_contract"]
    assert anchor == before


def test_admission_priority_avoids_unconsumed_profit_expiry(monkeypatch):
    """Seller admission precedes a newly triggered 3x plan in the real policy."""
    profit_preparer = bot._v14_prepare_profit_open_plan
    anchor = policy.default_extension("IM")
    anchor.update(v14_core_put_entry_premium=1., v14_core_put_profit3x_eligible=True)
    _anchor, frame = wire_adapter(monkeypatch, "IM", anchor)
    monkeypatch.setattr(bot, "_v14_prepare_profit_open_plan", profit_preparer)
    monkeypatch.setattr(bot, "_v14_option_mark", lambda *a: (3.5, None))
    def unused_profit_selection(*args):
        bot._require_model_expiry_calendar(date(2027, 3, 19), "unused profit candidate")
        return pd.Series(dict(instrument="MO2703-P-7200", lastprice=10.))
    monkeypatch.setattr(bot, "select_im_put_for_reset", unused_profit_selection)
    candidate = _candidate("IM", contract="MO2612-P-7200", expiry=date(2026, 12, 18))
    monkeypatch.setattr(bot, "_v14_im_short_put_candidate", lambda *a, **k: candidate)
    signal = _signal("IM", market_date=date(2026, 11, 2), next_trade_date=date(2026, 11, 3),
                     core_eod_contract="IM2612", core_put_current_contract="MO2612-P-7200",
                     core_put_current_qty_normalized=1.5, core_put_target_qty_normalized=1.5,
                     put_reference_price=7200.)
    context = {"expiries": [], "failures": [], "signal": signal, "future": {}, "anchor": anchor}
    token = bot._SIGNAL_CALENDAR_DIAGNOSTICS.set(context)
    try:
        out = bot._apply_v14_live_policy("IM", signal, mo_quotes=frame)
    finally:
        bot._SIGNAL_CALENDAR_DIAGNOSTICS.reset(token)
    assert out["v14_action"] == "ENTER_SHORT_PUT"
    assert context["expiries"] == [], "unused 3x reselection must not poison admitted short-Put route"


def test_reference_comparison_does_not_verify_actual_target():
    ref, expiry = date(2026, 12, 23), date(2027, 3, 19)
    assert bot._expiry_definitely_after_reference(expiry, ref)
    with pytest.raises(bot.CalendarCoverageError):
        bot.require_signal_calendar("IM", dict(market_date=ref, next_trade_date=date(2026,12,24),
                                               core_target="IM2703", core_current="IM2703"))


def _frozen_policy():
    name = "_round3_adversarial_prior_policy"
    if name not in sys.modules:
        path = Path(__file__).parent / "quant_research_runs/20261021_expert_round3_repairs_v1/backup/ic_im_v1_4_policy.py"
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


@pytest.mark.parametrize("product", ["IC", "IM"])
@pytest.mark.parametrize("route", ["future", "short_put", "cash_wait", "assigned_etf", "recovery_future"])
@pytest.mark.parametrize("tier,momentum", [(0,.01),(1,0.),(2,.01),(0,-.01)])
@pytest.mark.parametrize("close", [False,True])
def test_candidate_fetch_plan_preserves_frozen_policy_trade_state(product, route, tier, momentum, close):
    """Compare optimized candidate consumption against the frozen implementation."""
    from test_icim_round3_policy_repairs_20261021 import short_anchor
    prior = _frozen_policy()
    anchor = (short_anchor(product) if route == "short_put" else policy.default_extension(product))
    anchor["v14_route_state"] = route
    signal = _signal(product, market_date=date(2026,12,1), next_trade_date=date(2026,12,2),
                     close_confirmed=close, valuation_tier=tier, valuation_puts_per_full_core=tier,
                     momentum_120=momentum, v14_short_put_mark=.1,
                     core_eod_contract=product+"2612", v14_recovery_net_pnl_mark=-1.)
    candidate = _candidate(product, contract="510500P2612M07500" if product=="IC" else "MO2612-P-7200",
                           expiry=date(2026,12,23 if product=="IC" else 18))
    expected = prior.apply_policy(product, signal, anchor, candidate=candidate, roll_candidate=candidate)
    required = policy.short_put_candidate_requirements(product, signal, anchor)
    actual = policy.apply_policy(product, signal, anchor,
                                 candidate=candidate if required["entry"] else {"tradable":False},
                                 roll_candidate=candidate if required["roll"] else None)
    keys = set(policy.default_extension(product)) | {"v14_action", "core_units_target", "total_units_target",
             "put_target_core_qty", "core_put_target_qty_normalized", "call_target_contract"}
    keys.discard("v14_roll_wait_reason")  # Disclosure reason may now describe an omitted probe.
    assert {k:actual.get(k) for k in keys} == {k:expected.get(k) for k in keys}
