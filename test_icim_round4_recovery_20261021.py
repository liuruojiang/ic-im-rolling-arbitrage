"""Independent collection -> public historical replay -> real temporary ledger.

Prices, future acquisition clocks and the 2027 calendar are synthetic. This
tests the real collector, rather than constructing its manifests by hand.
"""
from copy import deepcopy
from datetime import date, datetime, timedelta
import importlib
import json
import random

import pandas as pd
import pytest

import ic_im_evidence_adapter_v1 as adapter
import ic_im_signal_evidence_v1 as evidence
import ic_im_v1_4_policy as policy
import poe_ic_im_mainline_v1_4_bot as bot
import poe_ic_im_v1_4_state as state
import test_icim_round3_recovery_audit_20261021 as seed
from test_icim_round4_evidence_boundaries_20261021 import Response


def wall(monkeypatch, day):
    now = seed.fake_wall(monkeypatch, day)
    class Wall(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz) if tz else now.replace(tzinfo=None)
    monkeypatch.setattr(adapter, "datetime", Wall)
    return now


def providers(monkeypatch, day):
    samples = {p: seed.values(p, day) for p in ["IC", "IM"]}
    monkeypatch.setattr(bot, "live_proxy", lambda product, clock=None: deepcopy(samples[product][0]))
    adapter._keyed(bot, bot, "live_proxy", "market.live", lambda product, clock=None: {"product": product})
    monkeypatch.setattr(bot, "fetch_cffex_quotes", lambda product, clock=None:
        deepcopy(samples["IM"][2] if product == "MO" else samples[product][1]))
    adapter._keyed(bot, bot, "fetch_cffex_quotes", "cffex.quotes", lambda product, clock=None: {"product": product})
    monkeypatch.setattr(bot, "_fear_score_evidence", lambda day, mode: deepcopy(samples["IM"][3]))
    adapter._keyed(bot, bot, "_fear_score_evidence", "fear.score", lambda day, mode: {"day": day, "mode": mode})
    monkeypatch.setattr(bot.fear_grid, "fetch_jiu_reference", lambda day:
        {"jiu_reference_only": True, "jiu_status": "reference_only", "jiu_fear_greed_index": 50., "jiu_data_date": str(day)})
    monkeypatch.setattr(bot, "fetch_sse_510500_expiries", lambda: ["20" + month for month in sorted(samples["IC"][4])])
    adapter._keyed(bot, bot, "fetch_sse_510500_expiries", "ic.months", lambda: {})
    monkeypatch.setattr(bot, "fetch_sse_510500_quote", lambda:
        {"date": day.strftime("%Y%m%d"), "time": "150000", "last": 7.8, "prev_close": 7.8})
    adapter._keyed(bot, bot, "fetch_sse_510500_quote", "ic.etf", lambda: {})
    monkeypatch.setattr(bot, "_fetch_510500_chain_for_day", lambda month, day: deepcopy(samples["IC"][4][month]))
    adapter._keyed(bot, bot, "_fetch_510500_chain_for_day", "ic.chain", lambda month, day: {"month": month, "day": day})
    monkeypatch.delenv(bot.chinabond.FILE_ENV, raising=False)
    return samples


def seed_store(tmp_path, previous, day):
    store, record = seed.temporary_store(tmp_path, previous)
    if day.month == 12:
        for product, anchor in record["products"].items():
            anchor["post_core_contract"] = product + "2703"
            for key, value in list(anchor.items()):
                if isinstance(value, str) and value.startswith("MO2612-"):
                    anchor[key] = value.replace("MO2612-", "MO2703-")
                if isinstance(value, str) and value.startswith("510500P2612"):
                    anchor[key] = value.replace("510500P2612", "510500P2703")
                if value == "10012099":
                    anchor[key] = "10022703"
        record["digest"] = state._digest(record)
        state._validate_record(record)
        store._atomic_write(store.journal_dir / f"000000-{previous}.json", record)
        store._atomic_write(store.latest_path, record)
    return store, store.load_latest()


