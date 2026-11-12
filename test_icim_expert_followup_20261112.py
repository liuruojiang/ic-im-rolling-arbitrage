"""Public-boundary regression for the second expert follow-up; no external I/O."""
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


ROOT = Path(__file__).resolve().parent
TODAY = date(2026, 11, 12)
CLOCK = datetime(2026, 11, 12, 18, tzinfo=bot.BEIJING)
LISTED = ["202611", "202612", "202703", "202706"]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("follow-up regression forbids external HTTP")
    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    monkeypatch.setattr(bot, "_SIGNAL_OBSERVER", None)
    with bot.runtime_clock(CLOCK):
        yield


class Sink:
    def __init__(self, fail_ic_attachment=False):
        self.text = io.StringIO()
        self.attachments = []
        self.fail_ic_attachment = fail_ic_attachment

    def write(self, value):
        return self.text.write(value)

    def attach_file(self, **kwargs):
        if self.fail_ic_attachment and kwargs["name"].startswith("ic_"):
            raise RuntimeError("test attachment delivery refused")
        self.attachments.append(kwargs)


def run_handler(monkeypatch, private_builder, products=("IC",), *, fail_ic_attachment=False):
    sink = Sink(fail_ic_attachment)
    @contextmanager
    def message():
        yield sink
    monkeypatch.setattr(bot, "_sm", message)
    monkeypatch.setattr(bot, "_build_live_trade_signal", private_builder)
    monkeypatch.setattr(bot, "_write_last_verified_snapshot",
                        lambda msg, product: msg.write(f"verified snapshot {product}\n"))
    bot.ICIMMainlinesBot()._handle_signal(products, "close")
    return sink


@pytest.mark.parametrize("status", ["calendar_unverified", "put_fail_closed",
                                   "awaiting_opening_evidence", "original_evidence_unavailable"])
def test_public_diagnostic_route_keeps_text_and_json(monkeypatch, status):
    def builder(product, now=None, mode="close"):
        return bot.make_diagnostic_signal(product, now or CLOCK, "diagnostic body sentinel", status=status)
    sink = run_handler(monkeypatch, builder)
    assert "diagnostic body sentinel" in sink.text.getvalue()
    assert "信号展示失败" not in sink.text.getvalue()
    assert len(sink.attachments) == 1
    payload = json.loads(sink.attachments[0]["contents"])
    assert payload["diagnostic_status"] == status
    assert payload["diagnostic_only"] and not payload["persistence_eligible"]
    assert sink.attachments[0]["content_type"] == "application/json"
    assert "core_target" not in payload


def test_failed_attachment_keeps_diagnostic_body_and_the_other_product(monkeypatch):
    def builder(product, now=None, mode="close"):
        return bot.make_diagnostic_signal(product, now or CLOCK, f"diagnostic body {product}", status="put_fail_closed")
    sink = run_handler(monkeypatch, builder, ("IC", "IM"), fail_ic_attachment=True)
    assert "diagnostic body IC" in sink.text.getvalue()
    assert "附件" in sink.text.getvalue() and "提交失败" in sink.text.getvalue()
    assert "diagnostic body IM" in sink.text.getvalue()
    assert [item["name"].split("_", 1)[0] for item in sink.attachments] == ["im"]


@pytest.mark.parametrize("query,products,route", [
    ("中证500年化", ("IC",), "performance"),
    ("中证1000年化", ("IM",), "performance"),
    ("中证500/1000表现", ("IC", "IM"), "performance"),
    ("中证500和1000表现", ("IC", "IM"), "performance"),
    ("510500年化", ("IC",), "performance"),
    ("IC2612年化", ("IC",), "performance"),
    ("IM2612年化", ("IM",), "performance"),
    ("MO2612-P-7400年化", ("IM",), "performance"),
    ("MO年化表现", ("IM",), "performance"),
    ("MO规则", ("IM",), "params"),
    ("MODEL年化表现", ("IC", "IM"), "performance"),
    ("DEMO规则", ("IC", "IM"), "params"),
    ("510500P2612M06250年化", ("IC",), "performance"),
    ("IC 12月合约信号", ("IC",), "close_signal"),
    ("IM 12月合约规则", ("IM",), "params"),
    ("IM 12月合约盘中信号", ("IM",), "intraday_signal"),
])
def test_product_identifiers_and_explicit_routes_are_not_dates(query, products, route):
    intent = bot.classify_query(query, TODAY)
    assert intent.products == products and intent.route == route
    if route == "performance":
        assert (intent.start, intent.end) == (date(2025, 11, 12), TODAY)
    else:
        assert intent.start is intent.end is None


