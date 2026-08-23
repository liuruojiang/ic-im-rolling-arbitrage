from __future__ import annotations

import pandas as pd
import pytest

import ic_roll_momentum_stage2_put_v2 as study


def test_new_schedule_keeps_full_floor_on_bare_and_removes_it_from_momentum():
    selected = pd.DataFrame(
        {
            "valuation_tier_new": [0, 1, 2, 4],
            "v2_target_delta": [0.5, 0.5, 0.5, 1.0],
            "momentum_weight": [0.0, 0.5, 1.0, 1.0],
        }
    )
    out = study.build_new_schedule(selected)
    assert out["bare_full_target_delta"].tolist() == pytest.approx([0.25, 0.25, 0.25, 0.5])
    assert out["momentum_valuation_target_delta"].tolist() == pytest.approx([0.0, 0.0625, 0.25, 0.5])
    assert out["target_delta"].tolist() == pytest.approx([0.25, 0.3125, 0.5, 1.0])


def test_frozen_spec_hash_matches():
    assert study.sha256(study.SPEC) == study.SPEC_SHA256
    assert study.SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0] == study.SPEC_SHA256