def oracle(monkeypatch):
    monkeypatch.setattr(bot, "_OFFICIAL_EXCHANGE_CALENDAR_YEARS", bot._OFFICIAL_EXCHANGE_CALENDAR_YEARS | {2027})
    monkeypatch.setattr(bot, "_EXCHANGE_CLOSURES", bot._EXCHANGE_CLOSURES | {date(2027, 1, 1)})


@pytest.mark.parametrize("day,previous,later", [
    (date(2026, 11, 20), date(2026, 11, 19), date(2026, 11, 24)),
    (date(2026, 12, 31), date(2026, 12, 30), date(2027, 1, 5)),
])
@pytest.mark.parametrize("fear_missing", [False, True])
def test_real_independent_collector_survives_calendar_diagnostic_then_server_recovers(tmp_path, monkeypatch, day, previous, later, fear_missing):
    monkeypatch.setattr(bot.requests, "get", lambda *a, **k: pytest.fail("network escaped stored inputs"))
    now = wall(monkeypatch, day)
    store, record = seed_store(tmp_path / "state", previous, day)
    bot.install_runtime_anchors(state.anchors_from_record(record))
    samples = providers(monkeypatch, day)
    if fear_missing:
        samples["IM"][3].clear()
        samples["IM"][3].update({"fear_source_policy_revision": bot.fear_grid.SOURCE_POLICY_REVISION,
            "fear_greed_index": None, "fear_data_date": None, "fear_data_status": "unavailable",
            "fear_unavailable_reason": "synthetic original source outage", "fear_retrieved_at": now.isoformat(),
            "fear_fetch_started_at": now.isoformat(), "fear_available_sources": [], "fear_selected_source": None,
            "fear_cross_validation": "unavailable", "fear_source_warning": "synthetic outage",
            "fear_fallback_applied": False, "fear_csv_sha256": None, "fear_page_sha256": None,
            "fear_page_updated_at": None, "fear_page_data_date": None})
    builder = bot._build_live_trade_signal
    monkeypatch.setattr(bot, "_build_live_trade_signal", lambda *a, **k: pytest.fail("collector must not depend on signal conclusion"))
    root = tmp_path / "evidence"
    result = adapter.capture_daily(bot, root, now)
    assert result["status"] == "captured"
    for product in ["IC", "IM"]:
        reader, _ = adapter.load_original(bot, root, product, day)
        annotation = evidence.decode(reader.context["annotations"]["replay_completeness"])
        assert annotation["input_capture_complete"] is True
        assert annotation["missing_roles"] == []
        assert annotation["historical_supplements_needed"] == (["fear.score"] if fear_missing else [])
    monkeypatch.setattr(bot, "_build_live_trade_signal", builder)
    with adapter.evidence_location(root), bot.historical_replay(day):
        before = bot.build_live_trade_signal("IM", seed.clock(day), "close")
    assert before["diagnostic_only"] and before["persistence_eligible"] is False
    assert store.load_latest()["digest"] == record["digest"]
    oracle(monkeypatch)
    actual = wall(monkeypatch, later)
    if fear_missing:
        samples["IM"][3].clear()
        samples["IM"][3].update(fear_history(day, actual))
    monkeypatch.setattr(bot.fear_grid, "fetch_jiu_reference", lambda *a: pytest.fail("reference must be replayed"))
    monkeypatch.setenv("ICIM_STATE_DIR", str(tmp_path / "import_only"))
    server = importlib.import_module("poe_ic_im_v1_4_server")
    owner = server.LedgerCoordinator(store)
    owner.evidence_root = root
    assert owner.catch_up_once(actual)
    restored = store.load_latest()
    assert restored["verified_day"] == str(day) and restored["sequence"] == 1
    for product, signal in restored["signals"].items():
        assert signal["signal_complete"] and signal["persistence_eligible"]
        assert not signal.get("diagnostic_only") and not signal.get("calendar_provisional")
        assert signal["original_evidence"]["unrecorded_inputs"] == []
        if fear_missing:
            assert any(row["role"] == "fear.score" and row["original_close"] is False
                       for row in signal["historical_evidence_supplements"])
        if day.month == 11:
            assert signal["option_monthly_reset_due"] is True


