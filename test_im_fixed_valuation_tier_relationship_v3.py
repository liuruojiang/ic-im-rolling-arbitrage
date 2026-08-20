from __future__ import annotations

import numpy as np

import ic_fixed_valuation_unbounded_score_v6 as ic_v6
import im_fixed_valuation_tier_relationship_v3 as study


def load_case():
    inputs = study.load_inputs()
    daily = study.add_tier_states(inputs["daily"])
    monthly = study.add_tier_states(inputs["monthly"])
    agreement = study.agreement_summary(daily, monthly)
    confusion = study.confusion_matrix_table(daily, monthly)
    events = study.cumulative_event_audit(monthly)
    context = ic_v6.make_price_context(daily)
    long, wide = study.make_scan(daily, monthly, context)
    decision = study.select_relationship(agreement, events, long)
    return (
        inputs,
        daily,
        monthly,
        agreement,
        confusion,
        events,
        long,
        wide,
        decision,
    )


def test_frozen_spec_inputs_and_v2_manifest() -> None:
    hashes = study.verify_frozen_inputs(require_fresh_output=False)
    assert study.sha256(study.SPEC) == study.SPEC_HASH
    assert len(hashes) == len(study.INPUT_HASHES)


def test_fixed_tier_boundaries() -> None:
    scores = study.score_to_tier(
        np_to_series([2.449999, 2.45, 2.499999, 2.50, 2.599999, 2.60])
    )
    assert scores.tolist() == [0, 1, 1, 2, 2, 3]
    definition = study.tier_definition()
    assert definition["tier"].tolist() == [0, 1, 2, 3]


def np_to_series(values):
    import pandas as pd

    return pd.Series(values, dtype=float)


def test_tier_formulas_and_two_of_three_equivalence() -> None:
    _inputs, daily, _monthly, *_rest = load_case()
    assert (
        daily["consensus_min_tier"]
        == daily[["mean_tier", "median_tier"]].min(axis=1)
    ).all()
    assert (
        daily["either_max_tier"]
        == daily[["mean_tier", "median_tier"]].max(axis=1)
    ).all()
    for level, threshold in enumerate(study.TIER_THRESHOLDS, start=1):
        count = (
            daily["pb_aggregate"].ge(1.50 + 0.50 * threshold).astype(int)
            + daily["erp"].le(0.045 - 0.015 * threshold).astype(int)
            + daily["trailing_dividend_contribution"]
            .le(0.030 - 0.010 * threshold)
            .astype(int)
        )
        assert np.array_equal(daily["median_tier"].ge(level), count.ge(2))


def test_agreement_confusion_and_event_shapes() -> None:
    (
        _inputs,
        daily,
        monthly,
        agreement,
        confusion,
        events,
        *_rest,
    ) = load_case()
    assert len(agreement) == 4
    assert len(confusion) == 64
    assert len(events) == 12
    assert confusion[
        (confusion["frequency"] == "daily")
        & (confusion["segment"] == "full")
    ]["rows"].sum() == len(daily)
    assert confusion[
        (confusion["frequency"] == "monthly")
        & (confusion["segment"] == "full")
    ]["rows"].sum() == len(monthly)


def test_scan_shape_selection_and_integrity() -> None:
    (
        inputs,
        daily,
        monthly,
        agreement,
        confusion,
        events,
        long,
        wide,
        decision,
    ) = load_case()
    vintages = study.vintage_invariance(monthly)
    checks = study.integrity_checks(
        daily,
        monthly,
        inputs,
        agreement,
        confusion,
        events,
        long,
        wide,
        vintages,
        decision,
    )
    assert len(long) == 20
    assert len(wide) == 4
    assert checks["all_checks_passed"] is True
    assert decision["selection_uses_strategy_outcomes"] is False


def test_formal_output_manifest_when_present() -> None:
    if not study.OUTPUT.exists():
        return
    assert (study.OUTPUT / "output_manifest.json").exists()
    assert (study.OUTPUT / "decision_summary.json").exists()
    assert (study.OUTPUT / "integrity_checks.json").exists()
