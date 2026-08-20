import pandas as pd

import ic_510500_put_close_execution_full_retest_v17 as v17


def test_component_scope_is_frozen_and_unique() -> None:
    assert len(v17.COMPONENTS) == 13
    assert len(set(v17.COMPONENTS)) == 13
    assert "ic_510500_put_proxy_validation_v1" in v17.COMPONENTS
    assert "ic_510500_put_dynamic_lower_threshold_front95_v16" in v17.COMPONENTS


def test_real_execution_fields_are_replaced_with_close() -> None:
    frames = {
        "etf500": pd.DataFrame({"open": [1.0], "close": [2.0]}),
        "histories": pd.DataFrame({"open": [0.1], "close": [0.2]}),
        "ic": pd.DataFrame({"open": [3.0], "close": [4.0]}),
    }
    result = v17.transformed_frames(frames)
    assert result["etf500"].loc[0, "open"] == 2.0
    assert result["histories"].loc[0, "open"] == 0.2
    assert result["ic"].loc[0, "open"] == 3.0


def test_model_execution_state_is_replaced_with_close() -> None:
    market = pd.DataFrame(
        {
            "spot_open": [1.0],
            "spot_close": [2.0],
            "sigma_open": [0.1],
            "sigma_close": [0.2],
            "rate_open": [0.01],
            "rate_close": [0.02],
            "dividend_open": [0.03],
            "dividend_close": [0.04],
        }
    )
    result, checks = v17.transformed_market((market, {}))
    for prefix in ["spot", "sigma", "rate", "dividend"]:
        assert result.loc[0, f"{prefix}_open"] == result.loc[0, f"{prefix}_close"]
    assert "T+1 close" in checks["execution_state_override"]


def test_metrics_use_compounded_nav_and_drawdown() -> None:
    ann, max_dd = v17.metrics(pd.Series([0.10, -0.10]))
    assert ann < 0
    assert abs(max_dd + 0.10) < 1e-12

