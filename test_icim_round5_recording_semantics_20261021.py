"""Follow-up review: scoped input identity, repeated HTTP and replay clocks.

Future market data/calendar/clocks and state seeds are synthetic. No production
ledger or current market endpoint is used by these tests.
"""
from copy import deepcopy
from datetime import date, datetime, timedelta
import importlib
import json
import random

import pandas as pd
import pytest
import requests

import ic_im_evidence_adapter_v1 as adapter
import ic_im_signal_evidence_v1 as evidence
import ic_im_v1_4_policy as policy
import poe_ic_im_mainline_v1_4_bot as bot
import poe_ic_im_v1_4_state as state
import test_icim_round3_recovery_audit_20261021 as seed
import test_icim_round4_recovery_20261021 as r4
from test_icim_round4_evidence_boundaries_20261021 import Response


DAY = date(2026, 10, 21)
CLOCK = datetime(2026, 10, 21, 18, tzinfo=bot.BEIJING)
URL = "https://yunhq.sse.com.cn:32042/v1/sho/list/tstyle/510500_01"
PARAMS = {"select": "contractid,last,chg_rate,presetpx,exepx,code", "order": "contractid,ase"}


def raw_archive(tmp_path, rows):
    with evidence.capture_session(tmp_path, DAY, CLOCK, {}) as session:
        session.call("probe", {}, lambda: True)
        for url, params, body, headers, status, encoding in rows:
            response = Response([body], headers=headers)
            response.status_code, response.encoding = status, encoding
            session.raw_response(url, params, response)
    if not session.context["complete"]:
        # Production now rejects the whole ambiguous archive at load time.
        # This test intentionally exercises http_response's additional in-memory
        # defense, so construct its malformed reader only after asserting that.
        with pytest.raises(evidence.EvidenceUnavailable, match="incomplete"):
            evidence.load_session(tmp_path, DAY)
        return evidence.EvidenceReader(session.root, session.manifest_path, session.context, session.manifest_sha256)
    return evidence.load_session(tmp_path, DAY)


@pytest.mark.parametrize("difference", ["body", "headers", "status", "encoding"])
def test_conflicting_same_url_parameters_are_rejected_instead_of_taking_last(tmp_path, difference):
    first = [URL, PARAMS, b"first", {"Last-Modified": "Wed, 21 Oct 2026 07:00:00 GMT"}, 200, "utf-8"]
    second = deepcopy(first)
    if difference == "body":
        second[2] = b"second"
    elif difference == "headers":
        second[3] = {"Last-Modified": "Wed, 21 Oct 2026 07:01:00 GMT"}
    elif difference == "status":
        second[4] = 503
    else:
        second[5] = "gbk"
    reader = raw_archive(tmp_path, [first, second])
    with pytest.raises(evidence.EvidenceUnavailable, match="ambiguous"):
        reader.http_response(URL, PARAMS)


def test_identical_repeat_http_is_reusable_without_consuming_invented_request_order(tmp_path):
    row = [URL, PARAMS, b"same original bytes", {}, 200, "utf-8"]
    reader = raw_archive(tmp_path, [row, row])
    for _ in range(3):
        result = reader.http_response(URL, PARAMS)
        assert result["body"] == row[2]
    assert reader.missing_inputs == []


@pytest.mark.parametrize("conflicting", [True, False])
def test_overlapping_sina_batches_must_agree_on_the_exact_projected_variable(tmp_path, conflicting):
    url = "https://hq.sinajs.cn/etag.php"
    one, two, three = "P_OP_mo2612P7200", "P_OP_mo2612P7300", "P_OP_mo2612P7400"
    def body(other, price):
        return f'var hq_str_{one}="{price}";\nvar hq_str_{other}="other";'.encode("ascii")
    rows = [[url, {"list": one + "," + two}, body(two, "one"), {}, 200, "utf-8"],
            [url, {"list": one + "," + three}, body(three, "two" if conflicting else "one"), {}, 200, "utf-8"]]
    reader = raw_archive(tmp_path, rows)
    if conflicting:
        with pytest.raises(evidence.EvidenceUnavailable, match="ambiguous"):
            reader.http_response(url, {"list": one})
    else:
        assert reader.http_response(url, {"list": one})["body"] == f'var hq_str_{one}="one";'.encode("ascii")


