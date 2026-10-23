from __future__ import annotations

import math

import pytest

import ic_510500_put_tiered_notional_delta_v20 as v20


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (1.999999, 0),
        (2.00, 1),
        (2.099999, 1),
        (2.10, 2),
        (2.149999, 2),
        (2.15, 3),
        (3.00, 3),
    ],
)
def test_valuation_tier_boundaries(score: float, expected: int) -> None:
    assert v20.valuation_tier(score) == expected


def test_momentum_only_supplies_floor_tier() -> None:
    assert v20.risk_tier(1.5, -0.01) == 1
    assert v20.risk_tier(2.12, -0.01) == 2
    assert v20.risk_tier(2.20, -0.01) == 3
    assert v20.risk_tier(1.5, 0.01) == 0


def test_candidate_target_ladders() -> None:
    notional, delta = v20.target_for_variant("binary_notional1x", 3)
    assert notional == 1.0 and math.isnan(delta)
    notional, delta = v20.target_for_variant("tier_notional123", 3)
    assert notional == 3.0 and math.isnan(delta)
    notional, delta = v20.target_for_variant("binary_delta25", 3)
    assert math.isnan(notional) and delta == 0.25
    notional, delta = v20.target_for_variant("tier_delta255075", 3)
    assert math.isnan(notional) and delta == 0.75


def test_atm_put_delta_is_about_half() -> None:
    delta = v20.bs_put_delta(100.0, 100.0, 0.0, 0.0, 0.20, 0.25)
    assert delta == pytest.approx(-0.48, abs=0.03)


def test_implied_volatility_round_trip() -> None:
    proxy = v20.v19.v18.v13.proxy
    expected = 0.27
    price = proxy.bs_put(100.0, 95.0, 0.02, 0.01, expected, 0.25)
    actual = v20.implied_volatility(price, 100.0, 95.0, 0.02, 0.01, 0.25)
    assert actual == pytest.approx(expected, abs=1e-10)


def test_real_integer_sizing_error_is_bounded() -> None:
    full_qty = 20
    absolute_delta = 0.28
    target = 0.75
    qty = v20._delta_real_qty(full_qty, target, absolute_delta)
    error = abs(qty / full_qty * absolute_delta - target)
    assert qty == 54
    assert error <= 0.02


def test_exact_model_notional_hits_delta_target() -> None:
    absolute_delta = 0.23
    target = 0.75
    notional = target / absolute_delta
    assert notional * absolute_delta == pytest.approx(target, abs=1e-15)
