from contextlib import contextmanager
from copy import deepcopy
from datetime import date, datetime, timedelta
import json

import pandas as pd
import pytest
import requests

import ic_im_quarter_roll_v1_3 as q
import poe_ic_im_mainline_v1_4_bot as bot
import run_ic_im_v1_4_github_digest as digest
from test_icim_close_curve_20261124_v2 import sample


CLOCK = datetime(2026, 12, 18, 20, tzinfo=bot.BEIJING)


def capture_before_real_missing_contract(product, clock, mode):
    """Real source gate, report builder and exact original missing-leg guard."""
    day = clock.date()
    frame = pd.DataFrame({"instrument": [product + m for m in ("2612", "2701", "2703", "2706")],
                          "lastprice": [6000., 0., 5800., 5700.], "volume": [10]*4,
                          "position": [10]*4, "source_date": [day]*4})
    verified = bot._validate_quote_frame(frame, product, clock, "independent fixture")
    curve = q.build_close_curve(product, verified, day, lambda c: q._report_calendar_expiry(product, day, c),
                                spot=6100., spot_date=day, spot_source="independent index close")
    ctx = bot._SIGNAL_CALENDAR_DIAGNOSTICS.get()
    ctx["signal"].update(market_date=day, index_price=6100., mode=mode, close_confirmed=True,
                         close_report_curve=curve)
    ctx["future"] = dict(quote_source=verified.attrs["source"], quote_date=str(day), quote_status="signal_date_matched",
                         quotes=[dict(contract=r.instrument, lastprice=r.lastprice) for r in verified.itertuples()])
    return bot._require_listed_future_quote(product, verified, product + "2701")


@pytest.mark.parametrize("product", ["IC", "IM"])
def test_actual_missing_new_near_error_keeps_prior_observations_and_remains_error(monkeypatch, product):
    anchor = deepcopy(bot.LIVE_CONTINUATION_ANCHOR[product])
    calls = []
    def build(p, now, mode):
        calls.append(p)
        return capture_before_real_missing_contract(p, now, mode)
    monkeypatch.setattr(bot, "_build_live_trade_signal", build)
    monkeypatch.setattr(bot, "_now_beijing", lambda: CLOCK)
    with pytest.raises(RuntimeError, match="2701.*缺少有限正价") as caught:
        bot.build_live_signal_with_transport_retry(product, "close", 45.)
    assert type(caught.value) is RuntimeError and calls == [product]
    assert bot._SIGNAL_CALENDAR_DIAGNOSTICS.get() is None
    assert bot.LIVE_CONTINUATION_ANCHOR[product] == anchor
    observation = bot._close_failure_observation(caught.value, product, "close")
    assert observation["diagnostic_only"] and not observation["persistence_eligible"]
    assert bot._close_failure_observation(caught.value, "IM" if product == "IC" else "IC", "close") is None
    assert bot._close_failure_observation(caught.value, product, "intraday") is None
    assert observation["diagnostic_close_report_curve"]["legs"]["quarter_near"]["price"] == 5800.


class Capture:
    def __init__(self): self.text = []; self.files = []
    def write(self, text): self.text.append(str(text))
    def attach_file(self, **kwargs): self.files.append(kwargs)


def test_real_failed_query_renders_observations_without_observer_or_target(monkeypatch):
    output = Capture(); observed = []
    @contextmanager
    def capture(): yield output
    monkeypatch.setattr(bot, "_sm", capture)
    monkeypatch.setattr(bot, "_now_beijing", lambda: CLOCK)
    monkeypatch.setattr(bot, "_build_live_trade_signal", capture_before_real_missing_contract)
    monkeypatch.setattr(bot, "_write_last_verified_snapshot", lambda *a: None)
    monkeypatch.setattr(bot, "_SIGNAL_OBSERVER", lambda *a: observed.append(a))
    bot.ICIMMainlinesBot()._handle_signal(("IC",), "close")
    text = "".join(output.text)
    assert "当日信号警告" in text and "IC2701 缺少有限正价" in text
    assert "IC2703 5800.00" in text and "IC2706 5700.00" in text
    assert "+4.92%" in text and "+6.56%" in text and "仅行情观察" in text
    assert "完整目标：N/A" in text and observed == []
    assert text.count("### IC") == 1 and len(output.files) == 1
    attached = json.loads(output.files[0]["contents"])
    assert not attached["persistence_eligible"] and attached["signal_complete"] is False