def test_annotation_is_encoded_once_and_capture_context_is_active(tmp_path):
    value = {"prefetch_gaps": [], "calendar_pending": [{"date": date(2027, 1, 1)}], "complete": False}
    assert evidence.current_session.get() is None
    with evidence.capture_session(tmp_path, DAY, CLOCK, {}) as session:
        assert evidence.current_session.get() is session
        session.call("probe", {}, lambda: True)
        session.annotate("replay_completeness", value)
    assert evidence.current_session.get() is None
    reader = evidence.load_session(tmp_path, DAY)
    assert evidence.decode(reader.context["annotations"]["replay_completeness"]) == value


def chain_payload(day, month="2701"):
    return {"date": day.strftime("%Y%m%d"), "time": 150000, "total": 1, "begin": 0, "end": 1,
        "list": [["510500P" + month + "M07500", .4, 0., .4, 7.5, "1002" + month]]}


def test_real_chain_adapter_coalesces_month_day_and_never_aliases_years(tmp_path, monkeypatch):
    day = date(2026, 11, 26)
    clock = r4.wall(monkeypatch, day)
    calls = []
    def get(url, params=None, **kwargs):
        assert evidence.current_session.get() is not None
        calls.append((url, deepcopy(params)))
        response = requests.Response()
        response.status_code, response.encoding = 200, "utf-8"
        response._content = json.dumps(chain_payload(day)).encode("utf-8")
        response._content_consumed = True
        return response
    monkeypatch.setattr(bot.requests, "get", get)
    with bot.runtime_clock(clock), evidence.capture_session(tmp_path, day, clock, {}):
        first = bot._fetch_510500_chain_for_day("2701", day)
        repeated = bot._fetch_510500_chain_for_day("2701", day)
    assert first[0].equals(repeated[0])
    assert calls == [(URL, PARAMS)]
    reader = evidence.load_session(tmp_path, day)
    assert len(reader.context["raw"]) == 1
    monkeypatch.setattr(bot.requests, "get", lambda *a, **k: pytest.fail("typed input replay escaped to HTTP"))
    with evidence.replay_session(reader), bot.runtime_clock(clock), bot.historical_replay(day):
        assert bot._fetch_510500_chain_for_day("2701", day)[0].contract.iloc[0] == "510500P2701M07500"
        assert bot._fetch_510500_chain_for_day("2701", day)[0].equals(first[0])
        assert reader.missing_inputs == []
        with pytest.raises(evidence.EvidenceUnavailable):
            bot._fetch_510500_chain_for_day("2601", day)
    assert len(reader.missing_inputs) == 1


def test_wrong_year_return_from_same_month_url_cannot_form_a_complete_chain_archive(tmp_path, monkeypatch):
    day = date(2026, 11, 26)
    clock = r4.wall(monkeypatch, day)
    def get(*a, **k):
        response = requests.Response()
        response.status_code, response.encoding = 200, "utf-8"
        response._content = json.dumps(chain_payload(day, "2701")).encode("utf-8")
        response._content_consumed = True
        return response
    monkeypatch.setattr(bot.requests, "get", get)
    monkeypatch.setattr(bot, "fetch_sina_510500_chain", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("synthetic fallback unavailable")))
    with bot.runtime_clock(clock), evidence.capture_session(tmp_path, day, clock, {}) as session:
        with pytest.raises(RuntimeError):
            bot._fetch_510500_chain_for_day("2601", day)
    assert session.context["complete"] is False


def held_record(tmp_path, previous, month):
    store, original = seed.temporary_store(tmp_path, previous)
    for key, value in list(original["products"]["IC"].items()):
        if isinstance(value, str) and value.startswith("510500P2612"):
            original["products"]["IC"][key] = value.replace("510500P2612", "510500P" + month)
        elif value == "10012099":
            original["products"]["IC"][key] = "1002" + month
    original["digest"] = state._digest(original)
    state._validate_record(original)
    store._atomic_write(store.journal_dir / f"000000-{previous}.json", original)
    store._atomic_write(store.latest_path, original)
    bot.install_runtime_anchors(state.anchors_from_record(original))
    return store, original


