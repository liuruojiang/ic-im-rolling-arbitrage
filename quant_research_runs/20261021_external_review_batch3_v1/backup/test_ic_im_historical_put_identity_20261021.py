"""Historical prices cannot establish their own code, strike or unit."""
from datetime import date, datetime
from decimal import Decimal
import json
from pathlib import Path

import pandas as pd
import pytest

import ic_im_adjusted_put_migration_v1 as migration
import poe_ic_im_mainline_v1_4_bot as bot

FIXTURE = Path(__file__).parent / "tests/fixtures/icim_external_review_round2_20261021"
DAY = date(2026, 10, 20)
M = "510500P2612M07500"
NEW_M_ID, OLD_ID = "10012099", "10011470"


def master_payload():
    return json.loads((FIXTURE / "sse_contract_master.json").read_text(encoding="utf-8"))


def setup_sources(monkeypatch, *, mapping=None, price=.25):
    mapping = mapping if mapping is not None else migration.master_terms(master_payload(), as_of=DAY)
    calls = []
    def terms(reference):
        calls.append(("identity", reference))
        assert reference == DAY
        return mapping
    def closes(security):
        calls.append(("price", security))
        result = pd.Series([price], index=pd.to_datetime([DAY]))
        result.attrs["source"] = "synthetic_ID_keyed_price"
        return result
    monkeypatch.setattr(bot, "fetch_sse_510500_dated_master", terms, raising=False)
    monkeypatch.setattr(bot, "fetch_option_closes", closes)
    return calls


def test_old_m_label_cannot_take_adjusted_id_price_even_before_new_signal_boundary(monkeypatch):
    calls = setup_sources(monkeypatch)
    with pytest.raises(RuntimeError, match="身份|identity|adjusted"):
        bot.fetch_sse_existing_put_historical_quote(M, OLD_ID, DAY)
    assert calls == [("identity", DAY)]


def test_real_dated_master_can_support_standard_m_without_copying_requested_identity(monkeypatch):
    calls = setup_sources(monkeypatch)
    frame, stamp = bot.fetch_sse_existing_put_historical_quote(M, NEW_M_ID, DAY)
    assert calls == [("identity", DAY), ("price", NEW_M_ID)]
    row = frame.iloc[0]
    assert row["security_id"] == NEW_M_ID
    assert row["strike"] == 7.5 and row["contract_unit"] == 10000
    assert stamp["identity_date"] == DAY.isoformat()
    assert len(stamp["identity_payload_sha256"]) == 64
    assert row["last"] == .25


def test_missing_dated_identity_must_fail_before_price_fetch(monkeypatch):
    calls = setup_sources(monkeypatch, mapping={})
    with pytest.raises(RuntimeError, match="身份|identity"):
        bot.fetch_sse_existing_put_historical_quote(M, NEW_M_ID, DAY)
    assert calls == [("identity", DAY)]


@pytest.mark.parametrize("price", [float("nan"), float("inf"), -.01, True])
def test_bad_price_is_rejected_after_independent_identity(monkeypatch, price):
    setup_sources(monkeypatch, price=price)
    with pytest.raises(RuntimeError, match="价格|price"):
        bot.fetch_sse_existing_put_historical_quote(M, NEW_M_ID, DAY)


def test_zero_price_is_not_dropped_or_replaced(monkeypatch):
    setup_sources(monkeypatch, price=0.)
    frame, _ = bot.fetch_sse_existing_put_historical_quote(M, NEW_M_ID, DAY)
    assert frame.iloc[0]["last"] == 0.


def test_adjusted_code_remains_blocked_until_engine_migration(monkeypatch):
    calls = setup_sources(monkeypatch)
    with pytest.raises(RuntimeError, match="adjusted"):
        bot.fetch_sse_existing_put_historical_quote("510500P2612A07500", OLD_ID, DAY)
    assert not any(call[0] == "price" for call in calls)


def test_dated_terms_from_another_day_do_not_support_price_row(monkeypatch):
    setup_sources(monkeypatch)
    with pytest.raises(RuntimeError, match="interval|availability"):
        # Independently supplied mapping must still prove the requested day.
        monkeypatch.setattr(bot, "fetch_sse_510500_dated_master",
                            lambda _: migration.master_terms(master_payload(), as_of=DAY))
        bot.fetch_sse_existing_put_historical_quote(M, NEW_M_ID, date(2026,10,21))


def test_network_error_is_an_explicit_missing_identity_error(monkeypatch):
    def unavailable(*args, **kwargs):
        raise bot.requests.exceptions.ProxyError("synthetic proxy unavailable")
    monkeypatch.setattr(bot.requests, "get", unavailable)
    with pytest.raises(RuntimeError, match="逐日身份来源不可用"):
        bot.fetch_sse_510500_dated_master(DAY)


@pytest.mark.parametrize("identity", ["１００１２０９９", "1001209", "100120990", None])
def test_bad_requested_id_does_not_become_an_output_id(monkeypatch, identity):
    calls = setup_sources(monkeypatch)
    with pytest.raises(RuntimeError):
        bot.fetch_sse_existing_put_historical_quote(M, identity, DAY)
    assert not any(call[0] == "price" for call in calls)


def test_standard_core_overlay_succeeds_with_independent_dated_identity(monkeypatch):
    setup_sources(monkeypatch)
    anchor = dict(v14_core_put_contract=M, v14_core_put_security_id=NEW_M_ID,
                  v14_core_put_qty=5, verified_core_put_delta=.25)
    signal = dict(market_date=DAY, core_put_target_delta=.25,
                  option_monthly_reset_due=False, put_target_momentum_qty=0)
    monkeypatch.setattr(bot, "_now_beijing", lambda: datetime(2026,10,20,15,40,tzinfo=bot.BEIJING))
    with bot.historical_replay(DAY):
        bot._v14_ic_core_overlay(signal, anchor)
    assert signal["iv_monitor_evidence"]["security_id"] == NEW_M_ID
    assert signal["iv_monitor_option_price"] == .25


class Response:
    def __init__(self, payload):
        self.payload, self.closed = payload, False
        self.content = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.headers = {'Content-Length': str(len(self.content))}
    def raise_for_status(self): pass
    def json(self): return self.payload
    def close(self): self.closed = True
    def iter_content(self, chunk_size=65536):
        for start in range(0, len(self.content), chunk_size):
            yield self.content[start:start+chunk_size]


@pytest.mark.parametrize("requested", [DAY, date(2026,10,19), date(2026,10,21)])
def test_official_fetch_validates_actual_timesave_and_closes_response(monkeypatch, requested):
    response = Response(master_payload())
    calls = []
    def get(url, **kwargs):
        calls.append((url,kwargs))
        return response
    monkeypatch.setattr(bot.requests, "get", get)
    fetch = getattr(bot, "fetch_sse_510500_dated_master")
    if requested == DAY:
        assert fetch(requested)[NEW_M_ID].unit == 10000
    else:
        with pytest.raises(RuntimeError, match="date/identity/listing"):
            fetch(requested)
    assert response.closed
    assert calls[0][0] == "https://query.sse.com.cn/commonQuery.do"
    assert calls[0][1]["params"]["queryDate"] == requested.strftime("%Y%m%d")
    assert calls[0][1]["timeout"] <= 8