@pytest.mark.parametrize("query,expected", [
    ("中证 500 2026年3月至今表现", (date(2026, 3, 1), TODAY)),
    ("IC2012-03-01表现", (date(2012, 3, 1), date(2012, 3, 1))),
    ("IC2012年3月至今", (date(2012, 3, 1), TODAY)),
])
def test_instrument_recognition_preserves_adjacent_real_dates(query, expected):
    intent = bot.classify_query(query, TODAY)
    assert intent.products == ("IC",) and intent.route == "performance"
    assert (intent.start, intent.end) == expected


@pytest.mark.parametrize("query", [
    "中证500 2026-03-311表现", "中证1000 19026.03到2026.08表现",
    "510500 2026-13表现", "MO2612-P-7400 12026-2027表现",
    "IC2012-03-001表现", "IC2026-13表现", "510500.03.01到2026.08.31表现",
])
def test_bad_performance_dates_remain_errors_after_identifiers_are_recognized(query):
    with pytest.raises(ValueError):
        bot.parse_date_range(query, TODAY)
    with pytest.raises(ValueError):
        bot.classify_query(query, TODAY)


def test_confirmed_unlisted_roll_month_needs_no_future_calendar_or_chain(monkeypatch):
    monkeypatch.setattr(bot, "fetch_510500_expiries_with_failover", lambda: LISTED)
    monkeypatch.setattr(bot, "_fetch_510500_chain_for_day", lambda *a: pytest.fail("unlisted chain requested"))
    monkeypatch.setattr(bot, "_require_official_exchange_calendar", lambda *a: pytest.fail("unused future calendar requested"))
    result = bot._v14_ic_short_put_candidate(
        dict(market_date=date(2026, 11, 13), next_trade_date=date(2026, 11, 16)),
        roll_from_expiry=date(2026, 12, 23))
    assert result["tradable"] is False and result["reason"] == "target_month_not_listed"
    assert result["month"] == "202701" and result["listed_months"] == LISTED


@pytest.mark.parametrize("failure", ["listing", "listed_chain"])
def test_listing_or_listed_chain_failure_cannot_be_called_unlisted(monkeypatch, failure):
    def broken(*args):
        raise RuntimeError("source broken sentinel")
    monkeypatch.setattr(bot, "fetch_510500_expiries_with_failover",
                        broken if failure == "listing" else lambda: LISTED + ["202701"])
    monkeypatch.setattr(bot, "_fetch_510500_chain_for_day", broken)
    with bot.runtime_clock(CLOCK.replace(day=13)):
        signal = dict(market_date=date(2026, 11, 13), next_trade_date=date(2026, 11, 16))
        if failure == "listing":
            result = bot._v14_ic_short_put_candidate(signal, roll_from_expiry=date(2026, 12, 23))
            assert result["tradable"] is False and result["reason"] == "listing_unverified"
        else:
            with pytest.raises(RuntimeError, match="source broken sentinel"):
                bot._v14_ic_short_put_candidate(signal, roll_from_expiry=date(2026, 12, 23))