def restore_owner(tmp_path, monkeypatch, store, root):
    monkeypatch.setenv("ICIM_STATE_DIR", str(tmp_path / "import_only"))
    server = importlib.import_module("poe_ic_im_v1_4_server")
    owner = server.LedgerCoordinator(store)
    owner.evidence_root = root
    return owner


@pytest.mark.parametrize("day,previous,month", [
    (date(2026, 11, 23), date(2026, 11, 20), "2703"),
    (date(2026, 11, 26), date(2026, 11, 25), "2701"),
])
def test_existing_unknown_year_ic_put_collects_without_new_expiry_then_real_server_recovers(tmp_path, monkeypatch, day, previous, month):
    clock = r4.wall(monkeypatch, day)
    store, original = held_record(tmp_path / "state", previous, month)
    samples = r4.providers(monkeypatch, day)
    assert month in samples["IC"][4]  # Rule-shaped listing, never add a missing month for the test.
    monkeypatch.setattr(bot.requests, "get", lambda *a, **k: pytest.fail("unrecorded original/replay HTTP"))
    root = tmp_path / "evidence"
    with monkeypatch.context() as capture_patch:
        capture_patch.setattr(bot, "_build_live_trade_signal", lambda *a, **k: pytest.fail("independent collector invoked builder"))
        assert adapter.capture_daily(bot, root, clock)["status"] == "captured"
    reader, runtime = adapter.load_original(bot, root, "IC", day)
    assert any(evidence.decode(row["key"]).get("month") == month for row in reader.context["entries"].values())
    with adapter.evidence_location(root), bot.historical_replay(day):
        before = bot.build_live_trade_signal("IC", seed.clock(day), "close")
    assert before["diagnostic_only"] and not before["persistence_eligible"]
    assert store.load_latest()["digest"] == original["digest"]
    r4.oracle(monkeypatch)
    actual = r4.wall(monkeypatch, day + timedelta(days=1))
    for name in ["daily_valuation", "chinabond"]:
        monkeypatch.setattr(getattr(bot, name), "resolve", lambda *a, **k: pytest.fail("fresh resolve escaped original market/rate values"))
    monkeypatch.setattr(bot.fear_grid, "fetch_score", lambda *a, **k: pytest.fail("fresh Fear escaped original evidence"))
    monkeypatch.setattr(bot.fear_grid, "fetch_jiu_reference", lambda *a, **k: pytest.fail("fresh reference escaped original evidence"))
    owner = restore_owner(tmp_path, monkeypatch, store, root)
    assert owner.catch_up_once(actual), owner.last_refresh_error
    restored = store.load_latest()
    assert restored["verified_day"] == str(day)
    ic = restored["signals"]["IC"]
    assert not ic["option_monthly_reset_due"]
    assert ic["v14_core_put_contract"] == "510500P" + month + "M07500"
    assert ic["signal_complete"] and ic["persistence_eligible"]
    assert ic["original_evidence"]["unrecorded_inputs"] == []
    assert ic["valuation_provenance"] == samples["IC"][0]["valuation_provenance"]
    assert ic["signal_generation_timing"] == "retrospective_replay"
    assert datetime.fromisoformat(ic["signal_generated_at"]).date() == actual.date()


def test_nov23_ic_2701_not_in_rule_shaped_listing_never_becomes_a_formal_target(tmp_path, monkeypatch):
    day = date(2026, 11, 23)
    clock = r4.wall(monkeypatch, day)
    store, original = held_record(tmp_path / "state", date(2026, 11, 20), "2701")
    samples = r4.providers(monkeypatch, day)
    assert set(samples["IC"][4]) == {"2611", "2612", "2703", "2706"}
    monkeypatch.setattr(bot.requests, "get", lambda *a, **k: pytest.fail("unrecorded HTTP cannot repair missing held identity"))
    root = tmp_path / "evidence"
    assert adapter.capture_daily(bot, root, clock)["status"] == "captured"
    r4.oracle(monkeypatch)
    actual = r4.wall(monkeypatch, date(2026, 11, 24))
    owner = restore_owner(tmp_path, monkeypatch, store, root)
    assert owner.catch_up_once(actual) is False
    assert store.load_latest()["digest"] == original["digest"]
    assert "2701" in owner.last_refresh_diagnostic


