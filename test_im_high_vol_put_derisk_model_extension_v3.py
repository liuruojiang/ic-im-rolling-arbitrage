import pandas as pd

import im_high_vol_put_derisk_model_extension_v3 as target


def test_stress_windows_are_ordered() -> None:
    for start, end in target.STRESS_WINDOWS.values():
        assert isinstance(start, pd.Timestamp)
        assert start < end


def test_proxy_candidate_grid_size() -> None:
    names = {
        target.candidate_name(mode, threshold, shape)
        for mode in ("derisk_keep_put", "replace_put_derisk")
        for threshold in target.THRESHOLDS
        for shape in target.SHAPES
    }
    assert len(names) == 18


def test_proxy_thresholds_match_real_platform() -> None:
    assert target.THRESHOLDS == (0.35, 0.375, 0.40)