def test_ic_unpriced_fixed_target_reaches_public_diagnostic_attachment(monkeypatch):
    monkeypatch.setattr(bot, "fetch_510500_expiries_with_failover", lambda: LISTED)
    chain = pd.DataFrame([dict(contract="510500P2703M06250", strike=6.25, last=0., security_id="10012282"),
                          dict(contract="510500P2703M06500", strike=6.5, last=.3, security_id="10012283")])
    monkeypatch.setattr(bot, "_fetch_510500_chain_for_day", lambda *a: (chain, dict(date="20261112", time="150000", source="上交所行情接口")))
    monkeypatch.setattr(bot, "_implied_volatility", lambda *a: pytest.fail("unpriced target must stop before IV"))
    def builder(product, now=None, mode="close"):
        bot.select_ic_put_for_reset(TODAY, 6.7, 6500., .25)
        pytest.fail("unpriced fixed target was accepted")
    sink = run_handler(monkeypatch, builder)
    payload = json.loads(sink.attachments[0]["contents"])
    assert "510500P2703M06250" in sink.text.getvalue()
    assert payload["diagnostic_status"] == "put_fail_closed"
    assert payload["diagnostic_option"]["contract"] == "510500P2703M06250"
    assert not payload["persistence_eligible"] and "core_target" not in payload


def test_generation_uses_market_policy_day_but_retains_runtime_and_restores_on_error(monkeypatch):
    seen = {}
    monkeypatch.setattr(bot, "live_proxy", lambda *a: {"history_date": date(2026, 11, 11)})
    def stop_after_market_day(*args):
        seen.update(policy_day=bot._calendar_decision_day(), actual_clock=bot._now_beijing())
        raise RuntimeError("scope checkpoint")
    monkeypatch.setattr(bot, "_validate_signal_market_date", stop_after_market_day)
    outer = date(2028, 1, 1)
    token = bot._CALENDAR_POLICY_DAY.set(outer)
    try:
        clock = datetime(2026, 11, 12, 8, tzinfo=bot.BEIJING)
        with pytest.raises(RuntimeError, match="scope checkpoint"):
            bot.build_live_trade_signal("IC", clock, "intraday")
        assert seen == dict(policy_day=date(2026, 11, 11), actual_clock=clock)
        assert bot._CALENDAR_POLICY_DAY.get() == outer
    finally:
        bot._CALENDAR_POLICY_DAY.reset(token)


@pytest.mark.parametrize("lead_microseconds", [1, 3_000_000])
def test_future_quote_rejection_reports_vendor_minus_local_without_tolerance(lead_microseconds):
    local = datetime(2026, 11, 12, 15, tzinfo=bot.BEIJING)
    vendor = local + timedelta(microseconds=lead_microseconds)
    with pytest.raises(RuntimeError) as caught:
        bot.quote_snapshot_guard.validate_snapshot_time(vendor, local, TODAY, "test vendor")
    message = str(caught.value)
    assert f"vendor={vendor.isoformat()}" in message and f"local={local.isoformat()}" in message
    assert f"vendor_minus_local_seconds={lead_microseconds/1_000_000:.6f}" in message
    assert bot.quote_snapshot_guard.official_snapshot_evidence(local, local, TODAY, "equal") == {
        "source_snapshot_at": local.isoformat(timespec="seconds"),
        "source_snapshot_kind": "http_last_modified_whole_frame"}


def test_same_calendar_notice_is_rendered_once_with_real_saved_im_signal(monkeypatch):
    saved = ROOT/'quant_research_runs/20261112_state_council_calendar_v1/whole_revision_real_round7/IM_after_signal.json'
    signal = bot.signal_evidence.decode(json.loads(saved.read_text(encoding="utf-8")))
    signal = deepcopy(signal)
    notice = bot.state_council_calendar.description()
    signal["calendar_coverage_warning"] = notice
    signal["data_notes"] = [notice, "distinct audit note", *signal["data_notes"]]
    monkeypatch.setattr(bot, "_now_beijing", lambda: datetime(2026, 10, 22, 18, 40, 29, tzinfo=bot.BEIJING))
    sink = run_handler(monkeypatch, lambda *a: signal, ("IM",))
    assert "信号展示失败" not in sink.text.getvalue()
    assert sink.text.getvalue().count(notice) == 1
    assert "distinct audit note" in sink.text.getvalue()
