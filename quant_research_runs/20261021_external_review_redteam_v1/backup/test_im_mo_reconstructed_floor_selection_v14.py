from __future__ import annotations

import pandas as pd

import im_mo_reconstructed_floor_selection_v14 as research


def test_spec_inputs_and_scan_are_frozen() -> None:
    research.verify_inputs(require_fresh_output=False)


def test_candidate_grid_is_exact() -> None:
    definitions = research.candidate_definitions()
    assert definitions["candidate"].tolist() == [
        "no_put",
        "legacy_fixed175_or_mom120",
        "reconstructed_valmom_floor1",
        "reconstructed_valmom_floor2",
        "reconstructed_valmom_floor3",
    ]
    formal = definitions[definitions["selection_role"].eq("formal")]
    assert formal["floor_qty"].tolist() == [1, 2, 3]


def test_reconstructed_schedule_is_causal_and_post_cutover_identical() -> None:
    early = pd.read_csv(research.V13_EARLY, parse_dates=["date"])
    for floor in research.FLOORS:
        schedule, audit = research.build_schedule(floor, early)
        pre = schedule["eval_date"].lt(research.v13.CUTOVER_EVAL)
        post = ~pre
        assert schedule["execution_date"].gt(schedule["eval_date"]).all()
        assert schedule.loc[pre, "binary_target_qty"].ge(
            schedule.loc[pre, "v12_target_qty"]
        ).all()
        assert schedule.loc[post, "binary_target_qty"].eq(
            schedule.loc[post, "v12_target_qty"]
        ).all()
        assert audit["post_cutover_target_errors"] == 0
        assert audit["first_positive_execution"] == "2015-04-16"


def test_path_parity_detects_exact_and_changed_rows() -> None:
    left = pd.DataFrame(
        {"date": pd.to_datetime(["2020-01-01", "2020-01-02"]), "cash_ret": [0.0, 0.1]}
    )
    right = left.copy()
    assert research.path_parity(left, right, ["cash_ret"])["cash_ret"] == 0.0
    right.loc[1, "cash_ret"] += 0.01
    assert research.path_parity(left, right, ["cash_ret"])["cash_ret"] > 0.0