def prepared_error(monkeypatch, error, *, spot=6100.):
    signal, _, _ = sample("IC", date(2026, 12, 18), spot=spot)
    def build(product, now, mode):
        ctx = bot._SIGNAL_CALENDAR_DIAGNOSTICS.get()
        ctx["signal"].update(signal)
        ctx["future"] = dict(quote_source=signal["future_quote_source"], quote_date=str(signal["market_date"]),
                             quote_status="signal_date_matched")
        raise error
    with monkeypatch.context() as m:
        m.setattr(bot, "_build_live_trade_signal", build)
        with pytest.raises(type(error)) as caught:
            bot.build_live_trade_signal("IC", CLOCK, "close")
    assert caught.value is error
    assert bot._close_failure_observation(error, "IC", "close")
    return error


@pytest.mark.parametrize("final", ["ordinary", "transport", "new_observation", "success", "next_day", "budget"])
def test_retry_observation_is_whole_prefer_final_and_never_crosses_day(monkeypatch, final):
    first = prepared_error(monkeypatch, requests.Timeout("first transport"))
    last = requests.Timeout("second transport") if final == "transport" else RuntimeError("second failure")
    if final == "new_observation":
        last = prepared_error(monkeypatch, last, spot=6200.)
    calls = []
    def build(product, mode):
        calls.append(product)
        if len(calls) == 1: raise first
        if final == "success": return {"product": product, "data_notes": []}
        raise last
    monkeypatch.setattr(bot, "build_live_trade_signal", build)
    monkeypatch.setattr(bot, "_now_beijing", lambda: CLOCK + (timedelta(days=1) if final == "next_day" and len(calls) > 1 else timedelta()))
    if final == "budget":
        def exhausted(*a): raise last
        monkeypatch.setattr(bot, "_bounded_timeout", exhausted)
    if final == "success":
        result = bot.build_live_signal_with_transport_retry("IC", "close", 45.)
        assert result["data_notes"] and "icim_close_failure_observation" not in result
    else:
        with pytest.raises(type(last)) as caught:
            bot.build_live_signal_with_transport_retry("IC", "close", 45.)
        assert caught.value is last
        observation = bot._close_failure_observation(last, "IC", "close")
        if final == "next_day":
            assert observation is None
        elif final == "new_observation":
            assert observation["diagnostic_model"]["index_price"] == 6200.
            assert "首次尝试" not in observation["diagnostic_reason"]
        else:
            assert "首次尝试" in observation["diagnostic_reason"]
            assert observation["diagnostic_close_report_curve"] == first.icim_close_failure_observation["diagnostic_close_report_curve"]
            assert "首次尝试" not in first.icim_close_failure_observation["diagnostic_reason"]
    assert calls == (["IC"] if final == "budget" else ["IC", "IC"])


def test_no_acquired_observation_or_internal_failure_never_masks_original(monkeypatch):
    error = RuntimeError("early hard failure")
    def build(*args): raise error
    monkeypatch.setattr(bot, "_build_live_trade_signal", build)
    with pytest.raises(RuntimeError) as caught:
        bot.build_live_trade_signal("IC", CLOCK, "close")
    assert caught.value is error and bot._close_failure_observation(error, "IC", "close") is None
    # An observation formatter/collector error cannot replace the real guard.
    def diagnostic_failure(*a, **k): raise ValueError("optional diagnostic failed")
    monkeypatch.setattr(bot, "make_diagnostic_signal", diagnostic_failure)
    monkeypatch.setattr(bot, "_build_live_trade_signal", capture_before_real_missing_contract)
    with pytest.raises(RuntimeError, match="2701.*缺少有限正价") as caught:
        bot.build_live_trade_signal("IC", CLOCK, "close")
    assert bot._close_failure_observation(caught.value, "IC", "close") is None


def test_new_attachment_explains_compatibility_fields_without_rewriting_signal():
    signals = {p: sample(p, date(2026, 12, 18))[0] for p in ("IC", "IM")}
    original = deepcopy(signals)
    report = digest.render_stored_close_report({"verified_day": "2026-12-18", "sequence": 1,
                                                "digest": "d"*64, "signals": signals})
    assert report.count("**贴水字段口径：**") == 2
    assert "`close_report_curve`" in report and "`near_*_spread`" in report
    assert signals == original
    for part, p in zip(report.split("```json\n")[1:], ("IC", "IM")):
        actual = json.loads(part.split("\n```", 1)[0])
        assert actual == digest._jsonable(signals[p])
    old = {p: sample(p, date(2026, 11, 23))[0] for p in ("IC", "IM")}
    old_report = digest.render_stored_close_report({"verified_day": "2026-11-23", "sequence": 1,
                                                    "digest": "d"*64, "signals": old})
    assert "**贴水字段口径：**" not in old_report