def fear_history(day, fetched):
    return {"fear_source_policy_revision": bot.fear_grid.SOURCE_POLICY_REVISION,
        "fear_greed_index": 40., "fear_data_date": str(day), "fear_data_status": "retrospective_replay",
        "fear_retrieved_at": fetched.isoformat(), "fear_fetch_started_at": fetched.isoformat(),
        "fear_available_sources": ["csv"], "fear_selected_source": "csv", "fear_cross_validation": "single_source",
        "fear_source_warning": "synthetic historical CSV only", "fear_source_url": bot.fear_grid.CSV_URL,
        "fear_csv_sha256": "a" * 64, "fear_page_sha256": None, "fear_page_updated_at": None,
        "fear_page_data_date": None, "fear_page_value": None, "fear_csv_latest_date": str(fetched.date()),
        "fear_csv_latest_value": 42., "fear_csv_observations": {str(day): 40.},
        "fear_latest_available_date": str(fetched.date()), "fear_fallback_applied": False}


def test_unavailable_original_fear_recovers_with_separate_real_time_history_supplement(tmp_path, monkeypatch):
    day, later = date(2026, 11, 20), date(2026, 11, 24)
    seed.archive(monkeypatch, tmp_path, "IM", day)
    reader, runtime = adapter.load_original(bot, tmp_path, "IM", day)
    # Record the actual unavailable observation as its own original bundle.
    with evidence.capture_session(tmp_path, day, seed.clock(day), {"purpose": "synthetic missing Fear"}) as session:
        session.call("fear.score", {"day": day, "mode": "close"}, lambda: {"fear_greed_index": None, "fear_data_status": "unavailable"})
    missing = evidence.load_session(tmp_path, day, metadata_filter={"purpose": "synthetic missing Fear"})
    actual = wall(monkeypatch, later)
    calls = []
    monkeypatch.setattr(bot, "_fear_score_evidence", lambda d, mode: calls.append((d, mode)) or fear_history(d, actual))
    adapter._keyed(bot, bot, "_fear_score_evidence", "fear.score", lambda day, mode: {"day": day, "mode": mode})
    with evidence.replay_session(missing), bot.runtime_clock(runtime):
        value = bot._fear_score_evidence(day, "close")
        again = bot._fear_score_evidence(day, "close")
    assert value["fear_greed_index"] == again["fear_greed_index"] == 40.
    assert calls == [(day, "close")]
    assert missing.supplemental_evidence[0]["original_close"] is False
    assert missing.supplemental_evidence[0]["retrieved_at"] == actual.isoformat()
    assert missing.call("fear.score", {"day": day, "mode": "close"})["fear_greed_index"] is None
    supplement = evidence.load_session(tmp_path, day, metadata_filter={"capture_kind": "historical_fear_supplement"})
    assert supplement.metadata["original_close"] is False


def test_missing_fear_history_remains_unresolved_and_creates_no_complete_supplement(tmp_path, monkeypatch):
    day = date(2026, 11, 20)
    wall(monkeypatch, day)
    with evidence.capture_session(tmp_path, day, seed.clock(day), {"purpose": "synthetic unavailable"}) as session:
        session.call("fear.score", {"day": day, "mode": "close"}, lambda: {"fear_greed_index": None})
    reader = evidence.load_session(tmp_path, day)
    wall(monkeypatch, date(2026, 11, 24))
    monkeypatch.setattr(bot, "_fear_score_evidence", lambda *a: {"fear_greed_index": None})
    adapter._keyed(bot, bot, "_fear_score_evidence", "fear.score", lambda day, mode: {"day": day, "mode": mode})
    with evidence.replay_session(reader):
        result = bot._fear_score_evidence(day, "close")
    assert result["fear_greed_index"] is None
    assert result["fear_replay_recovery"] == "unavailable"
    assert str(day) in result["fear_unavailable_reason"] and "historical_replay" in result["fear_unavailable_reason"]
    with pytest.raises(evidence.EvidenceUnavailable):
        evidence.load_session(tmp_path, day, metadata_filter={"capture_kind": "historical_fear_supplement"})


