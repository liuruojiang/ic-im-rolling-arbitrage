"""Reproduce the November 23 provider decision with the original Friday tables."""
from datetime import date, datetime
import json
from unittest.mock import Mock

import pytest

import ic_im_expiry_evidence_v1 as expiry
import poe_ic_im_mainline_v1_4_bot as bot
from test_icim_monthly_old_leg_sse_20261120 import BASE, NEXT, observed


def check(frame, master, month, clock, **kwargs):
    return expiry.verify_sse_chain(
        frame, master, as_of=clock.date(), month="20" + month,
        model_expiry=bot._fourth_wednesday(2000 + int(month[:2]), int(month[2:])),
        observation_clock=clock,
        exchange_session_on_or_after=lambda d: bot._roll_forward_exchange_day(d, require_official=True),
        **kwargs)


@pytest.mark.parametrize("month", ["2611", "2612", "2703", "2706"])
@pytest.mark.parametrize("hour", [14, 20])
def test_prior_announcements_cannot_supply_current_prices(month, hour):
    frame, master = observed(month)
    clock = datetime(2026, 11, 23, hour, tzinfo=bot.BEIJING)
    with pytest.raises(expiry.ExpiryMasterUnavailable, match="TIMESAVE=2026-11-20.*2026-11-23"):
        check(frame, master, month, clock)


@pytest.mark.parametrize("fault", [
    "weekend", "older_session", "mixed_snapshot", "duplicate_security", "duplicate_contract",
    "future_adjusted", "wrong_listing", "wrong_expiry", "wrong_underlying", "wrong_type",
])
def test_invalid_prior_master_is_still_a_hard_conflict(fault):
    frame, master = observed("2612")
    rows = master["result"]
    announced = next(row for row in rows if row["START_DATE"] == "20261123")
    clock = datetime(2026, 11, 23, 20, tzinfo=bot.BEIJING)
    if fault == "weekend":
        for row in rows:
            row["TIMESAVE"] = "2026-11-22"
    elif fault == "older_session":
        clock = clock.replace(day=24)
    elif fault == "mixed_snapshot":
        announced["TIMESAVE"] = "2026-11-23"
    elif fault == "duplicate_security":
        rows.append(dict(announced))
    elif fault == "duplicate_contract":
        rows.append(dict(announced, SECURITY_ID="99999999"))
    elif fault == "future_adjusted":
        announced["CONTRACT_ID"] = announced["CONTRACT_ID"].replace("M", "A")
    elif fault == "wrong_listing":
        announced["START_DATE"] = "20261124"
    elif fault == "wrong_expiry":
        announced["END_DATE"] = "20261224"
    elif fault == "wrong_underlying":
        announced["SECURITYNAMEBYID"] = "300ETF(510300)"
    elif fault == "wrong_type":
        announced["UNDERLYING_TYPE"] = "STOCK"
    with pytest.raises(expiry.ExpiryEvidenceConflict) as raised:
        check(frame, master, "2612", clock)
    if fault.startswith("duplicate"):
        assert ("duplicate SECURITY_ID" if fault == "duplicate_security" else "duplicate CONTRACT_ID") in str(raised.value)


def test_live_entry_checks_fallback_day_and_never_records_old_sse_proof(monkeypatch):
    month = "2612"
    frame, master = observed(month)
    quote = json.loads((BASE / "sse_chain_2612.json").read_text(encoding="utf-8"))
    clock = datetime(2026, 11, 23, 20, tzinfo=bot.BEIJING)
    monkeypatch.setattr(bot, "_now_beijing", lambda: clock)
    monkeypatch.setattr(bot, "_sse_quote_json", lambda *args, **kwargs: quote)
    monkeypatch.setattr(bot, "fetch_sse_510500_expiry_master", lambda *args: master)
    monkeypatch.setattr(bot.evidence_adapter, "record_expiry_proof",
                        lambda *args: pytest.fail("old SSE proof was recorded"))
    fallback = Mock(return_value=(frame, {"date": "20261120", "source": "新浪财经期权详报价"}))
    monkeypatch.setattr(bot, "fetch_sina_510500_chain", fallback)
    with pytest.raises(RuntimeError):
        bot._fetch_510500_chain_for_day(month, NEXT)
    fallback.assert_called_once_with(month)


def test_recorded_previous_master_cannot_be_replayed_as_current():
    frame, master = observed("2703")
    proof = dict(revision=expiry.REVISION, provider="SSE", payload=master,
                 day=NEXT.isoformat(), month="202703")
    with pytest.raises((expiry.ExpiryEvidenceConflict, expiry.ExpiryMasterUnavailable)):
        expiry.verify_recorded_chain(frame, {"source": "上交所行情接口"}, proof, as_of=NEXT,
                                     month="202703", model_expiry=date(2027, 3, 24),
                                     observation_clock=datetime(2026, 11, 23, 20, tzinfo=bot.BEIJING))
