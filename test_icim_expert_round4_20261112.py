"""Fourth-round external-review contracts; all changed market inputs are controls."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import date, datetime, timedelta
import io
import json
from pathlib import Path

import pandas as pd
import pytest
import requests

import poe_ic_im_mainline_v1_4_bot as bot
import ic_im_expiry_evidence_v1 as expiry
import ic_im_signal_evidence_v1 as evidence
import test_ic_im_put_execution_repairs_20261016 as opening_fixtures

ROOT = Path(__file__).resolve().parent
DAY = date(2026, 11, 12)
CLOCK = datetime(2026, 11, 12, 18, tzinfo=bot.BEIJING)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*a, **kw):
        pytest.fail("review regression prohibits external HTTP")
    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    monkeypatch.setattr(bot, "_SIGNAL_OBSERVER", None)
    with bot.runtime_clock(CLOCK):
        yield


def candidate_inputs(monkeypatch, day):
    chain = pd.DataFrame([dict(contract="510500P2612M06500", security_id="10012149",
                               strike=6.5, last=.35, chg_rate=None, pre_settle=None)])
    events = []
    def source(month, reference):
        events.append((month, reference))
        return chain.copy(), {"date": reference.strftime("%Y%m%d"), "time": "150000", "source": "上交所行情接口"}
    monkeypatch.setattr(bot, "_fetch_510500_chain_for_day", source)
    monkeypatch.setattr(bot, "_gov10y_for_day", lambda *a: .02)
    signal = dict(market_date=day, next_trade_date=day+timedelta(days=1),
                  etf_price=6.871, future_last=6600.)
    return signal, events


@pytest.mark.parametrize("roll", [False, True])
def test_legacy_candidate_replay_does_not_add_listing_dependency(monkeypatch, tmp_path, roll):
    day = date(2026, 11, 11)
    clock = datetime(2026, 11, 11, 18, tzinfo=bot.BEIJING)
    real_adapter = bot._fetch_510500_chain_for_day
    signal, events = candidate_inputs(monkeypatch, day)
    chain, stamp = bot._fetch_510500_chain_for_day("2612", day)
    monkeypatch.setattr(bot, "_fetch_510500_chain_for_day", real_adapter)
    metadata = {"product": "IC", "original_close": True}
    monkeypatch.setattr(evidence, "_now", lambda: clock)
    with evidence.capture_session(tmp_path / "archive", day, clock, metadata) as session:
        session.call("ic.chain", {"month": "2612", "day": day}, lambda: (chain, stamp))
    reader = evidence.load_session(tmp_path / "archive", day)
    monkeypatch.setattr(bot, "fetch_510500_expiries_with_failover", lambda: reader.call("ic.months", {}))
    token = bot._HISTORICAL_REPLAY_DAY.set(day)
    try:
        with bot.runtime_clock(clock), evidence.replay_session(reader):
            candidate = bot._v14_ic_short_put_candidate(signal,
                roll_from_expiry=date(2026, 11, 25) if roll else None)
    finally:
        bot._HISTORICAL_REPLAY_DAY.reset(token)
    assert candidate["tradable"] and candidate["contract"] == "510500P2612M06500"
    assert len(events) == 1 and reader.missing_inputs == []


def test_new_live_listing_transport_failure_uses_validated_direct_chain(monkeypatch):
    signal, events = candidate_inputs(monkeypatch, date(2026, 11, 13))
    def unavailable():
        raise bot.SignalTransportError("listing transport outage")
    monkeypatch.setattr(bot, "fetch_510500_expiries_with_failover", unavailable)
    with bot.runtime_clock(CLOCK.replace(day=13)):
        result = bot._v14_ic_short_put_candidate(signal)
    assert result["tradable"] is True and events == [("2612", date(2026, 11, 13))]


@pytest.mark.parametrize("error", [RuntimeError("listing structure conflict"),
                                   evidence.EvidenceUnavailable("missing original months")])
def test_hard_listing_failure_does_not_fall_back_to_chain(monkeypatch, error):
    signal, events = candidate_inputs(monkeypatch, date(2026, 11, 13))
    def fail():
        raise error
    monkeypatch.setattr(bot, "fetch_510500_expiries_with_failover", fail)
    with bot.runtime_clock(CLOCK.replace(day=13)):
        if isinstance(error, evidence.EvidenceUnavailable):
            with pytest.raises(type(error)):
                bot._v14_ic_short_put_candidate(signal)
        else:
            result = bot._v14_ic_short_put_candidate(signal)
            assert result["tradable"] is False and result["reason"] == "listing_unverified"
    assert events == []


class Response:
    headers = {}
    def __init__(self, body):
        self.body, self.closed = body, False
    def raise_for_status(self):
        pass
    def iter_content(self, chunk_size=8192):
        yield self.body
    def close(self):
        self.closed = True


@pytest.mark.parametrize("body", [b"<html>blocked</html>", b'{"result":[', b"\xff\xfe", b""])
def test_non_json_master_is_unavailable_and_reaches_sina(monkeypatch, body):
    response = Response(body)
    monkeypatch.setattr(bot, "_evidence_http_get", lambda *a, **kw: response)
    monkeypatch.setattr(bot, "fetch_sse_510500_chain", lambda month:
                        bot.fetch_sse_510500_expiry_master(month, DAY))
    calls = []
    frame = pd.DataFrame([dict(contract="510500P2612M06500", strike=6.5, last=.35)])
    def sina(month):
        calls.append(month)
        return frame, {"source": "新浪财经期权详报价（上交所回退）"}
    monkeypatch.setattr(bot, "fetch_sina_510500_chain", sina)
    actual, _ = bot.fetch_510500_chain_with_failover("2612")
    assert actual.equals(frame) and calls == ["2612"] and response.closed


def test_duplicate_master_keys_remain_hard_conflicts(monkeypatch):
    response = Response(b'{"result":[],"result":[]}')
    monkeypatch.setattr(bot, "_evidence_http_get", lambda *a, **kw: response)
    with pytest.raises(expiry.ExpiryEvidenceConflict):
        bot.fetch_sse_510500_expiry_master("2612", DAY)
    assert response.closed


def test_cffex_deterministic_conflict_is_not_aggregated_as_source_failure(monkeypatch):
    events = []
    def fail(product, clock):
        events.append("official")
        raise expiry.ExpiryEvidenceConflict("deterministic CFFEX expiry conflict")
    monkeypatch.setattr(bot, "_fetch_cffex_official_quotes", fail)
    monkeypatch.setattr(bot, "_fetch_sina_future_quotes", lambda *a: pytest.fail("conflict fallback"))
    monkeypatch.setattr(bot, "_fetch_eastmoney_future_quotes", lambda *a: pytest.fail("conflict fallback"))
    with pytest.raises(expiry.ExpiryEvidenceConflict, match="deterministic"):
        bot.fetch_cffex_quotes("IM", CLOCK)
    assert events == ["official"]


def pending_ordinary(monkeypatch):
    anchor = opening_fixtures.anchor("IM")
    previous = deepcopy(anchor)
    previous["last_verified_day"] = DAY-timedelta(days=1)
    signal = opening_fixtures.signal("IM", DAY-timedelta(days=1), next_trade_date=DAY,
                                    core_put_target_contract="MO2701-P-7800",
                                    core_put_target_qty_normalized=1., v14_route_state="future")
    bot._v14_schedule_ordinary_put("IM", signal, previous, None)
    plan = signal["v14_ordinary_put_pending"]
    assert plan and plan["execution_day"] == DAY
    anchor["v14_ordinary_put_pending"] = plan
    monkeypatch.setitem(bot.LIVE_CONTINUATION_ANCHOR, "IM", anchor)
    return anchor


def test_ordinary_open_does_not_convert_expiry_conflict_to_retry(monkeypatch):
    pending_ordinary(monkeypatch)
    def fail(*a, **kw):
        raise expiry.ExpiryEvidenceConflict("ordinary opening expiry conflict")
    monkeypatch.setattr(bot, "fetch_cffex_quotes", fail)
    with pytest.raises(expiry.ExpiryEvidenceConflict):
        bot._v14_ordinary_open_evidence("IM", DAY, CLOCK, {})


@pytest.mark.parametrize("kind", ["short_put", "profit3x"])
def test_committed_open_does_not_convert_expiry_conflict_to_retry(monkeypatch, kind):
    anchor = opening_fixtures.anchor("IM")
    if kind == "short_put":
        signal = opening_fixtures.signal("IM", DAY-timedelta(days=1), next_trade_date=DAY)
        planned = bot.v14_policy.apply_policy("IM", signal, anchor, candidate=opening_fixtures.candidate("IM"))
        anchor.update(planned)
        assert anchor["v14_short_put_open_pending"]
    else:
        anchor.update(v14_profit_pending=True, v14_profit_trigger_day=DAY-timedelta(days=1),
                      v14_profit_execution_day=DAY, v14_profit_old_contract="MO2612-P-7600",
                      v14_profit_old_qty=1.5, v14_profit_reentry_contract="MO2701-P-7800",
                      v14_profit_reentry_qty=1.5)
    monkeypatch.setitem(bot.LIVE_CONTINUATION_ANCHOR, "IM", anchor)
    def fail(*a, **kw):
        raise expiry.ExpiryEvidenceConflict("committed opening expiry conflict")
    monkeypatch.setattr(bot, "_v14_committed_open_prices", fail)
    with pytest.raises(expiry.ExpiryEvidenceConflict):
        bot._v14_project_committed_opens_locked("IM", DAY, CLOCK, {})


def test_failed_attachment_continues_remaining_text_without_failure_snapshot(monkeypatch):
    class Sink:
        def __init__(self):
            self.text = io.StringIO()
        def write(self, value):
            return self.text.write(value)
        def attach_file(self, **kwargs):
            raise RuntimeError("attachment refused")
    sink = Sink()
    monkeypatch.setattr(bot, "_write_last_verified_snapshot", lambda *a: pytest.fail("duplicate failure snapshot"))
    with bot._isolated_product_report(sink, "IC") as message:
        message.write("diagnostic body\n")
        message.attach_file(name="diagnostic.json", contents=b"{}")
        message.write("remaining text\n")
    body = sink.text.getvalue()
    assert "diagnostic body" in body and "remaining text" in body and "提交失败" in body
    assert "信号展示失败" not in body and body.count("提交失败") == 1


@pytest.mark.parametrize("text, products", [
    ("查询 IC 12月合约", ("IC",)), ("查询IM 1月合约", ("IM",)),
    ("查询 IC 01月合约", ("IC",)), ("查询IC 2026年12月合约", ("IC",)),
    ("查询IM 2027年 01月合约", ("IM",)), ("查询中证1000 3月合约", ("IM",)),
    ("查询中证500 12月合约", ("IC",)), ("查询510500 3月合约", ("IC",)),
    ("查询IC2612 12月合约", ("IC",)),
])
def test_fallback_snapshot_accepts_month_contract_reference(text, products):
    result = bot.classify_query(text, DAY)
    assert result.route == "snapshot"
    assert result.products == products


@pytest.mark.parametrize("text", ["查询 IC 13月合约", "查询IC2026-13", "IC 12月合约 2026-13表现",
                                  "查询IC 12.5月合约", "查询IC 1.2月合约", "查询IC 1. 2月合约",
                                  "查询IC 12. 5月合约", "查询IC 2026年13月合约",
                                  "查询IC 12026年12月合约", "查询中证1000 1. 2月合约",
                                  "查询510500 12月合约2026-13"])
def test_snapshot_month_reference_does_not_mask_invalid_real_dates(text):
    with pytest.raises(ValueError):
        bot.classify_query(text, DAY)


@pytest.mark.parametrize("suffix, start, end", [
    ("2026-11-01", date(2026, 11, 1), date(2026, 11, 1)),
    ("2026年", date(2026, 1, 1), date(2026, 12, 31)),
])
def test_contract_reference_preserves_separate_real_date(suffix, start, end):
    result = bot.classify_query("查询IC 12月合约 " + suffix, DAY)
    assert (result.route, result.start, result.end) == ("performance", start, end)


def test_actual_quote_validator_conflict_reaches_public_opening_diagnostic(monkeypatch):
    pending_ordinary(monkeypatch)
    frame = pd.DataFrame([dict(instrument=f"MO{month}-{side}-{strike}", lastprice=100.,
                                volume=1, position=1, source_date=DAY, openprice=99.)
                          for month in ["2611", "2612", "2701", "2703", "2706", "2709"]
                          for side in ["C", "P"] for strike in [6000, 6100, 6200]])
    original_roll = bot._roll_forward_exchange_day
    def wrong_december(value, **kwargs):
        result = original_roll(value, **kwargs)
        return result+timedelta(days=1) if value.year == 2026 and value.month == 12 else result
    monkeypatch.setattr(bot, "_roll_forward_exchange_day", wrong_december)
    called = []
    def official(product, clock):
        called.append(product)
        return bot._validate_quote_frame(frame, product, clock, "中金所官方")
    monkeypatch.setattr(bot, "_fetch_cffex_official_quotes", official)
    monkeypatch.setattr(bot, "_fetch_eastmoney_mo_quotes", lambda *a: pytest.fail("deterministic conflict fallback"))
    monkeypatch.setattr(bot, "_build_live_trade_signal", lambda product, now, mode:
                        bot._v14_ordinary_open_evidence(product, DAY, now, {}))
    result = bot.build_live_trade_signal("IM", CLOCK, "close")
    assert result["diagnostic_status"] == "expiry_evidence_conflict"
    assert result["persistence_eligible"] is False and called == ["MO"]
    assert "v14_ordinary_put_pending" not in result


@pytest.mark.parametrize("fail", [False, True])
def test_cffex_verification_cache_is_query_scoped_and_restored(monkeypatch, fail):
    seen = []
    original = expiry.verify_cffex_rule_date
    def check(*args):
        seen.append(args[:3])
        return original(*args)
    monkeypatch.setattr(expiry, "verify_cffex_rule_date", check)
    def builder(product, now, mode):
        for _ in range(200):
            assert bot._third_friday(2026, 12) == date(2026, 12, 18)
        if fail:
            raise RuntimeError("after cached checks")
        return dict(product=product, market_date=DAY)
    monkeypatch.setattr(bot, "_build_live_trade_signal", builder)
    original_context = bot._CFFEX_EXPIRY_QUERY_CACHE.get()
    for _ in range(2):
        if fail:
            with pytest.raises(RuntimeError, match="after cached checks"):
                bot.build_live_trade_signal("IM", CLOCK, "close")
        else:
            bot.build_live_trade_signal("IM", CLOCK, "close")
        assert bot._CFFEX_EXPIRY_QUERY_CACHE.get() is original_context
    assert len(seen) == 2


def test_query_cache_rechecks_changed_calendar_bytes(monkeypatch, tmp_path):
    calendar = bot.state_council_calendar
    data = tmp_path / calendar.DATA_PATH.name
    notice = tmp_path / calendar.NOTICE_PATH.name
    data.write_bytes(calendar.DATA_PATH.read_bytes())
    notice.write_bytes(calendar.NOTICE_PATH.read_bytes())
    monkeypatch.setattr(calendar, "DATA_PATH", data)
    monkeypatch.setattr(calendar, "NOTICE_PATH", notice)
    token = bot._CFFEX_EXPIRY_QUERY_CACHE.set({})
    try:
        assert bot._third_friday(2027, 1) == date(2027, 1, 15)
        data.write_bytes(data.read_bytes()+b" ")
        with pytest.raises(bot.CalendarCoverageError, match="SHA"):
            bot._third_friday(2027, 1)
    finally:
        bot._CFFEX_EXPIRY_QUERY_CACHE.reset(token)


def test_query_cache_key_includes_calendar_policy_day(monkeypatch):
    checks = []
    original = expiry.verify_cffex_rule_date
    def check(*a):
        checks.append(bot._calendar_decision_day())
        return original(*a)
    monkeypatch.setattr(expiry, "verify_cffex_rule_date", check)
    token = bot._CFFEX_EXPIRY_QUERY_CACHE.set({})
    try:
        for policy_day in [DAY, DAY, DAY+timedelta(days=1)]:
            policy_token = bot._CALENDAR_POLICY_DAY.set(policy_day)
            try:
                bot._third_friday(2026, 12)
            finally:
                bot._CALENDAR_POLICY_DAY.reset(policy_token)
    finally:
        bot._CFFEX_EXPIRY_QUERY_CACHE.reset(token)
    assert checks == [DAY, DAY+timedelta(days=1)]


def test_real_date_before_instrument_and_month_reference_remains_performance():
    result = bot.classify_query("2026-01-05 IM2612 12月合约", DAY)
    assert (result.route, result.products, result.start, result.end) == (
        "performance", ("IM",), date(2026, 1, 5), date(2026, 1, 5))
