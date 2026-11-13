"""Real saved close data and adversarial controls for the held-only fallback."""
from copy import deepcopy
from datetime import date, datetime
import hashlib
import json
from pathlib import Path
import re

import pytest

import ic_im_held_put_close_v1 as close

FIXTURES = Path(__file__).resolve().parent / "quant_research_runs/20261113_ic_held_close_v1/fixtures"


def valid_inputs():
    return dict(
        anchor=dict(last_verified_day=date(2026, 11, 12), v14_route_state="future",
                    v14_core_put_contract="510500P2612M06500", v14_core_put_security_id="10012149",
                    v14_core_put_qty=15),
        signal_day=date(2026, 11, 13), expected_previous_day=date(2026, 11, 12),
        current_ledger_day=date(2026, 11, 12), expected_expiry=date(2026, 12, 23),
        observed_at=datetime.fromisoformat("2026-11-13T20:41:00+08:00"),
        detail_raw=(FIXTURES / "detail.bin").read_bytes(),
        identity_raw=(FIXTURES / "identity.bin").read_bytes(),
        sina_daily_raw=(FIXTURES / "sina_daily.bin").read_bytes(),
        eastmoney_daily_raw=(FIXTURES / "eastmoney_daily.bin").read_bytes(),
        official_master=json.loads((FIXTURES / "official_master.json").read_text(encoding="utf-8")),
    )


def replace_packet(raw, field, value):
    text = raw.decode("gb18030")
    first, body, last = text.split('"')
    fields = body.split(",")
    fields[field] = value
    return (first + '"' + ",".join(fields) + '"' + last).encode("gb18030")


def sina_rows(raw):
    return json.loads(re.search(r"\((\[.*\])\)\s*;?\s*$", raw.decode(), flags=re.DOTALL).group(1))


def save_sina(rows):
    return ("(" + json.dumps(rows, ensure_ascii=False) + ");").encode()


def test_saved_actual_midnight_source_can_supply_identity_but_never_iv():
    inputs = valid_inputs()
    before = deepcopy(inputs)
    result = close.validate_held_put_close(**inputs)
    assert inputs == before
    assert result["row"] == dict(contract="510500P2612M06500", security_id="10012149", strike=6.5,
                                 last=0.2703, expiry=date(2026, 12, 23), contract_unit=10000)
    evidence = result["evidence"]
    assert evidence["identity_source_stamp"] == "2026-11-13T00:00:00+08:00"
    assert evidence["official_master_snapshot_day"] == "2026-11-12"
    assert evidence["iv_monitor_eligible"] is False and evidence["quote_time"] is None
    assert "source_stamp" not in result["row"]
    assert evidence["identity_time_used_for_freshness"] is False
    assert evidence["new_listing_or_candidate_certified"] is False
    assert evidence["raw_sha256"]["detail"] == hashlib.sha256(inputs["detail_raw"]).hexdigest()


@pytest.mark.parametrize("clock", ["2026-11-13T15:29:59+08:00", "2026-11-13T09:30:00+08:00",
                                  "2026-11-14T00:01:00+08:00", "2026-11-12T20:00:00+08:00"])
def test_actual_observation_scope_is_same_day_after_cutoff(clock):
    inputs = valid_inputs()
    inputs["observed_at"] = datetime.fromisoformat(clock)
    with pytest.raises(close.HeldPutCloseUnavailable):
        close.validate_held_put_close(**inputs)


@pytest.mark.parametrize("key,value", [("v14_core_put_contract", "510500P2612A06500"),
    ("v14_core_put_contract", "510500C2612M06500"), ("v14_core_put_security_id", "10012150"),
    ("v14_core_put_qty", 0), ("v14_core_put_qty", 0.5), ("v14_core_put_qty", True),
    ("v14_core_put_qty", float("nan")), ("v14_core_put_qty", float("inf")),
    ("v14_route_state", "short_put"), ("last_verified_day", date(2026, 11, 11))])
def test_cannot_reassign_or_create_unverified_held_identity(key, value):
    inputs = valid_inputs()
    inputs["anchor"][key] = value
    with pytest.raises((close.HeldPutCloseUnavailable, close.HeldPutCloseConflict)):
        close.validate_held_put_close(**inputs)


@pytest.mark.parametrize("field,value", [(7, "6.75"), (32, "2026-11-12 00:00:00"),
    (32, "2026-11-13 23:59:59"), (36, "510050"), (43, "A"), (45, "C"), (46, "2026-12-24")])
def test_actual_detail_packet_identity_conflicts_rejected(field, value):
    inputs = valid_inputs()
    inputs["detail_raw"] = replace_packet(inputs["detail_raw"], field, value)
    with pytest.raises(close.HeldPutCloseConflict):
        close.validate_held_put_close(**inputs)


@pytest.mark.parametrize("field,value", [(12, "510500P2612M06750"), (13, "6.75"), (16, "A")])
def test_complete_trading_code_must_match_current_security(field, value):
    inputs = valid_inputs()
    inputs["identity_raw"] = replace_packet(inputs["identity_raw"], field, value)
    with pytest.raises(close.HeldPutCloseConflict):
        close.validate_held_put_close(**inputs)


def test_duplicate_identity_packet_not_silently_first_selected():
    inputs = valid_inputs()
    inputs["identity_raw"] *= 2
    with pytest.raises(close.HeldPutCloseUnavailable):
        close.validate_held_put_close(**inputs)


