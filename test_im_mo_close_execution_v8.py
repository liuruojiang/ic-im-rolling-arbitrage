from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd

import im_mo_close_execution_v8 as v8


def test_spec_hash_and_sidecar() -> None:
    digest = hashlib.sha256(v8.SPEC.read_bytes()).hexdigest()
    assert digest == v8.SPEC_SHA256
    assert v8.SPEC_HASH_FILE.read_text(encoding="utf-8").split()[0] == digest


def test_grid_is_frozen_and_complete() -> None:
    definitions = v8.candidate_definitions()
    assert len(definitions) == 12
    assert definitions["candidate"].is_unique
    assert set(definitions["structure"]) == set(v8.STRUCTURES)
    assert set(definitions["moneyness"]) == set(v8.MONEYNESS)
    assert definitions["execution"].eq("t_plus_1_close").all()


def test_close_selector_uses_im_close_and_close_liquidity() -> None:
    day = pd.Timestamp("2024-01-02")
    month = pd.Timestamp("2024-03-01")
    options = pd.DataFrame(
        {
            "date": [day, day, day],
            "contract_month": [month, month, month],
            "contract": ["P90", "P95", "P100"],
            "strike": [900.0, 950.0, 1000.0],
            "open": [999.0, 999.0, 999.0],
            "close": [1.0, 2.0, 3.0],
            "volume": [10.0, 10.0, 10.0],
            "open_interest": [10.0, 10.0, 10.0],
        }
    )
    im_close = pd.Series([1000.0], index=[day])
    selected = v8.select_close_contract(options, im_close, day, month, 0.95)
    assert selected is not None
    assert selected["contract"] == "P95"
    assert selected["close"] == 2.0


def test_close_executability_does_not_use_open() -> None:
    assert v8.executable_close(pd.Series({"open": 350.0, "close": 12.2, "volume": 527}))
    assert not v8.executable_close(pd.Series({"open": 350.0, "close": 0.0, "volume": 527}))


def test_formal_output_integrity_when_present() -> None:
    if not v8.OUTPUT.exists():
        return
    manifest = pd.read_json(v8.OUTPUT / "decision_summary.json", typ="series")
    price = manifest["price_integrity"]
    assert price["max_close_price_error"] <= 1e-14
    assert price["special_used_price"] == 12.2
    assert price["special_raw_open"] == 350.0
    assert price["special_raw_close"] == 12.2
    daily = pd.read_csv(v8.OUTPUT / "daily_candidates.csv.gz")
    assert len(daily) > 0
    assert not daily[["ret", "cash_ret"]].isna().any().any()
    assert not daily.duplicated(["layer", "candidate", "date"]).any()


def test_frozen_dependencies_exist() -> None:
    for path in [v8.V6_DAILY, v8.V7_RECORD, v8.SPEC, v8.SPEC_HASH_FILE]:
        assert Path(path).exists()
