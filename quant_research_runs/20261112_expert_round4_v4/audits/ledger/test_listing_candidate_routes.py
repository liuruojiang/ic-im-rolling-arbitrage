"""Independent real policy->candidate->adapted archive-chain boundary tests.

Dates, contract months and selected premiums are explicitly synthetic controls.
SSE wire field structure and full master identity rows originate from saved
October fixtures; no modified fixture is presented as real later market data.
"""
from copy import deepcopy
from datetime import date, datetime, timedelta
import json
from pathlib import Path

import pandas as pd
import pytest
import requests

import poe_ic_im_mainline_v1_4_bot as bot
import ic_im_v1_4_policy as policy
import ic_im_signal_evidence_v1 as evidence
import ic_im_expiry_evidence_v1 as expiry

ROOT = Path(__file__).resolve().parents[4]
HERE = Path(__file__).resolve().parent
NEW_DAY = date(2026, 11, 12)
LISTED = ["202611", "202612", "202703", "202706"]
BASE = ROOT / "tests/fixtures/icim_external_review_round2_20261021"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(requests.sessions.Session, "request",
                        lambda *a, **k: pytest.fail("candidate-route audit forbids external HTTP"))
    monkeypatch.setattr(bot, "_SIGNAL_OBSERVER", None)


def market_inputs(day, month):
    master = json.loads((BASE / "sse_contract_master.json").read_text(encoding="utf-8"))
    template = json.loads((BASE / "sse_december_identity.json").read_text(encoding="utf-8"))
    raw_rows = bot.sse_identity.validate_chain_rows(template, "2612")
    target = f"26{month:02d}"
    expires = bot._fourth_wednesday(2026, month)
    identities = {}
    for index, row in enumerate(master["result"]):
        old = row["SECURITY_ID"]
        security = str(20000000 + month * 1000 + index)
        identities[old] = security
        row["SECURITY_ID"] = security
        row["CONTRACT_ID"] = row["CONTRACT_ID"][:7] + target + row["CONTRACT_ID"][11:]
        row["TIMESAVE"] = day.isoformat()
        for key in ["EXPIRE_DATE", "EXERCISE_DATE", "END_DATE"]:
            row[key] = expires.strftime("%Y%m%d")
    rows = []
    for row in raw_rows:
        row = list(row)
        row[0] = row[0][:7] + target + row[0][11:]
        row[5] = identities[row[5]]
        if row[0] == f"510500P{target}M06500":
            row[1] = .35
        rows.append(row)
    frame = bot._numeric_frame(pd.DataFrame(rows, columns=["contract", "last", "chg_rate", "pre_settle", "strike", "security_id"]),
                               skip=("contract", "security_id"))
    frame["expiry"] = expires
    stamp = {"date": day.strftime("%Y%m%d"), "time": "150000", "source": "上交所行情接口"}
    proof = {"revision": expiry.REVISION, "provider": "SSE", "day": day.isoformat(),
             "month": f"2026{month:02d}", "payload": master}
    raw = {"list": rows, "date": int(day.strftime("%Y%m%d")), "time": 150000,
           "total": len(rows), "begin": 0, "end": len(rows)}
    return frame, stamp, proof, raw


def configure_policy(monkeypatch, day, route):
    clock = datetime.combine(day, datetime.min.time(), bot.BEIJING).replace(hour=18)
    anchor = policy.default_extension("IC")
    anchor["last_verified_day"] = day - timedelta(days=1)
    anchor["post_core_contract"] = "IC2612"
    held = None
    if route == "roll":
        frame, stamp, proof, raw = market_inputs(day, day.month)
        name = f"510500P26{day.month:02d}M06500"
        row = frame.loc[frame.contract.eq(name)].iloc[0]
        frame.loc[frame.contract.eq(name), "last"] = .1
        held = (frame, stamp, proof, raw)
        anchor.update(v14_route_state="short_put", v14_cycle_id="independent-roll",
                      v14_short_put_contract=name, v14_short_put_security_id=str(row.security_id),
                      v14_short_put_qty_normalized=9.8, v14_short_put_entry_premium=.8,
                      v14_short_put_expiry=bot._fourth_wednesday(2026, day.month),
                      v14_short_put_cost_basis="confirmed_dated_open",
                      v14_short_put_entry_price_day=day - timedelta(days=1))
    signal = dict(product="IC", market_date=day, next_trade_date=day+timedelta(days=1),
                  close_confirmed=True, valuation_tier=1, valuation_puts_per_full_core=1,
                  momentum_120=.02, momentum_next_weight=1., option_monthly_reset_due=False,
                  core_units_current=.5, core_units_target=.5, total_units_current=1., total_units_target=1.,
                  momentum_put_target_delta=0., put_target_momentum_qty=0.,
                  momentum_put_target_qty_normalized=0., put_current_core_qty=0., put_target_core_qty=0.,
                  core_put_target_delta=0., core_put_driver="boundary-control", momentum_put_driver="boundary-control",
                  etf_price=6.871, future_last=6600.)
    monkeypatch.setitem(bot.LIVE_CONTINUATION_ANCHOR, "IC", anchor)
    # These unrelated long-Put/opening assembly stages have no active legs in
    # this fixture. Candidate, policy, held identity/marks and chain validators
    # below remain the actual production functions.
    monkeypatch.setattr(bot, "_v14_ic_core_overlay", lambda *a: None)
    monkeypatch.setattr(bot, "_v14_prepare_lifecycle_evidence", lambda *a: None)
    monkeypatch.setattr(bot, "_v14_option_mark", lambda *a: (None, None))
    monkeypatch.setattr(bot, "_gov10y_for_day", lambda *a: .02)
    observations = {"requirements": [], "candidates": []}
    original_required = policy.short_put_candidate_requirements
    def required(*args):
        value = original_required(*args)
        observations["requirements"].append(deepcopy(value))
        return value
    monkeypatch.setattr(policy, "short_put_candidate_requirements", required)
    original_candidate = bot._v14_ic_short_put_candidate
    def candidate(*args, **kwargs):
        observations["candidates"].append({"roll_from_expiry": str(kwargs.get("roll_from_expiry"))})
        return original_candidate(*args, **kwargs)
    monkeypatch.setattr(bot, "_v14_ic_short_put_candidate", candidate)
    return anchor, signal, held, clock, observations


