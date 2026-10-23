from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime
import json
import copy
from pathlib import Path

import pytest

import poe_ic_im_mainline_v1_4_bot as strategy
import run_ic_im_v1_4_github_digest as digest


def _real_unpublished_result():
    fixture = Path(__file__).parent / "tests/fixtures/icim_fear_unpublished_20261014.json"
    return json.loads(fixture.read_text(encoding="utf-8"))


def _apply_previous_session_evidence(signals):
    # Approved re-evaluation of the frozen 14:28 source hash, not a new live quote.
    for signal in signals.values():
        # Synthetic request interval for this schema/publication regression;
        # no acquisition start was recorded in the original frozen report.
        signal["fear_fetch_started_at"] = signal["fear_retrieved_at"]
        signal.update(fear_greed_index=39.32, fear_data_date="2026-10-13",
                      fear_data_status=strategy.fear_grid.INTRADAY_FALLBACK_STATUS,
                      fear_intraday_policy_revision=strategy.fear_grid.INTRADAY_POLICY_REVISION,
                      fear_fallback_applied=True, fear_fallback_reason="same_day_not_published",
                      fear_previous_exchange_date="2026-10-13", fear_latest_available_date="2026-10-13",
                      fear_page_data_date="2026-10-13")


@pytest.mark.parametrize("published_fear", [False, True, "previous_session"])
def test_real_realtime_artifact_distinguishes_partial_from_complete(monkeypatch, tmp_path, published_fear):
    original = _real_unpublished_result()
    observed = copy.deepcopy(original["signals"])
    if published_fear:
        for signal in observed.values():
            signal.update(fear_greed_index=40.0, fear_data_date=signal["market_date"],
                          fear_data_status="intraday_provisional")
    if published_fear == "previous_session":
        _apply_previous_session_evidence(observed)
    before_signals = copy.deepcopy(observed)
    latest = {key: original[key] for key in ["sequence", "digest", "verified_day"]}

    class Store:
        def __init__(self, *_args):
            pass

        def load_latest(self):
            return latest

    class Coordinator:
        def __init__(self, *_args):
            pass

        def catch_up_until_current(self, *_args, **_kwargs):
            return 0

        def health(self, *_args):
            return {"status": "ok", "verified_day": latest["verified_day"]}

        def execute_query(self, *_args, **_kwargs):
            return "frozen formal-query fixture", [], observed

    monkeypatch.setattr(digest, "StateStore", Store)
    monkeypatch.setattr(digest, "LedgerCoordinator", Coordinator)
    result = digest.build_artifacts(
        state_dir=tmp_path / "isolated", out_dir=tmp_path / "out",
        clock=datetime.fromisoformat(original["generated_at"]), max_sessions=1,
        mode="realtime", expected_market_date=original["market_date"],
    )
    assert observed == before_signals
    if published_fear:
        assert result["status"] == "ok" and result["signals"] == observed
    else:
        assert result["status"] == "partial"
        assert set(result["incomplete_components"]) == {
            "IC.grid", "IC.total_futures", "IM.grid", "IM.total_futures"
        }
        for product in ("IC", "IM"):
            signal = result["signals"][product]
            assert signal["grid_target"] is None and signal["total_units_target"] is None
            assert signal["grid_action"] == "UNCONFIRMED"
            assert signal["core_units_target"] == observed[product]["core_units_target"]
    assert json.loads((tmp_path / "out/result.json").read_text(encoding="utf-8")) == result


