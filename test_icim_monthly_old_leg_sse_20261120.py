"""Real November 20 closing chain/master reconciliation and negative controls."""
from copy import deepcopy
from datetime import date, datetime
import json
from pathlib import Path

import pandas as pd
import pytest

import ic_im_expiry_evidence_v1 as expiry
import poe_ic_im_mainline_v1_4_bot as bot

BASE = Path(__file__).resolve().parent / "quant_research_runs/20261120_monthly_old_leg_repair/fixtures"
DAY = date(2026, 11, 20)
CLOCK = datetime.fromisoformat("2026-11-20T22:15:00+08:00")
NEXT = date(2026, 11, 23)


def observed(month):
    master = json.loads((BASE / f"sse_master_{month}.json").read_text(encoding="utf-8"))
    quote = json.loads((BASE / f"sse_chain_{month}.json").read_text(encoding="utf-8"))
    rows = bot.sse_identity.validate_chain_rows(quote, month)
    frame = pd.DataFrame(rows, columns=["contract", "last", "chg_rate", "pre_settle", "strike", "security_id"])
    return bot._numeric_frame(frame, skip=("contract", "security_id")), master


@pytest.mark.parametrize("month,expected", [("2611", 30), ("2612", 62), ("2703", 34), ("2706", 22)])
def test_actual_close_excludes_only_next_sessions_unlisted_securities(month, expected):
    frame, master = observed(month)
    actual_expiry = bot._fourth_wednesday(2000 + int(month[:2]), int(month[2:]))
    result = expiry.verify_sse_chain(frame, master, as_of=DAY, month="20" + month,
                                     model_expiry=actual_expiry, observation_clock=CLOCK,
                                     next_exchange_session=NEXT)
    assert len(result) == expected == len(frame)
    excluded = result.attrs["expiry_evidence"]["excluded_not_yet_listed_security_ids"]
    assert len(excluded) == 2 and set(excluded).isdisjoint(result.security_id)
    assert set(result.security_id) == {row["SECURITY_ID"] for row in master["result"]
                                      if row["START_DATE"] <= "20261120"}


@pytest.mark.parametrize("fault", ["not_close", "wrong_session", "missing_today", "future_in_quotes",
                                     "duplicate_next", "future_date_mismatch", "bad_future_terms"])
def test_future_announcements_do_not_relax_master_integrity(fault):
    month = "2612"
    frame, master = observed(month)
    clock, next_day = CLOCK, NEXT
    next_row = next(row for row in master["result"] if row["START_DATE"] == "20261123")
    if fault == "not_close":
        clock = CLOCK.replace(hour=14)
    elif fault == "wrong_session":
        next_day = date(2026, 11, 24)
    elif fault == "missing_today":
        frame = frame.iloc[:-1]
    elif fault == "future_in_quotes":
        frame = pd.concat([frame, pd.DataFrame([{"contract": next_row["CONTRACT_ID"],
                           "security_id": next_row["SECURITY_ID"], "strike": 5.0}])], ignore_index=True)
    elif fault == "duplicate_next":
        master["result"].append(deepcopy(next_row))
    elif fault == "future_date_mismatch":
        next_row["START_DATE"] = "20261124"
    elif fault == "bad_future_terms":
        next_row["EXERCISE_DATE"] = "20261224"
    with pytest.raises(expiry.ExpiryEvidenceConflict):
        expiry.verify_sse_chain(frame, master, as_of=DAY, month="202612",
                                model_expiry=date(2026, 12, 23), observation_clock=clock,
                                next_exchange_session=next_day)