def test_two_actual_independent_captures_while_stale_then_sequential_server_recovery(tmp_path, monkeypatch):
    monkeypatch.setattr(bot.requests, "get", lambda *a, **k: pytest.fail("unrecorded replay network"))
    first, second, previous = date(2026, 11, 20), date(2026, 11, 23), date(2026, 11, 19)
    store, original = seed_store(tmp_path / "state", previous, first)
    bot.install_runtime_anchors(state.anchors_from_record(original))
    root = tmp_path / "evidence"
    with monkeypatch.context() as acquisition:
        acquisition.setattr(bot, "_build_live_trade_signal", lambda *a, **k: pytest.fail("collector called signal builder"))
        for day in [first, second]:
            now = wall(acquisition, day)
            providers(acquisition, day)
            assert adapter.capture_daily(bot, root, now)["status"] == "captured"
            assert store.load_latest()["digest"] == original["digest"]
            for product in ["IC", "IM"]:
                reader, _ = adapter.load_original(bot, root, product, day)
                assert reader.metadata["anchor_day_at_capture"] == str(previous)
    oracle(monkeypatch)
    now = wall(monkeypatch, date(2026, 11, 24))
    monkeypatch.setenv("ICIM_STATE_DIR", str(tmp_path / "import_only"))
    server = importlib.import_module("poe_ic_im_v1_4_server")
    owner = server.LedgerCoordinator(store)
    owner.evidence_root = root
    assert owner.catch_up_until_current(now, max_sessions=2) == 2, owner.last_refresh_error
    assert store.load_latest()["verified_day"] == str(second)
    assert store.load_latest()["sequence"] == 2
    for product, signal in store.load_latest()["signals"].items():
        assert signal["signal_complete"] and signal["persistence_eligible"]
        assert signal["original_evidence"]["unrecorded_inputs"] == []


@pytest.mark.parametrize("original_archive", [True, False])
def test_legacy_ic_profit_rebuild_uses_original_chain_only_if_present(tmp_path, monkeypatch, original_archive):
    day = date(2026, 11, 20)
    wall(monkeypatch, day)
    oracle(monkeypatch)
    seed.archive(monkeypatch, tmp_path, "IC", day)
    reader, runtime = adapter.load_original(bot, tmp_path, "IC", day)
    monkeypatch.setattr(bot.requests, "get", lambda *a, **k: pytest.fail("legacy IC replay escaped stored chain"))
    signal = {"market_date": day, "close_confirmed": True, "option_monthly_reset_due": False,
        "etf_price": 7.8, "future_last": 7800., "core_put_target_delta": .25, "put_target_momentum_qty": 0}
    anchor = {"v14_route_state": "future", "v14_profit_pending": True,
        "v14_profit_execution_day": str(day), "v14_profit_trigger_day": "2026-09-25"}
    from contextlib import nullcontext
    with bot.runtime_clock(runtime), bot.historical_replay(day), (evidence.replay_session(reader) if original_archive else nullcontext()):
        bot._v14_prepare_lifecycle_evidence("IC", signal, anchor, None)
    if original_archive:
        assert signal["v14_profit_reentry_status"] == "confirmed_close_research_price"
        assert signal["v14_core_put_contract"].startswith("510500P2703")
        assert reader.missing_inputs == []
    else:
        assert signal["v14_profit_reentry_status"] == "blocked_historical_new_contract_chain_unavailable"


def test_batched_mo_original_crosschecks_replay_quote_and_open_without_http(tmp_path, monkeypatch):
    day = date(2026, 10, 21)
    clock = wall(monkeypatch, day)
    contracts = [f"MO2612-P-{6600+i}" for i in range(130)]
    frame = pd.DataFrame({"instrument": contracts})
    frame.attrs["listed_instruments"] = contracts
    calls = []
    def get(url, params, **kwargs):
        calls.append(params["list"])
        bodies = []
        for symbol in params["list"].split(","):
            fields = ["0"] * 41
            fields[1], fields[2], fields[3], fields[5], fields[7], fields[9] = "1.9", "2", "2.1", "10", symbol.split("P")[-1], "1"
            fields[32], fields[40] = "2026-10-21 15:00:00", "1"
            bodies.append(f'var hq_str_{symbol}="{",".join(fields)}";')
        return Response(["\n".join(bodies).encode("ascii")])
    monkeypatch.setattr(bot.requests, "get", get)
    with evidence.capture_session(tmp_path, day, clock, {"purpose": "synthetic complete MO verification"}):
        result = bot._prefetch_mo_replay_verification(frame, clock)
    assert len(calls) == 3 and len(result["symbols"]) == 130
    reader = evidence.load_session(tmp_path, day)
    monkeypatch.setattr(bot.requests, "get", lambda *a, **k: pytest.fail("verification escaped raw original evidence"))
    with evidence.replay_session(reader):
        assert bot.verify_sina_option_quote(contracts[0], day, 2., clock)["lastprice"] == 2.
        assert bot.verify_sina_option_open(contracts[-1], day, 1., clock)["openprice"] == 1.
    assert reader.missing_inputs == []


