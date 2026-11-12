"""True saved-master evidence and deliberately mutated expiry contracts."""
from copy import deepcopy
from datetime import date, datetime, timedelta
import json
from pathlib import Path

import pandas as pd
import pytest
import requests

import ic_im_expiry_evidence_v1 as expiry
import ic_im_evidence_adapter_v1 as adapter
import ic_im_signal_evidence_v1 as evidence
import poe_ic_im_mainline_v1_4_bot as bot

ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "tests/fixtures/icim_external_review_round2_20261021"
MASTER = json.loads((FIXTURES / "sse_contract_master.json").read_text(encoding="utf-8"))
ASOF = date(2026, 10, 20)
EXPIRY = date(2026, 12, 23)


def chain_from_saved_master(master=MASTER):
    rows = [dict(contract=r["CONTRACT_ID"], security_id=r["SECURITY_ID"],
                 strike=float(r["EXERCISE_PRICE"]), last=float(r["SECURITY_CLOSEPX"]))
            for r in master["result"]]
    return pd.DataFrame(rows)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def blocked(*a, **kw):
        pytest.fail("expiry contracts forbid external HTTP")
    monkeypatch.setattr(requests.sessions.Session, "request", blocked)


def test_all_58_real_master_records_have_consistent_expiry():
    result = expiry.verify_sse_chain(chain_from_saved_master(), MASTER,
                                    as_of=ASOF, month="202612", model_expiry=EXPIRY)
    assert len(result) == 58 and set(result.expiry) == {EXPIRY}
    assert result.attrs["expiry_evidence"]["as_of"] == "2026-10-20"
    assert not result.attrs["expiry_evidence"]["delivery_date_used_as_expiry"]


@pytest.mark.parametrize("field", ["EXPIRE_DATE", "EXERCISE_DATE", "END_DATE"])
def test_each_actual_expiry_field_conflict_is_rejected(field):
    payload = deepcopy(MASTER)
    payload["result"][0][field] = "20261224"
    with pytest.raises(expiry.ExpiryEvidenceConflict):
        expiry.verify_sse_chain(chain_from_saved_master(), payload, as_of=ASOF,
                                month="202612", model_expiry=EXPIRY)


@pytest.mark.parametrize("change", ["all_dates", "missing_date", "duplicate_id", "wrong_scope", "wrong_strike"])
def test_master_conflicts_are_rejected(change):
    payload = deepcopy(MASTER)
    row = payload["result"][0]
    if change == "all_dates":
        for key in ["EXPIRE_DATE", "EXERCISE_DATE", "END_DATE"]:
            row[key] = "20261224"
    elif change == "missing_date":
        del row["EXPIRE_DATE"]
    elif change == "duplicate_id":
        payload["result"].append(deepcopy(row))
    elif change == "wrong_scope":
        payload["sqlId"] = "untrusted"
    else:
        row["EXERCISE_PRICE"] = "999"
    with pytest.raises(expiry.ExpiryEvidenceConflict):
        expiry.verify_sse_chain(chain_from_saved_master(), payload, as_of=ASOF,
                                month="202612", model_expiry=EXPIRY)


def test_real_prior_day_master_is_unavailable_on_next_day():
    with pytest.raises(expiry.ExpiryMasterUnavailable, match="TIMESAVE"):
        expiry.verify_sse_chain(chain_from_saved_master(), MASTER,
                                as_of=date(2026, 10, 21), month="202612", model_expiry=EXPIRY)


def test_delivery_day_cannot_replace_expiry():
    with pytest.raises(expiry.ExpiryEvidenceConflict):
        expiry.verify_sse_chain(chain_from_saved_master(), MASTER, as_of=ASOF,
                                month="202612", model_expiry=date(2026, 12, 24))


def setup_real_chain_consumer(monkeypatch, payload=MASTER):
    # Controlled transport date only; original fixture bytes are never changed.
    raw = json.loads((FIXTURES / "sse_december_identity.json").read_text(encoding="utf-8"))
    raw["date"] = 20261020
    monkeypatch.setattr(bot, "_sse_quote_json", lambda *a, **kw: raw)
    monkeypatch.setattr(expiry, "EFFECTIVE_SIGNAL_DATE", ASOF)
    monkeypatch.setattr(bot, "fetch_sse_510500_expiry_master", lambda *a, **kw: payload, raising=False)
    return bot._OPTION_CHAIN_REFERENCE_DAY.set(ASOF)


