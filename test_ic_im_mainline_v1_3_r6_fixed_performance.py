from __future__ import annotations

import build_ic_im_mainline_v1_3_r6_fixed_performance as fixed


def test_fixed_v13_r6_reuses_frozen_ic_and_exact_im_replay() -> None:
    _ic, _im, metrics, comparison, validation = fixed.build()
    assert validation["IC_metrics_parity_max_abs_error"] <= 1e-12
    assert validation["IM_replay_metrics_parity_max_abs_error"] <= 1e-12
    assert validation["IM_momentum_target_vs_ledger_max_abs_error"] <= 1e-12
    assert validation["IM_min_cash_weight"] >= 0.0
    assert validation["IM_all_returns_finite"] is True
    assert validation["IM_date_unique_increasing"] is True
    assert comparison.iloc[1]["max_dd"] > comparison.iloc[0]["max_dd"]
    unavailable = metrics.loc[(metrics["product"] == "IM") & metrics["window"].isin(["5y", "10y"])]
    assert not unavailable["available"].any()