@pytest.mark.parametrize("previous_session", [False, True])
def test_poe_report_does_not_print_unpublished_grid_as_zero_hold(monkeypatch, previous_session):
    original = _real_unpublished_result()
    if previous_session:
        _apply_previous_session_evidence(original["signals"])
    chunks = []

    class Capture:
        @contextmanager
        def start_message(self):
            class Message:
                def write(self, value):
                    chunks.append(str(value))
            yield Message()

    def frozen_signal(product, *_args):
        signal = copy.deepcopy(original["signals"][product])
        signal["market_date"] = date.fromisoformat(signal["market_date"])
        return signal

    monkeypatch.setattr(strategy, "poe", Capture())
    monkeypatch.setattr(strategy, "build_live_signal_with_transport_retry", frozen_signal)
    monkeypatch.setattr(strategy, "_SIGNAL_OBSERVER", None)
    strategy.ICIMMainlinesBot()._handle_signal(("IC", "IM"), "intraday")
    text = "".join(chunks)
    if previous_session:
        assert "**部分有效：" not in text
        assert text.count("下一交易日预估目标：0.5倍") == 2
        assert text.count("沿用上一交易日 2026-10-13（当日未发布；盘中预估）") == 2
        assert text.count("恐贪读数：**39.32**") == 2
        assert "数据日 `2026-10-14`；数据状态 `intraday_previous_session`" not in text
    else:
        assert text.count("**部分有效：") == 2
        assert text.count("| **期货合计** | **0.5倍** | **N/A**") == 2
        assert "下一交易日预估目标：0.5倍" not in text
    assert "恐贪读数：**None**" not in text
    assert "T+1指定日开盘" in text


def test_partial_cli_message_cannot_claim_digest_ok(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(digest, "build_artifacts", lambda **_kwargs: {
        "status": "partial", "publication_mode": "realtime",
        "verified_day": "2026-10-13", "sequence": 13, "build": strategy.BUILD_ID,
    })
    monkeypatch.setattr("sys.argv", ["digest", "--state-dir", str(tmp_path / "state"),
                                   "--out-dir", str(tmp_path / "out"), "--mode", "realtime"])
    assert digest.main() == 0
    text = capsys.readouterr().out
    assert "ic_im_digest_partial:" in text and "ic_im_digest_ok:" not in text


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


def test_real_20261012_ic_im_json_keeps_lifecycle_put_separate_from_protection_put():
    # Minimal projection of the saved successful close result from readiness run 38236987937.
    latest = {
        "verified_day": "2026-10-12",
        "sequence": 12,
        "digest": "b46787b277c91d80505c457c5d8cce7b0f6da430c9fccc1ad3be2abb8a4f9c66",
        "signals": {
            "IC": {
                "v14_core_put_contract": "510500P2612M07500",
                "v14_core_put_qty": 10,
                "v14_profit_reentry_status": None,
                "v14_lifecycle_evidence_status": "model_future_route_active",
                "put_target_contract": "510500P2612M07500",
                "put_target_core_qty": 10,
            },
            "IM": {
                "v14_core_put_contract": None,
                "v14_core_put_qty": 0.0,
                "v14_profit_reentry_status": None,
                "v14_lifecycle_evidence_status": "model_future_route_active",
                "core_put_target_contract": "MO2612-P-7600",
                "core_put_target_qty_normalized": 1.5,
            },
        },
    }
    report = digest.render_stored_close_report(latest)
    assert "- v1.4独立生命周期核心Put：510500P2612M07500；数量：10；" in report
    assert "- v1.4独立生命周期核心Put：N/A（当前无独立生命周期合约）；数量：0.0；" in report
    assert "- 普通核心保护Put目标：510500P2612M07500；数量：10（普通保护规则，独立于上列生命周期字段）" in report
    assert "- 普通核心保护Put目标：MO2612-P-7600；数量：1.5（普通保护规则，独立于上列生命周期字段）" in report
    assert "- v1.4核心Put：MO2612-P-7600；数量：0.0" not in report
    # Ordinary protection targets remain available in the per-leg JSON under their own fields.
    assert '"put_target_contract": "510500P2612M07500"' in report
    assert '"put_target_core_qty": 10' in report
    assert '"core_put_target_contract": "MO2612-P-7600"' in report
    assert '"core_put_target_qty_normalized": 1.5' in report


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