def test_real_chain_consumer_calls_the_master_validator(monkeypatch):
    token = setup_real_chain_consumer(monkeypatch)
    try:
        frame, stamp = bot.fetch_sse_510500_chain("2612")
    finally:
        bot._OPTION_CHAIN_REFERENCE_DAY.reset(token)
    assert frame.attrs["expiry_evidence"]["actual_expiry"] == "2026-12-23"
    assert set(frame.expiry) == {EXPIRY}


def test_real_chain_consumer_rejects_conflicting_master(monkeypatch):
    payload = deepcopy(MASTER)
    payload["result"][0]["EXPIRE_DATE"] = "20261224"
    token = setup_real_chain_consumer(monkeypatch, payload)
    try:
        with pytest.raises(expiry.ExpiryEvidenceConflict):
            bot.fetch_sse_510500_chain("2612")
    finally:
        bot._OPTION_CHAIN_REFERENCE_DAY.reset(token)


def test_conflict_cannot_be_hidden_by_sina_fallback(monkeypatch):
    def conflict(*a):
        raise expiry.ExpiryEvidenceConflict("actual expiry conflict")
    monkeypatch.setattr(bot, "fetch_sse_510500_chain", conflict)
    monkeypatch.setattr(bot, "fetch_sina_510500_chain", lambda *a: pytest.fail("conflict was masked"))
    with pytest.raises(expiry.ExpiryEvidenceConflict):
        bot.fetch_510500_chain_with_failover("2612")


def test_missing_master_can_use_dated_sina_fallback(monkeypatch):
    def unavailable(*a):
        raise expiry.ExpiryMasterUnavailable("missing current master")
    frame = pd.DataFrame([dict(contract="510500P2612M06250", strike=6.25, last=.1)])
    monkeypatch.setattr(bot, "fetch_sse_510500_chain", unavailable)
    monkeypatch.setattr(bot, "fetch_sina_510500_chain", lambda *a: (frame, {"source": "Sina"}))
    assert bot.fetch_510500_chain_with_failover("2612")[0].equals(frame)


def test_old_market_day_does_not_request_new_master(monkeypatch):
    raw = json.loads((FIXTURES / "sse_december_identity.json").read_text(encoding="utf-8"))
    monkeypatch.setattr(bot, "_sse_quote_json", lambda *a, **kw: raw)
    monkeypatch.setattr(bot, "fetch_sse_510500_expiry_master", lambda *a: pytest.fail("legacy backfill"), raising=False)
    token = bot._OPTION_CHAIN_REFERENCE_DAY.set(date(2026, 10, 21))
    try:
        frame, _ = bot.fetch_sse_510500_chain("2612")
    finally:
        bot._OPTION_CHAIN_REFERENCE_DAY.reset(token)
    assert "expiry_evidence" not in frame.attrs


@pytest.mark.parametrize("year,month,expected", [(2024, 2, date(2024, 2, 19)),
                                                   (2026, 6, date(2026, 6, 22)),
                                                   (2026, 11, date(2026, 11, 20))])
def test_cffex_independent_rule_handles_exchange_holidays(year, month, expected):
    proof = expiry.verify_cffex_rule_date(year, month, expected, bot._is_exchange_trading_day)
    assert proof["expiry"] == expected.isoformat()
    assert proof["independent_monthly_notice_verified"] is False


def test_cffex_wrong_model_day_is_rejected():
    with pytest.raises(expiry.ExpiryEvidenceConflict):
        expiry.verify_cffex_rule_date(2026, 11, date(2026, 11, 21), lambda d: d.weekday() < 5)


def test_production_cffex_rule_checks_independent_of_roll_helper(monkeypatch):
    monkeypatch.setattr(bot, "_roll_forward_exchange_day", lambda d: d + timedelta(days=1))
    with bot.runtime_clock(datetime(2026, 11, 12, 18, tzinfo=bot.BEIJING)):
        with pytest.raises(expiry.ExpiryEvidenceConflict):
            bot._third_friday(2026, 11)


def test_public_builder_converts_expiry_conflict_to_diagnostic(monkeypatch):
    def conflict(*a, **kw):
        raise expiry.ExpiryEvidenceConflict("expiry contradiction")
    monkeypatch.setattr(bot, "_build_live_trade_signal", conflict)
    monkeypatch.setattr(bot, "_SIGNAL_OBSERVER", None)
    result = bot.build_live_trade_signal("IC", datetime(2026, 11, 12, 18, tzinfo=bot.BEIJING), "close")
    assert result["diagnostic_status"] == "expiry_evidence_conflict"
    assert not result["persistence_eligible"] and result["diagnostic_only"]


