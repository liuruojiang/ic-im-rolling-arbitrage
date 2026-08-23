from __future__ import annotations

import hashlib

import pandas as pd

import im_mo_put_strike_anchor_scan_v2 as study


def test_preregistered_v2_spec_hash_matches() -> None:
    assert hashlib.sha256(study.SPEC.read_bytes()).hexdigest() == study.SPEC_SHA256
    assert study.SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0] == study.SPEC_SHA256


def decision_fixture(
    *,
    active_full_return: float = 0.19,
    spot_full_return: float = 0.186,
    active_full_dd: float = -0.125,
    spot_full_dd: float = -0.125,
    spot_recent_better: bool = True,
) -> pd.DataFrame:
    rows = []
    for anchor in ("active_im", "csi1000_spot"):
        for moneyness in (90, 95, 100):
            is_spot = anchor == "csi1000_spot"
            full_return = (
                spot_full_return if is_spot and moneyness == 95 else active_full_return
            )
            full_dd = spot_full_dd if is_spot and moneyness == 95 else active_full_dd
            if moneyness != 95 and is_spot:
                full_return = active_full_return + 0.001
                full_dd = active_full_dd + 0.001
            rows.append(
                {
                    "candidate": f"im12_core_put_{anchor}_m{moneyness:03d}",
                    "ann_return_full": full_return,
                    "max_dd_full": full_dd,
                    "ann_return_last_3y": full_return,
                    "max_dd_last_3y": full_dd,
                    "ann_return_last_1y": (
                        0.34 if is_spot and moneyness == 95 and spot_recent_better else 0.32
                    ),
                    "max_dd_last_1y": (
                        -0.055 if is_spot and moneyness == 95 and spot_recent_better else -0.061
                    ),
                }
            )
    return pd.DataFrame(rows)


def test_lower_full_return_same_drawdown_recent_better_keeps_default() -> None:
    decision, stability, detail = study.corrected_decision(decision_fixture())
    assert decision == "keep_default"
    assert stability == "recent_only"
    assert detail["full_return_not_worse"] is False
    assert detail["full_dd_strictly_better"] is False


def test_strict_full_improvement_with_neighbor_can_promote() -> None:
    fixture = decision_fixture(
        active_full_return=0.19,
        spot_full_return=0.191,
        active_full_dd=-0.125,
        spot_full_dd=-0.124,
    )
    decision, stability, _ = study.corrected_decision(fixture)
    assert decision == "promote_candidate"
    assert stability == "wide_stable"


def test_v1_formal_output_is_preserved_as_input() -> None:
    assert (study.V1_OUTPUT / "daily_candidates.csv.gz").exists()
    assert (study.V1_OUTPUT / "data_manifest.json").exists()
