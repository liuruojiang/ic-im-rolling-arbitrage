from __future__ import annotations

from contextlib import contextmanager

import poe_ic_im_mainline_v1_4_bot as strategy
import run_ic_im_v1_4_github_digest as digest


def test_stored_close_report_uses_v14_identity():
    latest = {
        "verified_day": "2026-09-16",
        "sequence": 0,
        "digest": "diagnostic",
        "signals": {"IC": {}, "IM": {}},
    }
    report = digest.render_stored_close_report(latest)
    assert report.startswith("# IC / IM 1.4 收盘确认账本")
    assert strategy.BUILD_ID in report
    assert "IC / IM 1.3 收盘确认账本" not in report


def test_stored_close_report_renders_expiry_branches_visibly():
    latest = {
        "verified_day": "2026-09-18",
        "sequence": 1,
        "digest": "diagnostic",
        "signals": {
            "IC": {"v14_expiry_conditional_signal": {"expired_worthless": "future"}},
            "IM": {},
        },
    }
    report = digest.render_stored_close_report(latest)
    assert "卖Put到期条件信号" in report
    assert "价外失效" in report
    assert "实际账户操作由用户自行处理" in report


def test_parameter_copy_matches_half_unit_grid(monkeypatch):
    chunks: list[str] = []

    class Capture:
        @contextmanager
        def start_message(self):
            class Message:
                def write(self, value):
                    chunks.append(str(value))

            yield Message()

    monkeypatch.setattr(strategy, "poe", Capture())
    strategy.ICIMMainlinesBot()._handle_params(("IC", "IM"))
    text = "".join(chunks)
    assert "0或0.5倍独立网格" in text
    assert "IC 1.4" in text and "≤0.500 加0.5倍" in text
    assert "IM 1.4" in text and "≤1.60 加0.5倍" in text
    assert "3倍" in text
    assert "IV严格>30%" in text
    assert "q_delta05" in text
    assert "IV严格>35%" in text
    assert "q3" in text
    assert "自2026-09-26信号日起IM不再卖Call" in text
    assert "正式研究信号" in text
    assert "加1倍" not in text


def test_delivery_identity_is_fix7_profit3x_next_open():
    assert "coreput3x-open-fix7" in digest.DELIVERY_REVISION
    assert "coreput3x-open-fix7" in strategy.BUILD_ID
    assert strategy.v14_policy.FIX6_BUILD_ID.endswith("-fix6-nocall-repeatroll-iciv30-qdelta05")


def test_digest_budget_override_is_scoped_and_preserves_request_deadline():
    assert strategy._signal_product_network_budget(2) == 45.0
    with strategy.signal_product_budget_override(180.0):
        assert strategy._signal_product_network_budget(2) == 180.0
        with strategy._network_budget(1.0):
            assert 0 < strategy._bounded_timeout(8.0) <= 1.0
    assert strategy._signal_product_network_budget(2) == 45.0