def seal(root, day, clock, target, held, months, *, proof=True):
    with evidence.capture_session(root, day, clock, {"scope": "synthetic-real-policy-route"}) as session:
        for data in ([held] if held is not None else []) + ([target] if target is not None else []):
            frame, stamp, raw_proof, raw = data
            month = raw_proof["month"][-4:]
            session.call("ic.chain", {"month": month, "day": day}, lambda frame=frame, stamp=stamp: (frame, stamp))
            if day >= NEW_DAY and (proof or data is held):
                session.call("ic.expiry", {"month": month, "day": day}, lambda value=raw_proof: value)
        if months != "absent":
            session.call("ic.months", {}, lambda: months)
    return evidence.load_session(root, day)


def check_requirements(observed, route):
    assert len(observed["requirements"]) == 1
    required = observed["requirements"][0]
    assert required[route] is True and required["entry" if route == "roll" else "roll"] is False
    assert len(observed["candidates"]) == 1
    return required


@pytest.mark.parametrize("route", ["entry", "roll"])
@pytest.mark.parametrize("day", [date(2026, 10, 22), date(2026, 11, 11)])
@pytest.mark.parametrize("months", ["absent", ["202611"], None])
def test_old_required_entry_and_roll_never_consume_new_listing_role(monkeypatch, tmp_path, route, day, months):
    anchor, signal, held, clock, observed = configure_policy(monkeypatch, day, route)
    target = market_inputs(day, day.month + 1)
    monkeypatch.setattr(evidence, "_now", lambda: clock)
    reader = seal(tmp_path / "archive", day, clock, target, held, months)
    before = deepcopy(anchor)
    token = bot._HISTORICAL_REPLAY_DAY.set(day)
    try:
        with bot.runtime_clock(clock), evidence.replay_session(reader):
            result = bot._apply_v14_live_policy("IC", deepcopy(signal))
    finally:
        bot._HISTORICAL_REPLAY_DAY.reset(token)
    check_requirements(observed, route)
    assert result["v14_action"] == ("ENTER_SHORT_PUT" if route == "entry" else "ROLL_SHORT_PUT"), {k:v for k,v in result.items() if k in ["v14_candidate_data_status", "v14_action_reason"]}
    assert result["v14_short_put_contract"] == f"510500P26{day.month+1:02d}M06500"
    assert reader.missing_inputs == [] and anchor == before


@pytest.mark.parametrize("route", ["entry", "roll"])
@pytest.mark.parametrize("mode", ["valid", "missing_list", "bad_list", "missing_proof", "unlisted"])
def test_new_reader_policy_requires_original_listing_and_per_month_proof(monkeypatch, tmp_path, route, mode):
    anchor, signal, held, clock, observed = configure_policy(monkeypatch, NEW_DAY, route)
    target = market_inputs(NEW_DAY, 12)
    months = "absent" if mode == "missing_list" else [] if mode == "bad_list" else ["202611"] if mode == "unlisted" else LISTED
    monkeypatch.setattr(evidence, "_now", lambda: clock)
    reader = seal(tmp_path / "archive", NEW_DAY, clock,
                  None if mode == "unlisted" else target, held, months, proof=mode != "missing_proof")
    before = deepcopy(anchor)
    token = bot._HISTORICAL_REPLAY_DAY.set(NEW_DAY)
    try:
        with bot.runtime_clock(clock), evidence.replay_session(reader):
            result = bot._apply_v14_live_policy("IC", deepcopy(signal))
    finally:
        bot._HISTORICAL_REPLAY_DAY.reset(token)
    if mode == "missing_proof" and route == "roll":
        # The real held-identity archive scan checks every chain before policy
        # requirements, so this bad package is correctly rejected earlier.
        assert observed == {"requirements": [], "candidates": []}
        assert result["v14_held_put_identity_status"] == "unverified"
    else:
        check_requirements(observed, route)
    if mode == "valid":
        assert result["v14_action"] == ("ENTER_SHORT_PUT" if route == "entry" else "ROLL_SHORT_PUT"), {k:v for k,v in result.items() if k in ["v14_candidate_data_status", "v14_action_reason"]}
        assert reader.missing_inputs == []
    elif mode == "unlisted":
        assert result["v14_action"] == ("HOLD" if route == "entry" else "WAIT_SHORT_PUT_ROLL")
        assert "target_month_not_listed:202612" in result["v14_candidate_data_status"]
        assert not reader.missing_inputs
    else:
        assert result["v14_action"] == "HOLD_FAIL_CLOSED"
        assert "target_month_not_listed" not in result["v14_candidate_data_status"]
        assert "unavailable" in result["v14_candidate_data_status"]
    assert anchor == before


