from __future__ import annotations

import pandas as pd

import im_put_four_tier_mom120_floor_scan_v3 as scan


def test_preregistered_spec_hash_matches() -> None:
    assert scan.sha256(scan.SPEC) == scan.SPEC_SHA256
    assert scan.SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0] == scan.SPEC_SHA256


def test_candidate_grid_contains_reference_and_full_range() -> None:
    assert [item["mom_floor_qty"] for item in scan.CANDIDATES] == [0, 1, 2, 3, 4]
    assert any(item["candidate"] == "IM_4tier_mom_floor_3" for item in scan.CANDIDATES)


def test_floor_is_maximum_of_valuation_and_negative_momentum(monkeypatch) -> None:
    base = pd.DataFrame(
        {
            "eval_date": pd.to_datetime(["2026-01-02", "2026-01-05", "2026-01-06"]),
            "execution_date": pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07"]),
            "momentum_120": [-0.10, -0.05, 0.10],
            "mom120_active": [True, True, False],
            "new_valuation_tier": [1, 4, 2],
            "binary_target_qty": [3, 4, 2],
            "three_tier_target_qty": [3, 4, 2],
        }
    )

    def fake_build_schedule(*_args, **_kwargs):
        return base.copy()

    monkeypatch.setattr(scan.im_v2, "build_schedule", fake_build_schedule)
    result = scan.build_schedule(
        pd.DataFrame(),
        pd.DataFrame(),
        pd.DataFrame(),
        next(item for item in scan.CANDIDATES if item["mom_floor_qty"] == 3),
    )
    assert result["binary_target_qty"].tolist() == [3, 4, 2]
    assert result["mom_floor_binding"].tolist() == [True, False, False]


def test_four_put_floor_binds_on_all_negative_days(monkeypatch) -> None:
    base = pd.DataFrame(
        {
            "eval_date": pd.to_datetime(["2026-01-02", "2026-01-05"]),
            "execution_date": pd.to_datetime(["2026-01-05", "2026-01-06"]),
            "momentum_120": [-0.10, 0.10],
            "mom120_active": [True, False],
            "new_valuation_tier": [1, 3],
            "binary_target_qty": [3, 3],
            "three_tier_target_qty": [3, 3],
        }
    )
    monkeypatch.setattr(scan.im_v2, "build_schedule", lambda *_args, **_kwargs: base.copy())
    result = scan.build_schedule(
        pd.DataFrame(),
        pd.DataFrame(),
        pd.DataFrame(),
        next(item for item in scan.CANDIDATES if item["mom_floor_qty"] == 4),
    )
    assert result["binary_target_qty"].tolist() == [4, 3]