def test_master_transport_uses_the_observed_month_parameters(monkeypatch):
    observed = {}
    class Response:
        content = (FIXTURES / "sse_contract_master.json").read_bytes()
        headers = {}
        closed = False
        def raise_for_status(self):
            pass
        def iter_content(self, chunk_size=8192):
            yield self.content
        def close(self):
            self.closed = True
    response = Response()
    def get(url, **kwargs):
        observed.update(url=url, **kwargs)
        return response
    monkeypatch.setattr(bot, "_evidence_http_get", get)
    payload = bot.fetch_sse_510500_expiry_master("2612", ASOF)
    assert payload == MASTER and response.closed
    assert observed["url"] == "https://query.sse.com.cn/commonQuery.do"
    assert observed["params"]["expireDate"] == "202612"
    assert observed["params"]["securityId"] == "510500"
    assert "queryDate" not in observed["params"]
    assert observed["params"]["sqlId"] == bot.adjusted_put_migration.MASTER_SQL


def test_master_json_duplicate_fields_fail_closed(monkeypatch):
    class Response:
        headers = {}
        closed = False
        def raise_for_status(self):
            pass
        def iter_content(self, chunk_size=8192):
            yield b'{"result":[],"result":[{}]}'
        def close(self):
            self.closed = True
    response = Response()
    monkeypatch.setattr(bot, "_evidence_http_get", lambda *a, **kw: response)
    with pytest.raises(expiry.ExpiryEvidenceConflict, match="重复"):
        bot.fetch_sse_510500_expiry_master("2612", ASOF)
    assert response.closed


def test_actual_saved_stale_master_falls_back_to_actual_sina_expiry_fields(monkeypatch, tmp_path):
    old = ROOT / "tests/fixtures/icim_external_review_20261021"
    quote_payload = (old / "sina_december_quotes.bin").read_bytes()
    packets = bot._exact_quote_variables(quote_payload, "hq_str_CON_OP_")
    latest_stamp = max(datetime.strptime(value.split(",")[32], "%Y-%m-%d %H:%M:%S")
                       for value in packets.values()).replace(tzinfo=bot.BEIJING)
    source_chain = json.loads((FIXTURES / "sse_december_identity.json").read_text(encoding="utf-8"))
    monkeypatch.setattr(bot, "_sse_quote_json", lambda *a, **kw: source_chain)
    monkeypatch.setattr(bot, "fetch_sse_510500_expiry_master", lambda *a: MASTER)
    monkeypatch.setattr(expiry, "EFFECTIVE_SIGNAL_DATE", ASOF)
    monkeypatch.setattr(bot, "_public_quote_bytes", lambda *a, **kw: (old / "sina_december_listing.bin").read_bytes())
    monkeypatch.setattr(bot, "_sina_put_detail_batches", lambda ids: list(packets.items()))
    clock = latest_stamp + timedelta(seconds=1)
    monkeypatch.setattr(evidence, "_now", lambda: clock)
    archive = tmp_path / "sina_archive"
    token = bot._OPTION_CHAIN_REFERENCE_DAY.set(date(2026, 10, 21))
    try:
        with bot.runtime_clock(clock), evidence.capture_session(archive, clock.date(), clock, {}) as session:
            frame, stamp = bot._fetch_510500_chain_for_day("2612", clock.date())
    finally:
        bot._OPTION_CHAIN_REFERENCE_DAY.reset(token)
    assert len(packets) == 29 and len(frame) == 15 and set(frame.expiry) == {EXPIRY}
    assert len(frame.attrs["unresolved_adjusted_contracts"]) == 14
    assert "新浪" in stamp["source"]
    assert session.context["complete"] is True
    reader = evidence.load_session(archive, clock.date())
    monkeypatch.setattr(bot, "_public_quote_bytes", lambda *a, **kw: pytest.fail("Sina replay backfill"))
    with bot.runtime_clock(clock), evidence.replay_session(reader):
        replayed, _ = bot._fetch_510500_chain_for_day("2612", clock.date())
    def values(value):
        return [{key: None if pd.isna(item) else item.isoformat()
                 if isinstance(item, (date, datetime)) else item for key, item in row.items()}
                for row in value.to_dict("records")]
    # The codec may rebuild an equivalent fixed-offset pandas timezone dtype.
    assert values(replayed) == values(frame) and reader.missing_inputs == []