@pytest.mark.parametrize("route", ["entry", "roll"])
@pytest.mark.parametrize("error", [bot.SignalTransportError("transmission"), requests.Timeout("timeout"),
                                   requests.ConnectionError("connection"), requests.exceptions.ChunkedEncodingError("chunk")])
def test_new_live_transport_only_listing_failure_keeps_actual_chain_and_expiry_gates(monkeypatch, route, error):
    anchor, signal, held, clock, observed = configure_policy(monkeypatch, NEW_DAY, route)
    target = market_inputs(NEW_DAY, 12)
    data = {"2612": target}
    if held is not None:
        data["2611"] = held
        monkeypatch.setattr(bot, "fetch_option_closes", lambda *a, **k: pd.Series([.1], index=pd.to_datetime([NEW_DAY])))
    calls = []
    def quote(path, params):
        month = "26" + path.rsplit("_", 1)[-1]
        calls.append(["chain", month])
        return deepcopy(data[month][3])
    def master(month, reference):
        calls.append(["master", month, reference.isoformat()])
        return deepcopy(data[month][2]["payload"])
    def fail():
        raise error
    monkeypatch.setattr(bot, "_sse_quote_json", quote)
    monkeypatch.setattr(bot, "fetch_sse_510500_expiry_master", master)
    monkeypatch.setattr(bot, "fetch_510500_expiries_with_failover", fail)
    with bot.runtime_clock(clock):
        result = bot._apply_v14_live_policy("IC", deepcopy(signal))
    check_requirements(observed, route)
    assert result["v14_action"] == ("ENTER_SHORT_PUT" if route == "entry" else "ROLL_SHORT_PUT"), {k:v for k,v in result.items() if k in ["v14_candidate_data_status", "v14_action_reason"]}
    assert ["chain", "2612"] in calls and ["master", "2612", NEW_DAY.isoformat()] in calls


@pytest.mark.parametrize("error", [RuntimeError("bad listing structure"), evidence.EvidenceUnavailable("missing original"),
                                   requests.HTTPError("403 denied")])
def test_new_live_hard_listing_errors_do_not_call_direct_chain(monkeypatch, error):
    anchor, signal, held, clock, observed = configure_policy(monkeypatch, NEW_DAY, "entry")
    calls = []
    monkeypatch.setattr(bot, "fetch_510500_expiries_with_failover", lambda: (_ for _ in ()).throw(error))
    monkeypatch.setattr(bot, "_sse_quote_json", lambda *a, **k: calls.append("bad chain access"))
    with bot.runtime_clock(clock):
        result = bot._apply_v14_live_policy("IC", deepcopy(signal))
    check_requirements(observed, "entry")
    assert result["v14_action"] == "HOLD_FAIL_CLOSED" and not calls


def test_live_transport_fallback_cannot_mask_actual_expiry_conflict(monkeypatch):
    anchor, signal, held, clock, observed = configure_policy(monkeypatch, NEW_DAY, "entry")
    frame, stamp, proof, raw = market_inputs(NEW_DAY, 12)
    proof["payload"]["result"][0]["EXPIRE_DATE"] = "20261224"
    monkeypatch.setattr(bot, "fetch_510500_expiries_with_failover",
                        lambda: (_ for _ in ()).throw(requests.Timeout("listing failed")))
    monkeypatch.setattr(bot, "_sse_quote_json", lambda *a, **k: raw)
    monkeypatch.setattr(bot, "fetch_sse_510500_expiry_master", lambda *a: proof["payload"])
    monkeypatch.setattr(bot, "fetch_sina_510500_chain", lambda *a: pytest.fail("expiry conflict masked"))
    with bot.runtime_clock(clock), pytest.raises(expiry.ExpiryEvidenceConflict):
        bot._apply_v14_live_policy("IC", deepcopy(signal))
    check_requirements(observed, "entry")
