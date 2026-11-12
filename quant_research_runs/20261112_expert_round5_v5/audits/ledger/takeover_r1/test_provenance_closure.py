"""Only the affected successful-failover CaptureSession/Reader paths."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[5]
CONTROL = ROOT / "quant_research_runs/20261112_expert_round5_v5/audits/ledger/r1/test_ledger_listing_outcome_routes.py"
spec = importlib.util.spec_from_file_location("ledger_r1_controls_for_provenance_closure", CONTROL)
controls = importlib.util.module_from_spec(spec)
spec.loader.exec_module(controls)
offline = controls.offline
bot, adapter, evidence = controls.bot, controls.adapter, controls.evidence


def assert_provenance(reader):
    value = reader.call("ic.months", {})
    adapter.validate_listing_outcome(value, controls.DAY)
    assert value["status"] == "available" and value["months"] == controls.LISTED
    assert len(value["failures"]) == 1
    failure = value["failures"][0]
    assert (failure["source"], failure["kind"], failure["error_type"]) == ("SSE", "transport", "Timeout")
    assert "controlled listing transport timeout" in failure["error"]


@pytest.mark.parametrize("route", ["entry", "roll"])
def test_actual_successful_sina_capture_and_policy_replay_preserve_failed_source(monkeypatch, tmp_path, route):
    anchor, _, clock, observed, calls, _ = controls.configure(monkeypatch, route, "sina_available")
    original_anchor = deepcopy(anchor)
    outer = bot._IC_LISTING_QUERY_CACHE.get()
    with evidence.capture_session(tmp_path, controls.DAY, clock, controls.metadata()) as session:
        live = bot.build_live_trade_signal("IC", clock, "close")
    assert session.context["complete"] and not session.context["failures"]
    reader = evidence.load_session(tmp_path, controls.DAY)
    assert_provenance(reader)
    before_replay = deepcopy(calls)
    again = controls.replay(reader, clock)
    assert {k: v for k, v in live.items() if k != "input_evidence_status"} == {
        k: v for k, v in again.items() if k != "input_evidence_status"}
    assert live["v14_action"] == ("ENTER_SHORT_PUT" if route == "entry" else "ROLL_SHORT_PUT")
    assert calls == before_replay and not reader.missing_inputs
    assert len(observed["requirements"]) == 2 and anchor == original_anchor
    assert bot._IC_LISTING_QUERY_CACHE.get() is outer
    controls.record("provenance_" + route, {"complete": True, "replay_equal": True,
        "action": live["v14_action"], "outcome": reader.call("ic.months", {})})
