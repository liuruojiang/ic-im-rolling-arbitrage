from __future__ import annotations

import pandas as pd

import ic_put_four_tier_mom120_floor_scan_v3 as scan


def _base(momentum: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "unbounded_median_knot": [1.80, 1.80, 2.03, 2.12],
            "momentum_120": momentum,
            "target_delta": [0.25, 0.25, 0.50, 0.75],
            "risk_tier": [1, 1, 2, 3],
        }
    )


def _definition(family: str, floor: float) -> dict:
    return next(
        item
        for item in scan.CANDIDATES
        if item["family"] == family and abs(item["mom_floor"] - floor) < 1e-12
    )


def test_momentum_floor_grid_and_valuation_dominance() -> None:
    base = _base([-0.1, 0.1, -0.1, -0.1])
    result0 = scan.build_schedule(base, _definition("cons4", 0.0))
    result25 = scan.build_schedule(base, _definition("cons4", 0.25))
    result50 = scan.build_schedule(base, _definition("cons4", 0.50))
    assert result0["target_delta"].tolist() == [0.0, 0.0, 0.50, 1.00]
    assert result25["target_delta"].tolist() == [0.25, 0.0, 0.50, 1.00]
    assert result50["target_delta"].tolist() == [0.50, 0.0, 0.50, 1.00]


def test_strictly_negative_boundary_does_not_include_zero() -> None:
    base = _base([0.0, -0.1, 0.0, 0.0])
    result = scan.build_schedule(base, _definition("cons4", 0.75))
    assert result["target_delta"].tolist() == [0.0, 0.75, 0.50, 1.00]


def test_candidate_grid_is_complete() -> None:
    assert len(scan.CANDIDATES) == 11
    for family in scan.FAMILIES:
        floors = sorted(
            item["mom_floor"] for item in scan.CANDIDATES if item["family"] == family
        )
        assert floors == list(scan.FLOORS)
