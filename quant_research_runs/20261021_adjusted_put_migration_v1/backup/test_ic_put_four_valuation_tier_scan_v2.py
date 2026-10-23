from __future__ import annotations

import pandas as pd

import ic_put_four_valuation_tier_scan_v2 as scan


def _base() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "unbounded_median_knot": [1.80, 2.03, 2.08, 2.12],
            "target_delta": [0.25, 0.50, 0.50, 0.75],
            "momentum_floor_on": [True, False, False, False],
            "risk_tier": [1, 2, 2, 3],
        }
    )


def test_four_tier_mapping_and_momentum_floor() -> None:
    definition = next(
        item
        for item in scan.CANDIDATES
        if item["candidate"] == "IC_4tier_1900_2000_2050_2100"
    )
    result = scan.build_schedule(_base(), definition)
    assert result["valuation_tier_new"].tolist() == [0, 2, 3, 4]
    assert result["target_delta"].tolist() == [0.25, 0.50, 0.75, 1.00]
    assert result["risk_tier"].tolist() == [1, 2, 3, 4]


def test_existing_top_only_promotes_old_highest_tier() -> None:
    definition = next(item for item in scan.CANDIDATES if item["policy"] == "existing_top")
    result = scan.build_schedule(_base(), definition)
    assert result["target_delta"].tolist() == [0.25, 0.50, 0.50, 1.00]


def test_all_four_tier_thresholds_strictly_increase() -> None:
    for definition in scan.CANDIDATES:
        if definition["policy"] != "four_tier":
            continue
        values = definition["thresholds"]
        assert all(right > left for left, right in zip(values, values[1:]))
