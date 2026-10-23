from __future__ import annotations

import pandas as pd

import im_mo_2015_valuation_reconstruction_v13 as research


def test_spec_and_inputs_are_frozen() -> None:
    research.verify_inputs(require_fresh_output=False)


def test_certification_is_two_factor_lower_bound() -> None:
    pb = pd.Series([2.79, 2.80, 2.75, 2.725, 8.25])
    erp = pd.Series([0.005, 0.006, 0.0075, 0.00825, -0.027])
    assert research.certify_tier(pb, erp).tolist() == [2, 3, 2, 1, 3]


def test_user_2015_memory_is_confirmed_by_frozen_inputs() -> None:
    early, audit = research.build_early_valuation()
    june = early[early["date"].eq(research.PEAK_2015)].iloc[0]
    precrash = early[
        early["date"].between(research.PRECRASH_START, research.PEAK_2015)
    ]
    assert int(june["certified_tier"]) == 3
    assert precrash["certified_tier"].eq(3).all()
    assert audit["future_gov_rows"] == 0
    assert audit["max_close_relative_error"] == 0.0


def test_extended_schedule_changes_only_pre_cutover() -> None:
    early, _ = research.build_early_valuation()
    schedule, audit = research.build_extended_schedule(2, early)
    pre = schedule["eval_date"].lt(research.CUTOVER_EVAL)
    post = ~pre
    assert schedule.loc[pre, "binary_target_qty"].ge(
        schedule.loc[pre, "v12_target_qty"]
    ).all()
    assert schedule.loc[post, "binary_target_qty"].eq(
        schedule.loc[post, "v12_target_qty"]
    ).all()
    assert audit["post_cutover_target_errors"] == 0
    assert audit["first_positive_execution"] == "2015-04-16"