@pytest.mark.parametrize("kind", ["mixed", "mixed_with_conflict", "uniform_future"])
def test_master_mixed_or_future_days_are_hard_conflicts(kind):
    payload = deepcopy(MASTER)
    if kind == "uniform_future":
        for row in payload["result"]:
            row["TIMESAVE"] = "2026-10-21"
    else:
        payload["result"][1]["TIMESAVE"] = "2026-10-19"
        if kind == "mixed_with_conflict":
            payload["result"][0]["EXPIRE_DATE"] = "20261224"
    with pytest.raises(expiry.ExpiryEvidenceConflict):
        expiry.verify_sse_chain(chain_from_saved_master(), payload, as_of=ASOF,
                                month="202612", model_expiry=EXPIRY)


def test_sse_producer_captures_raw_proof_and_replay_requires_no_HTTP(monkeypatch, tmp_path):
    # Artificial acquisition clock/day on saved literal provider fields.
    clock = datetime(2026, 10, 20, 18, tzinfo=bot.BEIJING)
    raw = json.loads((FIXTURES / "sse_december_identity.json").read_text(encoding="utf-8"))
    raw.update(date=20261020, time=150000)
    calls = []
    def master(*a):
        calls.append(a)
        return deepcopy(MASTER)
    monkeypatch.setattr(bot, "_sse_quote_json", lambda *a, **kw: raw)
    monkeypatch.setattr(bot, "fetch_sse_510500_expiry_master", master)
    monkeypatch.setattr(evidence, "_now", lambda: clock)
    monkeypatch.setattr(expiry, "EFFECTIVE_SIGNAL_DATE", ASOF)
    root = tmp_path / "archive"
    with bot.runtime_clock(clock), evidence.capture_session(root, ASOF, clock, {}) as session:
        frame, stamp = bot._fetch_510500_chain_for_day("2612", ASOF)
        bot._fetch_510500_chain_for_day("2612", ASOF)
    assert len(calls) == 1 and session.context["complete"] is True
    assert {r["role"] for r in session.context["entries"].values()} == {"ic.expiry", "ic.chain"}
    reader = evidence.load_session(root, ASOF)
    monkeypatch.setattr(bot, "fetch_sse_510500_expiry_master", lambda *a: pytest.fail("replay supplemented HTTP"))
    with bot.runtime_clock(clock), evidence.replay_session(reader):
        replayed, _ = bot._fetch_510500_chain_for_day("2612", ASOF)
    assert replayed.equals(frame) and reader.missing_inputs == []


@pytest.mark.parametrize("mode", ["missing", "wrong_month", "wrong_expiry", "forged_attrs"])
def test_new_day_sealed_chain_cannot_bypass_raw_proof(monkeypatch, tmp_path, mode):
    clock = datetime(2026, 10, 20, 18, tzinfo=bot.BEIJING)
    monkeypatch.setattr(evidence, "_now", lambda: clock)
    monkeypatch.setattr(expiry, "EFFECTIVE_SIGNAL_DATE", ASOF)
    frame = expiry.verify_sse_chain(chain_from_saved_master(), MASTER, as_of=ASOF,
                                    month="202612", model_expiry=EXPIRY)
    stamp = {"date": "20261020", "time": "150000", "source": "上交所行情接口"}
    proof = {"revision": expiry.REVISION, "provider": "SSE", "day": ASOF.isoformat(),
             "month": "202612", "payload": deepcopy(MASTER)}
    if mode == "wrong_expiry":
        frame["expiry"] = date(2026, 12, 24)
    if mode == "forged_attrs":
        frame.attrs["expiry_evidence"] = {"source": "SSE_dated_contract_master", "master_sha256": "0"*64}
    root = tmp_path / "archive"
    with evidence.capture_session(root, ASOF, clock, {}) as session:
        if mode in {"wrong_month", "wrong_expiry"}:
            session.call("ic.expiry", {"month": "2703" if mode == "wrong_month" else "2612", "day": ASOF}, lambda: proof)
        session.call("ic.chain", {"month": "2612", "day": ASOF}, lambda: (frame, stamp))
    reader = evidence.load_session(root, ASOF)
    with bot.runtime_clock(clock), evidence.replay_session(reader):
        with pytest.raises((evidence.EvidenceUnavailable, expiry.ExpiryEvidenceConflict)):
            bot._fetch_510500_chain_for_day("2612", ASOF)


def test_duplicate_sse_quote_cannot_omit_another_master_identity():
    frame = chain_from_saved_master().iloc[1:]
    with pytest.raises(expiry.ExpiryEvidenceConflict, match="证券集合"):
        expiry.verify_sse_chain(frame, MASTER, as_of=ASOF, month="202612", model_expiry=EXPIRY)


