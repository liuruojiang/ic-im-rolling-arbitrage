from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd
import pytest

import ic_mainline_v1_2 as previous
import ic_mainline_v1_3 as candidate


def _ohlcv() -> pd.DataFrame:
    return pd.read_csv(candidate.CSI500_OHLCV_PATH, parse_dates=["date"])


def test_ic_v13_exactly_matches_current_a_share_csi500_execution_weight() -> None:
    schedule, audit = candidate.load_authoritative_local_state()
    assert audit["a_share_execution_weight_max_abs_error"] == 0.0
    assert audit["v1_2_execution_weight_changed_rows"] == 1114
    assert set(schedule["momentum_execution_weight"].unique()) == {0.0, 0.25, 0.5, 1.0}
    assert audit["call_nonzero_rows"] == 0


def test_ic_v13_nav_defense_has_one_session_execution_lag() -> None:
    schedule = candidate.build_momentum_schedule(_ohlcv())
    np.testing.assert_allclose(
        schedule["momentum_execution_weight"].iloc[1:],
        schedule["momentum_signal_target"].shift(1).iloc[1:],
        atol=1e-12,
        rtol=0.0,
    )
    defended = schedule["nav_decay_signal"] & schedule["base_momentum_signal_target"].gt(0.0)
    assert defended.any()
    np.testing.assert_allclose(
        schedule.loc[defended, "momentum_signal_target"],
        0.5 * schedule.loc[defended, "base_momentum_signal_target"],
        atol=1e-12,
        rtol=0.0,
    )


def test_ic_v13_preserves_non_momentum_parent_components() -> None:
    old, _ = previous.load_authoritative_local_state()
    new, _ = candidate.load_authoritative_local_state()
    for column in (
        "date",
        "core_ic_units",
        "grid_ic_units",
        "core_put_target_delta",
        "grid_put_target_delta",
        "call_target_contracts",
    ):
        pd.testing.assert_series_equal(new[column], old[column], check_names=False)


@pytest.mark.parametrize(
    "mutation, message",
    [
        (lambda frame: frame.assign(volume=-1.0), "volume"),
        (lambda frame: frame.assign(high=frame["low"] - 1.0), "OHLC"),
    ],
)
def test_ic_v13_rejects_invalid_ohlcv(mutation, message) -> None:
    with pytest.raises(ValueError, match=message):
        candidate.build_momentum_schedule(mutation(_ohlcv()))


@pytest.mark.parametrize(
    ("column", "bad_value"),
    [
        ("final_weight", np.nan),
        ("base_nav_for_dd", np.inf),
        ("base_dd_for_gate", np.nan),
    ],
)
def test_ic_full_history_authority_parity_rejects_nonfinite_oracle(
    monkeypatch, column, bad_value
) -> None:
    source = candidate._load_a_share_module()
    original = source.build_sleeve_curve

    def broken(*args, **kwargs):
        frame = original(*args, **kwargs).copy()
        frame.loc[frame.index[-1], column] = bad_value
        return frame

    source.build_sleeve_curve = broken
    monkeypatch.setattr(candidate, "_load_a_share_module", lambda: source)
    with pytest.raises(RuntimeError, match="non-finite"):
        candidate.load_authoritative_local_state()


def test_ic_v13_spec_hash_matches_sidecar() -> None:
    sidecar = candidate.SPEC_PATH.with_suffix(candidate.SPEC_PATH.suffix + ".sha256")
    assert hashlib.sha256(candidate.SPEC_PATH.read_bytes()).hexdigest() == sidecar.read_text(
        encoding="utf-8"
    ).split()[0]