@pytest.mark.parametrize("edit", ["missing_day", "duplicate_day", "future_day", "price_conflict", "wrong_market", "wrong_id", "bad_ohlc"])
def test_eastmoney_actual_dayline_faults_cannot_be_masked_by_good_sina(edit):
    inputs = valid_inputs()
    payload = json.loads(inputs["eastmoney_daily_raw"])
    rows = payload["data"]["klines"]
    if edit == "missing_day":
        rows.pop()
    elif edit == "duplicate_day":
        rows.append(rows[-1])
    elif edit == "future_day":
        rows.append(rows[-1].replace("2026-11-13", "2026-11-16"))
    elif edit == "price_conflict":
        rows[-1] = rows[-1].replace(",0.2703,", ",0.2704,")
    elif edit == "wrong_market":
        payload["data"]["market"] = True
    elif edit == "wrong_id":
        payload["data"]["code"] = "10012150"
    else:
        rows[-1] = rows[-1].replace(",0.1674,", ",0.2750,")
    inputs["eastmoney_daily_raw"] = json.dumps(payload).encode()
    with pytest.raises((close.HeldPutCloseUnavailable, close.HeldPutCloseConflict)):
        close.validate_held_put_close(**inputs)


@pytest.mark.parametrize("edit", ["missing_day", "duplicate_day", "future_day", "nan", "boolean", "wrapper"])
def test_sina_actual_dayline_faults_cannot_be_masked_by_good_eastmoney(edit):
    inputs = valid_inputs()
    rows = sina_rows(inputs["sina_daily_raw"])
    if edit == "missing_day":
        rows.pop()
    elif edit == "duplicate_day":
        rows.append(rows[-1])
    elif edit == "future_day":
        rows.append({**rows[-1], "d": "2026-11-16"})
    elif edit == "nan":
        rows[-1]["c"] = float("nan")
    elif edit == "boolean":
        rows[-1]["c"] = True
    inputs["sina_daily_raw"] = save_sina(rows)
    if edit == "wrapper":
        inputs["sina_daily_raw"] = b"wrong_symbol" + inputs["sina_daily_raw"]
    with pytest.raises((close.HeldPutCloseUnavailable, close.HeldPutCloseConflict)):
        close.validate_held_put_close(**inputs)


def test_duplicate_json_key_is_conflict_not_last_value_wins():
    inputs = valid_inputs()
    inputs["eastmoney_daily_raw"] = inputs["eastmoney_daily_raw"].replace(b'"market":10', b'"market":1,"market":10')
    with pytest.raises(close.HeldPutCloseConflict, match="重复字段"):
        close.validate_held_put_close(**inputs)


@pytest.mark.parametrize("field,value", [("CONTRACT_UNIT", "9999"), ("EXPIRE_DATE", "20261224"),
    ("CONTRACT_ID", "510500P2612A06500"), ("EXERCISE_PRICE", "6.75")])
def test_stale_official_master_still_preserves_positive_identity_conflicts(field, value):
    inputs = valid_inputs()
    row = next(row for row in inputs["official_master"]["result"] if row["SECURITY_ID"] == "10012149")
    row[field] = value
    with pytest.raises(close.HeldPutCloseConflict):
        close.validate_held_put_close(**inputs)


def test_cannot_use_for_monthly_new_selection_or_earlier_policy():
    inputs = valid_inputs()
    inputs["monthly_reset"] = True
    with pytest.raises(close.HeldPutCloseUnavailable):
        close.validate_held_put_close(**inputs)
    inputs["monthly_reset"] = False
    inputs["signal_day"] = date(2026, 11, 12)
    with pytest.raises(close.HeldPutCloseUnavailable):
        close.validate_held_put_close(**inputs)


def test_old_master_is_optional_conflict_evidence_not_current_listing_proof():
    inputs = valid_inputs()
    inputs["official_master"] = None
    result = close.validate_held_put_close(**inputs)
    assert result["evidence"]["official_master_snapshot_day"] is None
    assert result["evidence"]["official_master_conflict_check_only"] is False
    assert result["evidence"]["new_listing_or_candidate_certified"] is False


@pytest.mark.parametrize("number", ["1e10000", "1e-10000"])
def test_independent_adversarial_float_overflow_and_underflow_rejected(number):
    inputs = valid_inputs()
    rows = sina_rows(inputs["sina_daily_raw"])
    for field in ("o", "h", "l", "c"):
        rows[-1][field] = number
    inputs["sina_daily_raw"] = save_sina(rows)
    payload = json.loads(inputs["eastmoney_daily_raw"])
    fields = payload["data"]["klines"][-1].split(",")
    fields[1:5] = [number] * 4
    payload["data"]["klines"][-1] = ",".join(fields)
    inputs["eastmoney_daily_raw"] = json.dumps(payload).encode()
    with pytest.raises(close.HeldPutCloseUnavailable, match="可表示范围"):
        close.validate_held_put_close(**inputs)


def test_extremely_large_anchor_quantity_fails_with_typed_unavailable():
    inputs = valid_inputs()
    inputs["anchor"]["v14_core_put_qty"] = 10 ** 1000
    with pytest.raises(close.HeldPutCloseUnavailable, match="正整数"):
        close.validate_held_put_close(**inputs)
