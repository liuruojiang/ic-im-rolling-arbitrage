import math

import im_high_vol_put_derisk_ablation_scan_v2 as target


def test_scale_shapes_are_monotone_and_floored() -> None:
    ivs = (0.35, 0.40, 0.50, 0.80)
    for shape in target.SHAPES:
        values = [target.scale_for_shape(iv, 0.35, shape) for iv in ivs]
        assert values[0] == 1.0
        assert all(left >= right for left, right in zip(values, values[1:]))
        assert min(values) >= (0.50 if shape == "inverse_f50" else 0.25)


def test_linear_shape_strength() -> None:
    assert math.isclose(target.scale_for_shape(0.40, 0.35, "linear5_f25"), 0.75)
    assert math.isclose(target.scale_for_shape(0.40, 0.35, "linear10_f25"), 0.50)
    assert target.scale_for_shape(0.50, 0.35, "linear10_f25") == 0.25


def test_candidate_names_are_unique() -> None:
    names = {
        target.candidate_name(mode, threshold, shape)
        for mode in ("derisk_keep_put", "replace_put_derisk")
        for threshold in target.THRESHOLDS
        for shape in target.SHAPES
    }
    assert len(names) == 30
    assert target.candidate_name("gate_only", 0.325, "none") == "gate_only__iv325__none"
