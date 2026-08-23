from __future__ import annotations

import hashlib

import pandas as pd

import im_mo_put_strike_anchor_scan_v1 as study


def test_preregistered_spec_hash_matches() -> None:
    assert hashlib.sha256(study.SPEC.read_bytes()).hexdigest() == study.SPEC_SHA256
    assert study.SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0] == study.SPEC_SHA256


def test_candidate_grid_contains_current_and_proposed_95() -> None:
    labels = {
        study.candidate_name(anchor, moneyness)
        for anchor in study.ANCHORS
        for moneyness in study.MONEYNESS
    }
    assert "active_im_m095" in labels
    assert "csi1000_spot_m095" in labels
    assert "matched_expiry_im_m095" in labels
    assert len(labels) == 9


def test_reference_price_semantics() -> None:
    day = pd.Timestamp("2026-08-14")
    month = pd.Timestamp("2026-11-01")
    active = pd.Series([7730.8], index=[day])
    spot = pd.Series([7769.82], index=[day])
    quotes = pd.DataFrame(
        {
            "contract": ["IM2611"],
            "date": [day],
            "close": [7600.0],
            "volume": [100.0],
            "open_interest": [200.0],
        }
    ).set_index(["contract", "date"])
    kwargs = {
        "active_lookup": active,
        "spot_lookup": spot,
        "im_quote_lookup": quotes,
    }
    assert study.reference_price("active_im", day, month, **kwargs) == 7730.8
    assert study.reference_price("csi1000_spot", day, month, **kwargs) == 7769.82
    assert study.reference_price("matched_expiry_im", day, month, **kwargs) == 7600.0


def test_select_by_reference_uses_nearest_liquid_strike() -> None:
    day = pd.Timestamp("2026-08-14")
    month = pd.Timestamp("2026-11-01")
    options = pd.DataFrame(
        {
            "date": [day, day, day],
            "contract_month": [month, month, month],
            "contract": ["MO2611-P-7300", "MO2611-P-7400", "MO2611-P-7500"],
            "strike": [7300.0, 7400.0, 7500.0],
            "close": [20.0, 25.0, 30.0],
            "volume": [0.0, 10.0, 10.0],
            "open_interest": [100.0, 100.0, 100.0],
        }
    )
    selected = study.select_by_reference(options, day, month, 0.95, 7769.82)
    assert selected is not None
    assert selected["contract"] == "MO2611-P-7400"
    assert abs(float(selected["entry_moneyness"]) - 7400.0 / 7769.82) < 1e-12


def test_real_parent_schedule_is_t_plus_one_and_zero_to_four() -> None:
    inputs = study.load_research_inputs()
    schedule = inputs["schedule"]
    assert (schedule["execution_date"] > schedule["eval_date"]).all()
    assert schedule["binary_target_qty"].between(0, 4).all()
    assert len(schedule) == len(inputs["upstream"])
    assert inputs["upstream"]["contract"].iloc[0] == "IM2208"
    assert inputs["upstream"]["contract"].iloc[-1] == "IM2608"
