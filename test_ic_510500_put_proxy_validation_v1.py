from pathlib import Path

import pandas as pd
import pytest

import ic_510500_put_proxy_validation_v1 as research


def test_frozen_spec_hash() -> None:
    assert research.sha256(Path(research.SPEC)) == research.SPEC_HASH


def test_black_scholes_put_and_expiry() -> None:
    assert research.bs_put(80.0, 100.0, 0.02, 0.01, 0.25, 0.0) == pytest.approx(20.0)
    price = research.bs_put(100.0, 90.0, 0.02, 0.01, 0.25, 0.25)
    assert 0.0 < price < 90.0


def test_fourth_wednesday_and_model_months() -> None:
    dates = pd.bdate_range("2024-01-01", "2024-12-31")
    assert research.fourth_wednesday(pd.Timestamp("2024-02-01"), dates) == pd.Timestamp("2024-02-28")
    months = research.model_listed_months(pd.Timestamp("2024-01-02"), dates)
    assert months == list(pd.to_datetime(["2024-01-01", "2024-02-01", "2024-03-01", "2024-06-01"]))


def test_metrics_known_path() -> None:
    result = research.metrics(pd.Series([0.10, -0.10]))
    assert result["total_return"] == pytest.approx(-0.01)
    assert result["max_dd"] == pytest.approx(-0.10)


def test_candidate_grid_is_complete() -> None:
    assert len(research.REAL_CANDIDATES) == 18
    assert len(research.MODEL_CANDIDATES) == 54
    assert len(set(research.REAL_CANDIDATES + research.MODEL_CANDIDATES)) == 72

