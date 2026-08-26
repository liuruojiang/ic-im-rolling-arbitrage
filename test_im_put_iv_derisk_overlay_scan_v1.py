import math

import im_mo_csi1000_put_protection_battery_v6 as market_v6
import im_put_iv_derisk_overlay_scan_v1 as target


def test_implied_put_volatility_round_trip() -> None:
    expected = 0.32
    price = market_v6.proxy.bs_put(7000.0, 6650.0, 0.02, 0.015, expected, 90 / 365)
    actual = target.implied_put_volatility(
        price, 7000.0, 6650.0, 0.02, 0.015, 90 / 365
    )
    assert actual is not None
    assert math.isclose(actual, expected, rel_tol=0.0, abs_tol=1e-10)


def test_derisk_scale_is_continuous_and_floored() -> None:
    assert target.derisk_scale(0.30, 0.30, 0.50) == 1.0
    assert math.isclose(target.derisk_scale(0.40, 0.30, 0.50), 0.75)
    assert target.derisk_scale(0.80, 0.30, 0.50) == 0.50


def test_candidate_grid_is_complete() -> None:
    names = {
        target.candidate_name(threshold, floor)
        for threshold in target.THRESHOLDS
        for floor in target.MIN_CORE_SCALES
    }
    assert len(names) == 12
    assert "iv25_floor25" in names
    assert "iv40_floor75" in names
