from __future__ import annotations

import json
from datetime import date, datetime

import pytest

import run_ic_im_v1_2_github_digest as runner


def signal(
    product: str, day: date, *, close_confirmed: bool = True
) -> dict[str, object]:
    return {
        "product": product,
        "market_date": day,
        "close_confirmed": close_confirmed,
        "market_phase": "收盘后" if close_confirmed else "盘中",
        "state_anchor_day": day if close_confirmed else day.replace(day=day.day - 1),
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


def test_render_stored_close_report_uses_verified_ledger_without_refetch():
    day = date(2026, 8, 25)
    observed = {product: signal(product, day) for product in runner.PRODUCTS}
    report = runner.render_stored_close_report(
        {
            "verified_day": day.isoformat(),
            "sequence": 1,
            "digest": "abcdef",
            "signals": observed,
        }
    )
    assert "直接来自已通过SHA-256日志链校验" in report
    assert "## IC" in report
    assert "## IM" in report
    assert '"close_confirmed": true' in report


def test_validate_realtime_artifact_accepts_complete_non_mutating_snapshot():
    clock = datetime(2026, 8, 26, 14, 20, tzinfo=runner.strategy.BEIJING)
    completed = date(2026, 8, 25)
    observed = {
        product: signal(product, clock.date(), close_confirmed=False)
        for product in runner.PRODUCTS
    }
    before = {
        "verified_day": completed.isoformat(),
        "sequence": 1,
        "digest": "abcdef",
    }

    runner.validate_realtime_artifact(
        clock=clock,
        completed_day=completed,
        before=before,
        after=dict(before),
        observed=observed,
    )


@pytest.mark.parametrize(
    ("clock", "mutator", "message"),
    [
        (
            datetime(2026, 8, 26, 15, 5, tzinfo=runner.strategy.BEIJING),
            lambda before, after, observed: None,
            "连续交易时段",
        ),
        (
            datetime(2026, 8, 26, 14, 20, tzinfo=runner.strategy.BEIJING),
            lambda before, after, observed: observed["IC"].update(
                close_confirmed=True
            ),
            "错误标记为收盘确认",
        ),
        (
            datetime(2026, 8, 26, 14, 20, tzinfo=runner.strategy.BEIJING),
            lambda before, after, observed: after.update(digest="changed"),
            "不得改写",
        ),
    ],
)
def test_validate_realtime_artifact_rejects_unsafe_snapshot(
    clock, mutator, message
):
    completed = date(2026, 8, 25)
    observed = {
        product: signal(product, date(2026, 8, 26), close_confirmed=False)
        for product in runner.PRODUCTS
    }
    before = {
        "verified_day": completed.isoformat(),
        "sequence": 1,
        "digest": "abcdef",
    }
    after = dict(before)
    mutator(before, after, observed)

    with pytest.raises(RuntimeError, match=message):
        runner.validate_realtime_artifact(
            clock=clock,
            completed_day=completed,
            before=before,
            after=after,
            observed=observed,
        )


def test_write_failure_creates_machine_readable_artifact(tmp_path):
    clock = datetime(2026, 8, 26, 17, 30, tzinfo=runner.strategy.BEIJING)
    runner.write_failure(
        tmp_path, clock, RuntimeError("source unavailable"), mode="realtime"
    )

    payload = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert payload["status"] == "failed"
    assert payload["error_type"] == "RuntimeError"
    assert payload["error"] == "source unavailable"
    assert payload["publication_mode"] == "realtime"
    assert "source unavailable" in (tmp_path / "failure.txt").read_text(
        encoding="utf-8"
    )
