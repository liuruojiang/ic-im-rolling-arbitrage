from copy import deepcopy
from datetime import date, datetime
from io import StringIO

import pytest

import ic_im_quarter_roll_v1_3 as q
import poe_ic_im_mainline_v1_4_bot as bot
from test_icim_tenor_calendar_20261120 import quotes, expiry


def sample(product="IC", day=date(2026, 11, 25), *, spot=6100., missing=(), spot_date=None):
    frame, contracts, prices = quotes(product, day)
    frame = frame.loc[~frame.instrument.isin([contracts[i] for i in missing])].copy()
    curve = q.build_close_curve(product, frame, day, expiry, spot=spot,
                                spot_date=spot_date or day, spot_source="fixture index close")
    signal = dict(product=product, mode="close", market_date=day, close_confirmed=True,
                  index_price=spot, future_quote_date=day.isoformat(),
                  future_quote_source=frame.attrs["source"], close_report_curve=curve,
                  **q.listed_tenor_spreads(product, frame, day, expiry))
    return signal, contracts, prices


@pytest.mark.parametrize("product", ["IC", "IM"])
@pytest.mark.parametrize("day,roles,missing", [
    (date(2026, 12, 17), ("2612", "2701", "2703", "2706"), ()),
    (date(2026, 12, 18), ("2701", "2702", "2703", "2706"), (1,)),
    (date(2026, 12, 21), ("2701", "2702", "2703", "2706"), ()),
    (date(2027, 1, 15), ("2702", "2703", "2706", "2709"), (3,)),
])
def test_close_roles_advance_only_on_actual_expiry_and_missing_listing_isolated(product, day, roles, missing):
    signal, _, _ = sample(product, day)
    before = deepcopy(signal)
    lines = q.format_spreads(signal, product=product)
    assert signal == before and len(lines) == 8
    assert tuple(v["contract"] for v in signal["close_report_curve"]["legs"].values()) == tuple(product + m for m in roles)
    assert ["N/A" in line for line in lines[:3]] == [i in missing for i in (1, 2, 3)]
    assert ["N/A" in line for line in lines[3:7]] == [i in missing for i in range(4)]
    assert all(product + roles[0] in line for line in lines[:3] if "N/A" not in line)
    assert [name in line for name, line in zip(q.CLOSE_CURVE_LABELS, lines[3:7])] == [True]*4
    if day == date(2026, 12, 18):
        assert all(product + "2612" not in line for line in lines)
    assert "不是结算价" in lines[-1]


@pytest.mark.parametrize("missing,expected", [((1, 2, 3), [False, True, True, True]),
                                               ((0,), [True, False, False, False]),
                                               ((2,), [False, False, True, False])])
def test_each_index_leg_is_independent(missing, expected):
    signal, _, _ = sample(missing=missing)
    lines = q.format_close_curve(signal)
    assert ["N/A" in line for line in lines[3:7]] == expected


@pytest.mark.parametrize("spot", [True, -1, float("inf"), float("nan"), 1e-300, 1e308])
def test_bad_index_only_hides_index_spreads(spot):
    signal, _, _ = sample(spot=spot)
    lines = q.format_close_curve(signal)
    assert not any("N/A" in line for line in lines[:3])
    assert all("N/A" in line for line in lines[3:7])
    assert max(len(line) for line in lines) < 300


def test_wrong_index_date_and_quote_source_are_rejected_without_substitution():
    signal, _, _ = sample(spot_date=date(2026, 11, 24))
    assert all("N/A" in line for line in q.format_close_curve(signal)[3:7])
    signal["future_quote_source"] = "wrong source"
    assert all("N/A" in line for line in q.format_close_curve(signal)[:7])


def test_unknown_future_calendar_does_not_hide_authorized_near_or_change_strategy_context():
    signal, _, _ = sample(day=date(2027, 11, 22))
    context = {"expiries": [], "failures": []}
    token = bot._SIGNAL_CALENDAR_DIAGNOSTICS.set(context)
    try:
        lines = q.format_close_curve(signal)
        assert "N/A" not in lines[3] and all("N/A" in line for line in lines[4:7])
        assert context == {"expiries": [], "failures": []}
    finally:
        bot._SIGNAL_CALENDAR_DIAGNOSTICS.reset(token)


def test_diagnostic_preserves_verified_observation_without_formal_target():
    signal, _, _ = sample()
    future = {"quote_source": signal["future_quote_source"], "quote_date": str(signal["market_date"]),
              "quote_status": "signal_date_matched"}
    context = {"expiries": [], "failures": [], "signal": signal, "future": future,
               "anchor": bot.LIVE_CONTINUATION_ANCHOR["IC"]}
    token = bot._SIGNAL_CALENDAR_DIAGNOSTICS.set(context)
    try:
        diagnostic = bot.make_diagnostic_signal("IC", datetime(2026, 11, 25, 20, tzinfo=bot.BEIJING),
                                                "模拟期权故障", status="put_fail_closed")
    finally:
        bot._SIGNAL_CALENDAR_DIAGNOSTICS.reset(token)
    assert diagnostic["diagnostic_only"] and not diagnostic["persistence_eligible"]
    assert diagnostic["diagnostic_close_report_curve"] == signal["close_report_curve"]
    class DiagnosticOutput(StringIO):
        def attach_file(self, **kwargs):
            assert kwargs.get("name", "").endswith(".json")
    out = DiagnosticOutput()
    bot._write_diagnostic_signal(out, "IC", diagnostic)
    message = out.getvalue()
    assert message.count("期现贴水（") == 4 and message.count("期限贴水（") == 3
    assert "仅行情观察" in message and "完整目标：N/A" in message


def test_old_and_intraday_reports_keep_previous_monthly_anchor():
    old, _, _ = sample(day=date(2026, 11, 23))
    assert q.format_spreads(old) == q.format_tenor_spreads(old)
    signal, _, _ = sample(day=date(2026, 12, 18))
    signal["mode"] = "intraday"
    assert len(q.format_spreads(signal)) == 3 and "IC2612" in q.format_spreads(signal)[0]


def test_actual_attachment_path_has_exact_seven_items_and_scope_note_per_product():
    import run_ic_im_v1_4_github_digest as digest
    signals = {p: sample(p, date(2026, 12, 18))[0] for p in ("IC", "IM")}
    report = digest.render_stored_close_report({"verified_day": "2026-12-18", "sequence": 1,
                                                "digest": "d"*64, "signals": signals})
    for product, signal in signals.items():
        for line in q.format_close_curve(signal, product=product):
            assert "- " + line in report
