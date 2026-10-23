from __future__ import annotations

import pandas as pd
import pytest

import im_roll50_momentum50_fullcycle_put_v4 as study


def test_new_quantity_never_exceeds_old_v3_and_bare_floor_remains():
    frame = pd.DataFrame(
        {
            "full_qty": [4.0, 4.0, 2.0],
            "valuation_qty": [0.0, 2.0, 2.0],
            "momentum_weight": [1.0, 0.5, 1.0],
        }
    )
    bare = 0.5 * frame["full_qty"]
    new = bare + 0.5 * frame["momentum_weight"] * frame["valuation_qty"]
    old = (0.5 + 0.5 * frame["momentum_weight"]) * frame["full_qty"]
    assert bare.tolist() == pytest.approx([2.0, 2.0, 1.0])
    assert new.tolist() == pytest.approx([2.0, 2.5, 2.0])
    assert (new <= old + 1e-12).all()


def test_frozen_spec_hash_matches():
    assert study.sha256(study.SPEC) == study.SPEC_SHA256
    assert study.SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0] == study.SPEC_SHA256
