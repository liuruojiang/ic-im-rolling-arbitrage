import pandas as pd

import ic_valuation_overlay_selected_put_sync_v5 as scan


def test_selected_pair_bundle_is_frozen() -> None:
    assert scan.PAIRS == ((0.375, 1.0), (0.5, 1.0), (0.375, 0.875))
    assert len(set(scan.PAIRS)) == 3
    assert scan.PRIMARY_PAIR == (0.375, 1.0)
    assert scan.PUT_MODES == ("core_put_only", "sync_put_total_ic")


def test_candidate_labels_match_v1() -> None:
    assert (
        scan.candidate_label("model", 0.375, 1.0, "sync_put_total_ic")
        == "model__L0.38_H1.00__sync_put_total_ic"
    )
    assert scan.pair_label(0.375, 0.875) == "L0.38_H0.88"


def test_timing_audit_requires_same_day_trade_when_target_changes() -> None:
    schedules = pd.DataFrame(
        {
            "candidate": ["c", "c"],
            "execution_date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
            "core_target_delta": [0.25, 0.25],
            "target_delta": [0.50, 0.25],
        }
    )
    overlay = pd.DataFrame(
        {
            "candidate": ["c", "c"],
            "put_mode": ["sync_put_total_ic", "sync_put_total_ic"],
            "layer": ["model", "model"],
            "pair": ["p", "p"],
            "action": ["buy", "sell"],
            "signal_date": pd.to_datetime(["2024-01-01", "2024-01-02"]),
            "execution_date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
            "execution_open": [100.0, 110.0],
        }
    )
    puts = pd.DataFrame(
        {
            "candidate": ["c", "c"],
            "actual_execution_date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
        }
    )
    audit = scan.timing_audit(schedules, overlay, puts)
    assert audit["target_formula_error"].max() == 0
    assert audit["same_day_trade_pass"].all()
