from __future__ import annotations

import json
from datetime import date, datetime

import pytest

import run_ic_im_v1_2_github_digest as runner


def signal(product: str, day: date) -> dict[str, object]:
    return {
        "product": product,
        "market_date": day,
        "close_confirmed": True,
    }


def test_validate_close_artifact_accepts_exact_ledger_parity():
    day = date(2026, 8, 25)
    observed = {product: signal(product, day) for product in runner.PRODUCTS}
    latest = {
        "verified_day": day.isoformat(),
        "signals": runner._jsonable(observed),
    }

    runner.validate_close_artifact(
        completed_day=day,
        latest=latest,
        observed=observed,
    )


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (lambda values: values.pop("IM"), "同时包含IC和IM"),
        (
            lambda values: values["IM"].update(
                {"market_date": date(2026, 8, 22)}
            ),
            "信号日期不一致",
        ),
        (
            lambda values: values["IC"].update({"close_confirmed": False}),
            "不是收盘确认信号",
        ),
    ],
)
def test_validate_close_artifact_rejects_partial_or_unconfirmed(mutator, message):
    day = date(2026, 8, 25)
    observed = {product: signal(product, day) for product in runner.PRODUCTS}
    latest = {
        "verified_day": day.isoformat(),
        "signals": runner._jsonable(observed),
    }
    mutator(observed)

    with pytest.raises(RuntimeError, match=message):
        runner.validate_close_artifact(
            completed_day=day,
            latest=latest,
            observed=observed,
        )


def test_validate_close_artifact_rejects_report_ledger_divergence():
    day = date(2026, 8, 25)
    observed = {product: signal(product, day) for product in runner.PRODUCTS}
    latest = {
        "verified_day": day.isoformat(),
        "signals": runner._jsonable(observed),
    }
    latest["signals"]["IC"]["extra"] = "different"

    with pytest.raises(RuntimeError, match="逐腿记录不一致"):
        runner.validate_close_artifact(
            completed_day=day,
            latest=latest,
            observed=observed,
        )


def test_write_failure_creates_machine_readable_artifact(tmp_path):
    clock = datetime(2026, 8, 26, 17, 30, tzinfo=runner.strategy.BEIJING)
    runner.write_failure(tmp_path, clock, RuntimeError("source unavailable"))

    payload = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert payload["status"] == "failed"
    assert payload["error_type"] == "RuntimeError"
    assert payload["error"] == "source unavailable"
    assert "source unavailable" in (tmp_path / "failure.txt").read_text(
        encoding="utf-8"
    )