def test_replay_freezes_real_collection_end_despite_outer_elapsed_query_clock(tmp_path, monkeypatch):
    day = date(2026, 11, 23)
    session = seed.archive(monkeypatch, tmp_path, "IC", day)
    reader, runtime = adapter.load_original(bot, tmp_path, "IC", day)
    assert runtime == reader.closed_at.astimezone(bot.BEIJING)
    assert runtime >= reader.decision_at and runtime.date() == day
    monkeypatch.setattr(bot.time_module, "monotonic", lambda: 9_000_000.)
    token = bot._COLLECTION_CLOCK_ANCHOR.set((seed.clock(day) - timedelta(minutes=1), 0.))
    try:
        with bot.historical_replay(day), evidence.replay_session(reader):
            assert bot._iv_observation_clock(runtime, collection_started=0.) == runtime
    finally:
        bot._COLLECTION_CLOCK_ANCHOR.reset(token)


@pytest.mark.parametrize("stamp_hour,stamp_minute,expected", [(15, 0, True), (14, 54, False), (18, 1, False)])
def test_close_quote_freshness_is_consistent_at_original_elapsed_and_replay_end(stamp_hour, stamp_minute, expected):
    stamp = CLOCK.replace(hour=stamp_hour, minute=stamp_minute)
    outcomes = []
    for observation in [CLOCK + timedelta(seconds=5), CLOCK + timedelta(seconds=40)]:
        try:
            bot._validate_iv_monitor_times(DAY, stamp, stamp, observation, True)
            outcomes.append(True)
        except ValueError:
            outcomes.append(False)
    assert outcomes == [expected, expected]


@pytest.mark.parametrize("product", ["IC", "IM"])
def test_none_and_untradable_roll_candidate_leave_unneeded_gate_actions_identical(product):
    from test_ic_im_v1_4_policy import _signal, _candidate
    from test_icim_round3_policy_repairs_20261021 import short_anchor
    rng = random.Random(20261021)
    checked = 0
    for _ in range(200):
        signal = _signal(product, market_date=date(2026, 12, 1), next_trade_date=date(2026, 12, 2),
                         core_eod_contract=product + "2612", v14_short_put_mark=rng.choice([.1, .99, None]))
        signal.update(close_confirmed=rng.choice([True, False]), valuation_tier=rng.randrange(4),
                      valuation_puts_per_full_core=rng.randrange(4), momentum_next_weight=rng.choice([0., .5]))
        contract = "510500P2612M07500" if product == "IC" else "MO2612-P-7200"
        anchor = short_anchor(product, v14_short_put_contract=contract) if rng.choice([True, False]) else policy.default_extension(product)
        if policy.short_put_mark_required(product, signal, anchor) and signal["v14_short_put_mark"] is None:
            # STRAT-01: an unknown held-leg decay decision is not an unused candidate.
            with pytest.raises(RuntimeError, match="held short Put"):
                policy.short_put_candidate_requirements(product, signal, anchor)
            with pytest.raises(RuntimeError, match="held short Put"):
                policy.apply_policy(product, signal, anchor, candidate=None)
            continue
        required = policy.short_put_candidate_requirements(product, signal, anchor)
        if required["entry"] or required["roll"]:
            continue
        candidate = _candidate(product, contract=contract, expiry=date(2026, 12, 23 if product == "IC" else 18))
        none = policy.apply_policy(product, signal, anchor, candidate=candidate, roll_candidate=None)
        blocked = policy.apply_policy(product, signal, anchor, candidate=candidate,
            roll_candidate={"tradable": False, "reason": required["reason"]})
        # The policy may explain an unavailable roll with a different reason;
        # the requested invariant is identical actions and financial state.
        reasons = {"v14_action_reason", "v14_roll_wait_reason"}
        assert {key: value for key, value in none.items() if key not in reasons} == {
            key: value for key, value in blocked.items() if key not in reasons}
        assert none["v14_action"] not in {"ENTER_SHORT_PUT", "ROLL_SHORT_PUT"}
        checked += 1
    assert checked >= 20
