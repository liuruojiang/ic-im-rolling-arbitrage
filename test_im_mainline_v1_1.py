import pandas as pd
import pytest

import im_mainline_v1_1 as v11


def test_manifest_records_only_selected_im_changes() -> None:
    manifest = v11.rule_manifest()
    assert manifest["research_start"] == "2015-04-16"
    rules = manifest["im"]
    assert rules["grid"]["entry_lte"] == 1.60
    assert rules["grid"]["exit_gte"] == 2.00
    assert rules["grid"]["put_covered"] is False
    assert rules["grid"]["call_covered"] is False
    assert rules["put"]["mom120_negative_floor_qty"] == 3
    assert rules["put"]["fourth_contract_source"] == "valuation_tier_4_only"
    assert rules["call"]["rescue_expiry_rule"] == "rescue_next_listed"


@pytest.mark.parametrize(
    ("absolute", "relative", "momentum", "expected"),
    [
        (0, 0, 0.01, 0),
        (2, 1, 0.01, 2),
        (0, 0, -0.01, 3),
        (2, 1, -0.01, 3),
        (3, 2, -0.01, 3),
        (0, 4, 0.01, 4),
        (3, 4, -0.01, 4),
        (0, 0, 0.0, 0),
        (0, 0, None, 0),
    ],
)
def test_put_target_semantics(absolute, relative, momentum, expected) -> None:
    assert v11.put_target_qty(absolute, relative, momentum) == expected


def test_grid_boundaries_are_inclusive() -> None:
    assert v11.grid_close_signal(1.60, False) == "buy_next_open"
    assert v11.grid_close_signal(1.61, False) == "none"
    assert v11.grid_close_signal(2.00, True) == "sell_next_open"
    assert v11.grid_close_signal(1.99, True) == "none"


def test_schedule_uses_t_plus_one_and_grid_does_not_expand_options() -> None:
    state = pd.DataFrame(
        {
            "date": pd.date_range("2024-01-02", periods=4, freq="B"),
            "valuation_score": [1.60, 1.70, 2.00, 2.10],
            "absolute_tier": [0, 0, 0, 0],
            "relative_tier": [0, 0, 4, 0],
            "valuation_tier": [0, 0, 4, 0],
            "momentum_120": [-0.01, -0.01, 0.01, 0.01],
        }
    )
    schedule = v11.build_target_schedule(state)
    assert schedule["put_signal_target_qty"].tolist() == [3, 3, 4, 0]
    assert schedule["put_execution_target_qty"].tolist() == [0, 3, 3, 4]
    assert schedule["grid_executed_at_open"].tolist() == [
        "none",
        "buy_next_open",
        "none",
        "sell_next_open",
    ]
    assert schedule["grid_held_eod"].tolist() == [False, True, True, False]
    assert schedule["put_covered_im_units"].eq(1.0).all()
    assert schedule["call_covered_im_units"].eq(1.0).all()
    assert schedule["grid_put_qty"].eq(0).all()
    assert schedule["grid_call_qty"].eq(0).all()


def test_rejects_inconsistent_valuation_tier() -> None:
    state = pd.DataFrame(
        {
            "date": ["2024-01-02"],
            "valuation_score": [1.70],
            "absolute_tier": [0],
            "relative_tier": [4],
            "valuation_tier": [3],
            "momentum_120": [0.01],
        }
    )
    with pytest.raises(ValueError, match="valuation_tier"):
        v11.build_target_schedule(state)
