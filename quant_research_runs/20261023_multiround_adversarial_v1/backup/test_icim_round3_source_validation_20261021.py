"""Source-boundary regressions; mutated ETF/month payloads are synthetic.

The unchanged option-chain oracle is a retained real 2026-10-21 SSE response.
These tests do not establish real pre-open or morning daily-bar availability.
"""
from copy import deepcopy
from datetime import date, datetime
import json
from pathlib import Path

import numpy as np
import pytest

import ic_im_signal_evidence_v1 as evidence
import poe_ic_im_mainline_v1_4_bot as bot


DAY = date(2026, 10, 21)
CLOCK = datetime(2026, 10, 21, 15, 30, tzinfo=bot.BEIJING)


def etf_payload():
    return {"date": "20261021", "time": "150000", "total": 1, "begin": 0, "end": 1,
            "list": [["510500", "ETF", 7.8, 0, 0, 0, 1000, 7800, 7.7]]}


def months_payload():
    return {"date": "20261021", "time": "150000", "total": 4, "begin": 0, "end": 4,
            "list": [["510300", "202612"], ["510500", "202611"],
                     ["510500", "202612"], ["510500", "202703"]]}


def wire(monkeypatch, payload):
    monkeypatch.setattr(bot, "_sse_quote_json", lambda *a, **kw: deepcopy(payload))


@pytest.mark.parametrize("index", [2, 8])
@pytest.mark.parametrize("value", [True, np.bool_(True), False, None, float("nan"), float("inf"), 0, -1])
def test_etf_raw_price_rejected_before_numeric_coercion(monkeypatch, index, value):
    payload = etf_payload()
    payload["list"][0][index] = value
    wire(monkeypatch, payload)
    with pytest.raises((RuntimeError, ValueError, TypeError)):
        bot.fetch_sse_510500_quote()


@pytest.mark.parametrize("code", ["510300", "sh510500", "５１０５００", None, True, ""])
def test_etf_response_must_bind_the_requested_underlying(monkeypatch, code):
    payload = etf_payload()
    payload["list"][0][0] = code
    wire(monkeypatch, payload)
    with pytest.raises(RuntimeError):
        bot.fetch_sse_510500_quote()


@pytest.mark.parametrize("kind", ["duplicate", "extra_underlying", "short_row"])
def test_etf_response_must_be_one_complete_selected_row(monkeypatch, kind):
    payload = etf_payload()
    if kind == "short_row":
        payload["list"][0] = payload["list"][0][:8]
    else:
        second = deepcopy(payload["list"][0])
        if kind == "extra_underlying":
            second[0] = "510300"
        payload["list"].append(second)
        payload.update(total=2, end=2)
    wire(monkeypatch, payload)
    with pytest.raises((RuntimeError, ValueError, TypeError)):
        bot.fetch_sse_510500_quote()


@pytest.mark.parametrize("source", ["etf", "months"])
@pytest.mark.parametrize("field,value", [("total", 100), ("begin", 1), ("end", 0), ("total", True), ("end", "1")])
def test_explicit_sse_partial_or_unverified_pagination_cannot_be_ignored(monkeypatch, source, field, value):
    payload = etf_payload() if source == "etf" else months_payload()
    payload[field] = value
    wire(monkeypatch, payload)
    with pytest.raises(RuntimeError):
        (bot.fetch_sse_510500_quote if source == "etf" else bot.fetch_sse_510500_expiries)()


@pytest.mark.parametrize("value", ["202713", "202700", "2703", "2027-03", "２０２７０３", True, None, ""])
def test_invalid_month_for_requested_etf_cannot_silently_remove_a_candidate(monkeypatch, value):
    payload = months_payload()
    payload["list"][-1][1] = value
    wire(monkeypatch, payload)
    with pytest.raises(RuntimeError):
        bot.fetch_sse_510500_expiries()


@pytest.mark.parametrize("source", ["etf", "months"])
def test_partial_pagination_metadata_is_not_a_verified_full_response(monkeypatch, source):
    payload = etf_payload() if source == "etf" else months_payload()
    del payload["end"]
    wire(monkeypatch, payload)
    with pytest.raises(RuntimeError):
        (bot.fetch_sse_510500_quote if source == "etf" else bot.fetch_sse_510500_expiries)()


@pytest.mark.parametrize("price", [7.8, "7.8", 8])
def test_valid_etf_projection_keeps_original_output_contract(monkeypatch, price):
    payload = etf_payload()
    payload["list"][0][2] = price
    wire(monkeypatch, payload)
    assert bot.fetch_sse_510500_quote() == {
        "date": "20261021", "time": "150000", "last": float(price), "prev_close": 7.7}


def test_months_are_complete_sorted_and_other_underlyings_remain_out_of_scope(monkeypatch):
    payload = months_payload()
    payload["list"][0][1] = "an unrelated source value"
    payload["list"].append(["510500", "202611"])
    payload.update(total=5, end=5)
    wire(monkeypatch, payload)
    assert bot.fetch_sse_510500_expiries() == ["202611", "202612", "202703"]


@pytest.mark.parametrize("source", ["etf", "months"])
def test_existing_projection_without_pagination_fields_remains_compatible(monkeypatch, source):
    payload = etf_payload() if source == "etf" else months_payload()
    for field in ["total", "begin", "end"]:
        del payload[field]
    wire(monkeypatch, payload)
    result = (bot.fetch_sse_510500_quote if source == "etf" else bot.fetch_sse_510500_expiries)()
    assert result


@pytest.mark.parametrize("source", ["etf_boolean", "etf_wrong_code", "months"])
def test_bad_original_source_never_becomes_a_complete_archive(tmp_path, monkeypatch, source):
    payload = months_payload() if source == "months" else etf_payload()
    if source == "etf_boolean":
        payload["list"][0][2] = True
    elif source == "etf_wrong_code":
        payload["list"][0][0] = "510300"
    else:
        payload["list"][-1][1] = "202713"
    wire(monkeypatch, payload)
    with bot.runtime_clock(CLOCK), evidence.capture_session(tmp_path, DAY, CLOCK, {"purpose": "synthetic regression"}) as session:
        with pytest.raises(RuntimeError):
            (bot.fetch_sse_510500_expiries if source == "months" else bot.fetch_sse_510500_quote)()
    assert session.context["complete"] is False
    assert session.context["entries"] == {}
    with pytest.raises(evidence.EvidenceUnavailable):
        evidence.load_session(tmp_path, DAY)


def test_retained_real_sse_option_chain_still_parses_all_original_identities(monkeypatch):
    path = Path(__file__).resolve().parent / "tests/fixtures/icim_external_review_round2_20261021/sse_december_identity.json"
    payload = json.loads(path.read_bytes())
    wire(monkeypatch, payload)
    frame, stamp = bot.fetch_sse_510500_chain("2612")
    assert len(frame) == payload["total"] == 58
    assert frame["security_id"].tolist() == [row[5] for row in payload["list"]]
    assert stamp["date"] == "20261021"