@pytest.mark.parametrize("fault", ["missing", "duplicate", "wrong_identity"])
def test_incomplete_mo_verification_batch_cannot_claim_complete_archive(tmp_path, monkeypatch, fault):
    day = date(2026, 10, 21)
    clock = wall(monkeypatch, day)
    frame = pd.DataFrame({"instrument": ["MO2612-P-7200", "MO2612-P-7300"]})
    symbols = ["P_OP_mo2612P7200", "P_OP_mo2612P7300"]
    if fault == "missing":
        symbols.pop()
    elif fault == "duplicate":
        symbols[1] = symbols[0]
    else:
        symbols[1] = "P_OP_mo2612P7400"
    body = "\n".join(f'var hq_str_{symbol}="";' for symbol in symbols).encode("ascii")
    monkeypatch.setattr(bot.requests, "get", lambda *a, **k: Response([body]))
    with evidence.capture_session(tmp_path, day, clock, {}) as session:
        with pytest.raises(RuntimeError, match="标识不完整"):
            bot._prefetch_mo_replay_verification(frame, clock)
    assert session.context["complete"] is False


@pytest.mark.parametrize("product", ["IC", "IM"])
def test_none_roll_candidate_and_random_gate_contract_are_real_policy_compatible(monkeypatch, product):
    from test_icim_round3_policy_repairs_20261021 import short_anchor, wire_adapter
    from test_ic_im_v1_4_policy import _signal, _candidate
    contract = "510500P2612M07500" if product == "IC" else "MO2612-P-7200"
    anchor, frame = wire_adapter(monkeypatch, product, short_anchor(product, v14_short_put_contract=contract), mark=.99)
    held = bot._apply_v14_live_policy(product, _signal(product, market_date=date(2026, 12, 1),
        next_trade_date=date(2026, 12, 2), core_eod_contract=product + "2612"), mo_quotes=frame)
    assert held["v14_action"] != "HOLD_FAIL_CLOSED"
    rng = random.Random(20261021)
    checked = 0
    for _ in range(200):
        signal = _signal(product, market_date=date(2026, 12, 1), next_trade_date=date(2026, 12, 2),
                         core_eod_contract=product + "2612", v14_short_put_mark=rng.choice([.1, .99, None]))
        signal.update(close_confirmed=rng.choice([True, False]), valuation_tier=rng.randrange(4),
                      valuation_puts_per_full_core=rng.randrange(4), momentum_next_weight=rng.choice([0., .5]))
        candidate = _candidate(product, contract=contract, expiry=date(2026, 12, 23 if product == "IC" else 18))
        active = anchor if rng.choice([True, False]) else policy.default_extension(product)
        if policy.short_put_mark_required(product, signal, active) and signal["v14_short_put_mark"] is None:
            # Missing required marks reject; keep these random inputs as negatives.
            with pytest.raises(RuntimeError, match="held short Put"):
                policy.short_put_candidate_requirements(product, signal, active)
            with pytest.raises(RuntimeError, match="held short Put"):
                policy.apply_policy(product, signal, active, candidate=candidate)
            continue
        requirements = policy.short_put_candidate_requirements(product, signal, active)
        if not requirements["entry"] and not requirements["roll"]:
            out = policy.apply_policy(product, signal, active, candidate=candidate, roll_candidate=candidate)
            assert out["v14_action"] not in {"ENTER_SHORT_PUT", "ROLL_SHORT_PUT"}
            checked += 1
    assert checked >= 20
