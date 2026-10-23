import pandas as pd
import pytest
import json

import im_front_month_call_overwrite_v1 as research


def chain() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"contract": "MO2401-C-100", "close": 5.0, "settle": 5.0, "volume": 10, "open_interest": 20, "strike": 100.0},
            {"contract": "MO2401-C-102", "close": 4.0, "settle": 4.0, "volume": 9, "open_interest": 30, "strike": 102.0},
            {"contract": "MO2401-C-104", "close": 3.0, "settle": 3.0, "volume": 8, "open_interest": 40, "strike": 104.0},
        ]
    )


def test_atm_selects_nearest_strike() -> None:
    selected = research.choose_call(chain(), 101.0, 0.0)
    assert selected["strike"] == 102.0


def test_otm_candidate_cannot_select_itm_strike() -> None:
    selected = research.choose_call(chain(), 101.0, 0.015)
    assert selected["strike"] == 102.0
    assert selected["strike"] > 101.0


def test_im_contract_month() -> None:
    assert research.im_contract_month("IM2608") == pd.Timestamp("2026-08-01")
    with pytest.raises(RuntimeError):
        research.im_contract_month("IC2608")


def test_spec_hash_is_frozen() -> None:
    assert research.sha256(research.SPEC) == research.SPEC_SHA256
    assert research.SPEC_HASH.read_text(encoding="utf-8").split()[0] == research.SPEC_SHA256


def test_cash_rate_matches_three_percent() -> None:
    assert (1 + research.CASH_DAILY) ** 252 - 1 == pytest.approx(0.03)
    assert research.CASH_BASE == pytest.approx(0.70)


def test_expiry_zero_settlement_is_valid_in_official_data() -> None:
    _, _, calls = research.load_inputs()
    row = calls[
        calls["contract"].eq("MO2209-C-7200")
        & calls["date"].eq(pd.Timestamp("2022-09-16"))
    ].iloc[0]
    assert row["settle"] == pytest.approx(0.0)


def test_formal_output_is_complete_and_audited() -> None:
    metrics = pd.read_csv(research.OUTPUT / "metrics_by_window.csv")
    assert set(metrics["candidate"]) == set(research.CANDIDATES)
    assert len(metrics) == len(research.CANDIDATES) * len(research.WINDOWS)
    unavailable = metrics[metrics["segment"].isin(["last_10y", "last_5y"])]
    assert not unavailable["available"].any()
    assert unavailable[["ann_return", "max_dd"]].isna().all().all()
    audit = json.loads((research.OUTPUT / "audit_summary.json").read_text(encoding="utf-8"))
    assert audit["all_pass"] is True
    assert audit["month_match_errors"] == 0
    assert audit["causality_errors"] == 0


def test_formal_result_keeps_roll_im_baseline() -> None:
    metrics = pd.read_csv(research.OUTPUT / "metrics_by_window.csv")
    full = metrics[metrics["segment"].eq("full")].set_index("candidate")
    for candidate in research.MONEYNESS:
        assert full.loc[candidate, "ann_return"] < full.loc[research.BASELINE, "ann_return"]


def test_formal_baseline_cash_path_matches_upstream() -> None:
    paths = pd.read_csv(research.OUTPUT / "daily_paths.csv.gz")
    formal = paths[paths["candidate"].eq(research.BASELINE)].sort_values("date")
    upstream = pd.read_csv(research.UPSTREAM_DAILY).sort_values("date")
    assert formal["cash_ret"].to_numpy() == pytest.approx(
        upstream["im_net_plus_cash_ret"].to_numpy(), abs=1e-15
    )
