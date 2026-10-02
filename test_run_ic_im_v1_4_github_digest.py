from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime
import json

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


def test_fix9_attachment_explains_original_identity_only_plan_without_rewriting_it():
    identity_plan = {
        "product": "IC", "signal_day": "2026-09-29", "execution_day": "2026-09-30",
        "legs": {
            "core": {
                "changed": True, "old_contract": None, "old_security_id": None,
                "old_qty": 0, "new_contract": "510500P2612M07500",
                "new_security_id": "10012099", "new_qty": 10,
            },
            "momentum": {
                "changed": False, "old_contract": None, "old_security_id": None,
                "old_qty": 0, "new_contract": None, "new_security_id": None,
                "new_qty": 0,
            },
        },
    }
    signal = {
        "product": "IC", "market_date": "2026-09-29", "next_trade_date": "2026-09-30",
        "put_current_contract": "510500P2612M07500", "put_current_core_qty": 10,
        "put_current_momentum_qty": 0, "put_target_contract": "510500P2612M07500",
        "put_target_security_id": "10012099", "put_target_core_qty": 10,
        "put_target_momentum_qty": 0, "v14_ordinary_put_plan_status": "scheduled_t_plus_1_open",
        "v14_ordinary_put_pending": identity_plan,
    }
    report = digest.render_stored_close_report({
        "verified_day": "2026-09-29", "sequence": 8, "digest": "d" * 64,
        "signals": {"IC": signal, "IM": {}},
    })
    assert "不代表下一交易日开仓" in report
    assert '"new_qty": 10' in report
    assert '"v14_ordinary_put_plan_status": "scheduled_t_plus_1_open"' in report


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


def test_delivery_identity_preserves_fix7_then_uses_fix9():
    assert digest.delivery_revision_for_signal_day(date(2026, 9, 25)) == digest.FIX4_DELIVERY_REVISION
    assert digest.delivery_revision_for_signal_day(date(2026, 9, 26)) == digest.FIX6_DELIVERY_REVISION
    assert "coreput3x-open-fix7" in digest.delivery_revision_for_signal_day(date(2026, 9, 28))
    assert "coreput3x-open-fix7" in strategy.v14_policy.identity_for_signal_day(date(2026, 9, 28))[0]
    assert "ic-seller-mom120-fix9" in digest.delivery_revision_for_signal_day(date(2026, 9, 29))
    assert "ic-seller-mom120-fix9" in strategy.v14_policy.FIX9_BUILD_ID
    assert "fear-grid25-50-fix10" in strategy.v14_policy.FIX10_BUILD_ID
    assert "ic-csi500-abs40-fix11" in strategy.BUILD_ID
    assert digest.delivery_revision_for_signal_day(date(2026, 10, 8)) == digest.DELIVERY_REVISION
    assert strategy.v14_policy.FIX6_BUILD_ID.endswith("-fix6-nocall-repeatroll-iciv30-qdelta05")


def test_realtime_report_build_follows_signal_day_not_latest_module_build():
    sep28 = datetime(2026, 9, 28, 14, 40, tzinfo=strategy.BEIJING)
    sep29 = datetime(2026, 9, 29, 14, 40, tzinfo=strategy.BEIJING)
    assert strategy._report_build_id("intraday", sep28) == strategy.v14_policy.FIX7_BUILD_ID
    assert strategy._report_build_id("intraday", sep29) == strategy.v14_policy.FIX9_BUILD_ID


def test_failure_artifact_keeps_signal_day_for_transition_gate(tmp_path):
    digest.write_failure(tmp_path, datetime(2026, 9, 29, 8, tzinfo=strategy.BEIJING),
                         RuntimeError('diagnostic'), expected_market_date='2026-09-28')
    payload = json.loads((tmp_path / 'result.json').read_text(encoding='utf-8'))
    assert payload['status'] == 'failed'
    assert payload['market_date'] == '2026-09-28'
    assert payload['build'] == strategy.v14_policy.FIX7_BUILD_ID
    assert payload['delivery_revision'] == digest.FIX7_DELIVERY_REVISION


def test_digest_budget_override_is_scoped_and_preserves_request_deadline():
    assert strategy._signal_product_network_budget(2) == 45.0
    with strategy.signal_product_budget_override(180.0):
        assert strategy._signal_product_network_budget(2) == 180.0
        with strategy._network_budget(1.0):
            assert 0 < strategy._bounded_timeout(8.0) <= 1.0
    assert strategy._signal_product_network_budget(2) == 45.0