def test_profit_preselection_preserves_specific_expiry_conflict(monkeypatch):
    def conflict(*a, **kw):
        raise expiry.ExpiryEvidenceConflict("profit target expiry contradiction")
    monkeypatch.setattr(bot, "select_ic_put_for_reset", conflict)
    signal = {"market_date": date(2026, 11, 12), "close_confirmed": True,
              "v14_core_put_mark": .4, "put_target_core_qty": 1,
              "etf_price": 7., "future_last": 6600., "core_put_target_delta": .25}
    anchor = {"v14_route_state": "future", "v14_core_put_profit3x_eligible": True,
              "v14_core_put_entry_premium": .1}
    with pytest.raises(expiry.ExpiryEvidenceConflict):
        bot._v14_prepare_profit_open_plan("IC", signal, anchor, None)


def saved_sina_proof(monkeypatch, stale_style=None):
    old = ROOT / "tests/fixtures/icim_external_review_20261021"
    packets = bot._exact_quote_variables((old / "sina_december_quotes.bin").read_bytes(), "hq_str_CON_OP_")
    changed_id = None
    if stale_style is not None:
        for security, text in packets.items():
            fields = text.split(",")
            if fields[43] == stale_style:
                fields[32] = "2026-10-20 13:28:00"
                packets[security] = ",".join(fields)
                changed_id = security
                break
    monkeypatch.setattr(bot, "_public_quote_bytes", lambda *a, **kw: (old / "sina_december_listing.bin").read_bytes())
    monkeypatch.setattr(bot, "_sina_put_detail_batches", lambda ids: list(packets.items()))
    frame, stamp = bot.fetch_sina_510500_chain("2612")
    day = date(2026, 10, 21)
    clock = max(frame.source_stamp) + timedelta(seconds=1)
    proof = {"revision": expiry.REVISION, "provider": "Sina", "day": day.isoformat(),
             "month": "202612", "listed_ids": sorted(packets), "packets": list(packets.items())}
    return frame, stamp, proof, day, clock, changed_id


@pytest.mark.parametrize("style", ["A", "M"])
def test_unconsumed_stale_sina_rows_remain_visible_without_refreshing_them(monkeypatch, style):
    frame, stamp, proof, day, clock, changed_id = saved_sina_proof(monkeypatch, style)
    verified = expiry.verify_recorded_chain(frame, stamp, proof, as_of=day, month="202612",
                                            observation_clock=clock, model_expiry=EXPIRY)
    assert len(verified) == 15
    if style == "M":
        row = verified.loc[verified.security_id.eq(changed_id)].iloc[0]
        assert row.source_stamp.date() == ASOF
        with pytest.raises(RuntimeError):
            bot._require_selected_510500_quote_time(row, clock, day, source=stamp["source"])
    else:
        item = next(r for r in verified.attrs["unresolved_adjusted_contracts"] if r["security_id"] == changed_id)
        assert item["source_stamp"].date() == ASOF


def test_sina_aggregate_time_cannot_be_forged_above_raw_packets(monkeypatch):
    frame, stamp, proof, day, clock, _ = saved_sina_proof(monkeypatch)
    stamp["time"] = (clock + timedelta(minutes=1)).strftime("%H%M%S")
    with pytest.raises(expiry.ExpiryEvidenceConflict, match="总体时刻"):
        expiry.verify_recorded_chain(frame, stamp, proof, as_of=day, month="202612",
                                    observation_clock=clock + timedelta(minutes=2), model_expiry=EXPIRY)


def test_caught_proof_conflict_still_invalidates_capture_and_bound_signal(monkeypatch, tmp_path):
    clock = datetime(2026, 10, 20, 18, tzinfo=bot.BEIJING)
    monkeypatch.setattr(evidence, "_now", lambda: clock)
    proof = {"revision": expiry.REVISION, "provider": "SSE", "day": ASOF.isoformat(),
             "month": "202612", "payload": deepcopy(MASTER)}
    signal = {"persistence_eligible": True}
    with evidence.capture_session(tmp_path / "archive", ASOF, clock, {}) as session:
        adapter.record_expiry_proof("2612", ASOF, proof)
        session.bind_signal(signal)
        changed = deepcopy(proof)
        changed["payload"]["result"][0]["EXPIRE_DATE"] = "20261224"
        with pytest.raises(expiry.ExpiryEvidenceConflict):
            adapter.record_expiry_proof("2612", ASOF, changed)
    assert session.context["complete"] is False
    assert signal["persistence_eligible"] is False
    assert any(r["role"] == "ic.expiry.conflict" and not r["resolved"] for r in session.failures)
