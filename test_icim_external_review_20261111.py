"""Boundary counterexamples from the external review; synthetic cases are not market evidence."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import date, datetime
import io
import json

import numpy as np
import pandas as pd
import pytest
import requests

import poe_ic_im_mainline_v1_4_bot as bot


def complete_anchors():
    anchors = deepcopy(bot.LIVE_CONTINUATION_ANCHOR)
    for product, anchor in anchors.items():
        anchor.update(bot.v14_policy.default_extension(product))
    return anchors


@pytest.mark.parametrize("text,start,end", [
    ("2026年3月到2026年8月表现", date(2026, 3, 1), date(2026, 8, 31)),
    ("2026-03到2026-08", date(2026, 3, 1), date(2026, 8, 31)),
    ("2026/03至2026/08", date(2026, 3, 1), date(2026, 8, 31)),
    ("2026年8月至2026年3月", date(2026, 3, 1), date(2026, 8, 31)),
    ("2024年1月—2024年2月", date(2024, 1, 1), date(2024, 2, 29)),
])
def test_month_ranges_are_not_truncated(text, start, end):
    assert bot.parse_date_range(text) == (start, end)


@pytest.mark.parametrize("text", [
    "2026年3月到2026年8月及2026年10月表现",
    "2026-03-01至2026-08表现",
    "2026-03-01和2026-08-31表现",
    "2026年3月到2026年13月表现",
    "2026年3月至8月表现",
    "2026-03-01到03-31表现",
    "2026年3月到2026年8月及10月表现",
    "2026-03-01至2026-08-31及9月表现",
])
def test_ambiguous_or_invalid_date_fragments_are_rejected(text):
    with pytest.raises(ValueError):
        bot.parse_date_range(text)


def performance_inputs(monkeypatch, *, prior=True):
    index = pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07"])
    returns = pd.Series([0.0, -.10, -.20], index=index)
    prices = pd.Series([100.0, 90.0, 80.0], index=index)
    if not prior:
        returns, prices = returns.iloc[1:], prices.iloc[1:]
    monkeypatch.setattr(bot, "decode_returns", lambda product: returns.copy())
    monkeypatch.setattr(bot, "decode_benchmark_prices", lambda product, dates: prices.copy())
    return bot.performance_frame("IC", date(2026, 1, 6), date(2026, 1, 7))


def test_initial_losses_count_toward_drawdown_and_chart_return(monkeypatch):
    frame = performance_inputs(monkeypatch)
    assert frame["nav"].tolist() == pytest.approx([.9, .72])
    assert frame["drawdown"].tolist() == pytest.approx([-.10, -.28])
    assert frame["chart_nav"].tolist() == pytest.approx(frame["nav"].tolist())
    assert frame["benchmark_nav"].tolist() == pytest.approx([.9, .8])
    metrics = bot.performance_metrics(frame)
    assert frame["chart_nav"].iloc[-1] == pytest.approx(1 + metrics["return"])


def test_benchmark_missing_previous_close_is_not_silently_rebased(monkeypatch):
    frame = performance_inputs(monkeypatch, prior=False)
    assert frame["benchmark_nav"].isna().all()


@pytest.mark.parametrize("observations", [1, 2, 19])
def test_short_windows_do_not_publish_annualized_metrics(observations):
    frame = pd.DataFrame({"ret": [.01]*observations, "nav": 1.01**np.arange(1, observations+1),
                          "drawdown": [0.0]*observations})
    result = bot.performance_metrics(frame)
    assert result["cagr"] is None
    assert result["volatility"] is None
    assert result["sharpe"] is None
    assert result["return"] == pytest.approx(1.01**observations-1)


def test_empty_performance_window_has_a_clear_error():
    with pytest.raises(ValueError, match="观测|收益|为空"):
        bot.performance_metrics(pd.DataFrame())


def chain(month="2612", last=0.0):
    return pd.DataFrame([
        {"contract": f"510500P{month}M09500", "security_id": "10012000", "strike": 9.5, "last": last},
        {"contract": f"510500P{month}M09750", "security_id": "10012001", "strike": 9.75, "last": .2},
    ])


@pytest.mark.parametrize("last", [0.0, np.nan, np.inf, -1.0])
def test_ic_long_put_cannot_jump_away_from_an_unpriced_target(monkeypatch, last):
    monkeypatch.setattr(bot, "fetch_510500_expiries_with_failover", lambda: ["202612"])
    monkeypatch.setattr(bot, "_fetch_510500_chain_for_day", lambda *a: (chain(last=last), {}))
    monkeypatch.setattr(bot, "_require_selected_510500_quote_time", lambda *a, **kw: None)
    monkeypatch.setattr(bot, "_implied_volatility", lambda *a: .3)
    monkeypatch.setattr(bot, "_bs_price_delta", lambda *a: (.1, -.25))
    monkeypatch.setattr(bot, "_gov10y_for_day", lambda *a: .02)
    with pytest.raises(RuntimeError, match="09500|目标|报价"):
        bot.select_ic_put_for_reset(date(2026, 9, 30), 10.0, 8000.0, .25)


def test_ic_short_put_cannot_jump_away_from_an_unpriced_target(monkeypatch):
    monkeypatch.setattr(bot, "fetch_sse_510500_expiries", lambda: ["202609", "202610", "202612", "202703"])
    monkeypatch.setattr(bot, "_fetch_510500_chain_for_day", lambda *a: (chain("2610"), {}))
    monkeypatch.setattr(bot, "_validate_chain_stamp_matches", lambda *a: None)
    monkeypatch.setattr(bot, "_require_selected_510500_quote_time", lambda *a, **kw: None)
    monkeypatch.setattr(bot, "_implied_volatility", lambda *a: .3)
    monkeypatch.setattr(bot, "_bs_price_delta", lambda *a: (.1, -.25))
    monkeypatch.setattr(bot, "_gov10y_for_day", lambda *a: .02)
    result = bot._v14_ic_short_put_candidate({"market_date": date(2026, 9, 18),
        "next_trade_date": date(2026, 9, 21), "etf_price": 10.0, "future_last": 8000.0})
    assert result["tradable"] is False
    assert result["contract"] == "510500P2610M09500"


def test_source_failure_wording_is_not_proof_of_unlisted_month(monkeypatch):
    monkeypatch.setattr(bot, "fetch_sse_510500_expiries", lambda: ["202609", "202610", "202612", "202703"])
    def fail(*a):
        raise RuntimeError("有效510500 Put月份数据校验失败")
    monkeypatch.setattr(bot, "_fetch_510500_chain_for_day", fail)
    with pytest.raises(RuntimeError, match="数据校验失败"):
        bot._v14_ic_short_put_candidate({"market_date": date(2026, 9, 18),
                                      "next_trade_date": date(2026, 9, 21)})


@pytest.mark.parametrize("product,key", [
    ("IC", "verified_core_put_driver"),
    ("IC", "verified_momentum_put_driver"),
    ("IM", "verified_parent_puts"),
])
def test_anchor_install_rejects_missing_later_consumed_fields_atomically(monkeypatch, product, key):
    original = complete_anchors()
    monkeypatch.setattr(bot, "LIVE_CONTINUATION_ANCHOR", deepcopy(original))
    anchors = deepcopy(original)
    del anchors[product][key]
    with pytest.raises(ValueError, match=key):
        bot._install_runtime_anchors(anchors)
    assert bot.LIVE_CONTINUATION_ANCHOR == original


@pytest.mark.parametrize("value", [True, np.nan, -1, 1.5, 5])
def test_im_parent_put_tier_is_a_bounded_integer(monkeypatch, value):
    anchors = complete_anchors()
    monkeypatch.setattr(bot, "LIVE_CONTINUATION_ANCHOR", deepcopy(anchors))
    anchors["IM"]["verified_parent_puts"] = value
    with pytest.raises(ValueError, match="verified_parent_puts"):
        bot._install_runtime_anchors(anchors)


def test_one_product_render_failure_does_not_hide_the_other(monkeypatch):
    output = io.StringIO()
    @contextmanager
    def message():
        yield output
    monkeypatch.setattr(bot, "_sm", message)
    monkeypatch.setattr(bot, "_SIGNAL_OBSERVER", None)
    monkeypatch.setattr(bot, "build_live_signal_with_transport_retry", lambda *a: {"diagnostic_only": True})
    monkeypatch.setattr(bot, "_write_last_verified_snapshot", lambda *a: None)
    def render(msg, product, signal):
        if product == "IC":
            msg.write("UNFINISHED_IC_REPORT")
            raise KeyError("v14_profit_reentry_qty")
        msg.write("IM_RENDERED")
    monkeypatch.setattr(bot, "_write_diagnostic_signal", render)
    bot.ICIMMainlinesBot()._handle_signal(("IC", "IM"), "intraday")
    assert "IM_RENDERED" in output.getvalue()
    assert "信号展示失败" in output.getvalue()
    assert "UNFINISHED_IC_REPORT" not in output.getvalue()


@pytest.mark.parametrize("bad_first", [True, False])
def test_sina_invalid_contract_date_is_never_dropped_by_row_order(monkeypatch, bad_first):
    rows = [{"symbol": "IC2612", "tradedate": "not-a-date"},
            {"symbol": "IC2703", "tradedate": "2026-11-11"}]
    if not bad_first:
        rows.reverse()
    response = requests.Response()
    response.status_code = 200
    response._content = json.dumps(rows).encode()
    monkeypatch.setattr(bot, "_evidence_http_get", lambda *a, **kw: response)
    monkeypatch.setattr(bot, "_validate_quote_frame", lambda frame, *a: frame)
    with pytest.raises(RuntimeError, match="日期"):
        bot._fetch_sina_future_quotes("IC", datetime(2026, 11, 11, 14, tzinfo=bot.BEIJING))


def test_sina_newer_day_cannot_verify_a_previous_day_price(monkeypatch):
    fields = ["0"]*41
    fields[2], fields[5], fields[40] = "100", "5", "8"
    fields[32] = "2026-11-11 14:00:00"
    response = requests.Response()
    response.status_code = 200
    response._content = ('var hq_str_P_OP_mo2612P7400="'+','.join(fields)+'";').encode()
    monkeypatch.setattr(bot, "_evidence_http_get", lambda *a, **kw: response)
    with pytest.raises(RuntimeError, match="日期|同日|不一致"):
        bot.verify_sina_option_quote("MO2612-P-7400", date(2026, 11, 10), 1.0,
                                    datetime(2026, 11, 11, 14, 1, tzinfo=bot.BEIJING))


def test_transport_error_keeps_the_requests_exception_contract():
    request = requests.Request("GET", "https://example.invalid").prepare()
    response = requests.Response()
    error = bot.SignalTransportError("transport failed", request=request, response=response)
    assert isinstance(error, (RuntimeError, requests.ConnectionError))
    assert error.request is request
    assert error.response is response


def test_gov10y_is_consistent_within_a_query_but_not_cached_across_queries(monkeypatch):
    calls = []
    def resolve(*args):
        calls.append(args)
        return {"yield_decimal": .01 + .001*len(calls)}
    monkeypatch.setattr(bot.chinabond, "resolve", resolve)
    def build(product, clock, mode):
        return {"rates": [bot._gov10y_for_day(product, clock.date()),
                          bot._gov10y_for_day(product, clock.date())]}
    monkeypatch.setattr(bot, "_build_live_trade_signal", build)
    clock = datetime(2026, 11, 11, 16, tzinfo=bot.BEIJING)
    first = bot.build_live_trade_signal("IC", clock)
    second = bot.build_live_trade_signal("IC", clock)
    assert len(calls) == 2
    assert first["rates"][0] == first["rates"][1]
    assert second["rates"][0] == second["rates"][1]
    assert first["rates"][0] != second["rates"][0]


def test_chart_failure_leaves_no_pyplot_managed_figures(monkeypatch):
    import matplotlib.figure
    frame = performance_inputs(monkeypatch)
    before = set(bot.plt.get_fignums())
    def fail(*a, **kw):
        raise OSError("render failed")
    monkeypatch.setattr(matplotlib.figure.Figure, "savefig", fail)
    with pytest.raises(OSError, match="render failed"):
        bot.render_nav_drawdown_chart("IC", frame, date(2026, 1, 6))
    assert set(bot.plt.get_fignums()) == before


def test_unknown_exchange_year_stays_fail_closed():
    with pytest.raises(bot.CalendarCoverageError):
        bot._require_official_exchange_calendar(date(2028, 3, 1), "review regression")
