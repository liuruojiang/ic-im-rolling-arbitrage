from __future__ import annotations

import unittest
from pathlib import Path
import sys
import ast
import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ic-im-v1-4-daily-digest.yml"
sys.path.insert(0, str(ROOT / "scripts"))
from resolve_ic_im_v1_4_attempt import classify_attempt


class ICIMV14WorkflowTests(unittest.TestCase):
    def test_manual_ledger_recovery_cannot_enable_smtp(self) -> None:
        recovery = classify_attempt("workflow_dispatch", "true", "close_confirmed", "false", "false")
        self.assertEqual(recovery["smtp_enabled"], "false")
        steps = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["send"]["steps"]
        for name in ("Persist normal-send intent before SMTP", "Send Gmail", "Mark digest delivered"):
            with self.subTest(step=name):
                condition = next(s["if"] for s in steps if s.get("name") == name)
                self.assertIn("steps.attempt.outputs.smtp_enabled == 'true'", condition)
        promotion = next(s["if"] for s in steps if s.get("name") == "Upload successful ledger")
        self.assertIn("steps.verify_recovery.outcome == 'success'", promotion)

    def test_ledger_recovery_rejects_scheduled_realtime_and_correction_modes(self) -> None:
        for args in [("schedule", "true", "close_confirmed", "false", "false"),
                     ("workflow_dispatch", "true", "realtime", "false", "false"),
                     ("workflow_dispatch", "true", "close_confirmed", "true", "false"),
                     ("workflow_dispatch", "true", "close_confirmed", "false", "true"),
                     ("workflow_dispatch", "yes", "close_confirmed", "false", "false")]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                classify_attempt(*args)

    def test_ordinary_schedule_and_manual_delivery_keep_smtp_enabled(self) -> None:
        for event in ("schedule", "workflow_dispatch"):
            result = classify_attempt(event, "false", "close_confirmed", "false", "false")
            self.assertEqual(result["smtp_enabled"], "true")
            self.assertEqual(result["ledger_only"], "false")

    def test_workflow_uses_r1_migration_persistence_and_gmail_gate(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("name: IC IM v1.4-r1 Post-Close Digest", text)
        self.assertIn('- cron: "0 12 * * *"', text)
        self.assertIn('"$(date +%H)" -lt 20', text)
        self.assertIn('SCHEDULED_ATTEMPT: ${{ github.event_name == \'schedule\' || inputs.external_schedule == true }}', text)
        self.assertLess(text.index('Scheduled ICIM delivery is not due'), text.index('python automation/scripts/check_ic_im_v1_4_delivery.py'))
        self.assertNotIn('- cron: "3 5 * * *"', text)
        self.assertNotIn('- cron: "18 5 * * *"', text)
        self.assertNotIn('- cron: "33 5 * * *"', text)
        self.assertIn('group: ic-im-v1-4-r1-post-close-digest', text)
        self.assertIn("PLANNED_BJ: \"20:00 Asia/Shanghai\"", text)
        self.assertIn("inputs.publication_mode || 'close_confirmed'", text)
        self.assertIn("default: close_confirmed", text)
        self.assertIn("external_schedule:", text)
        self.assertIn("inputs.external_schedule != true", text)
        self.assertNotIn("github.event_name == 'workflow_dispatch' || steps.calendar.outputs.trade_day == 'true'", text)
        self.assertIn("restore_ic_im_v1_4_ledger.py", text)
        self.assertIn("--artifact-name ic-im-v1-4-r1-ledger", text)
        self.assertIn("restore_ic_im_v1_3_ledger.py", text)
        self.assertIn("--artifact-name ic-im-v1-3-r7-put-monthly-fix1-ledger", text)
        self.assertIn("--artifact-name ic-im-v1-3-r7-ledger", text)
        self.assertIn("--before-created-at 2026-09-07T00:00:00Z", text)
        self.assertIn("--required", text)
        self.assertIn("migrate_ic_im_v1_3_r7_to_v1_4_r1_state.py", text)
        self.assertIn("--source legacy-state", text)
        self.assertIn("--target state", text)
        self.assertIn("run_ic_im_v1_4_github_digest.py", text)
        self.assertIn("ref: dc81478f0552f9ac685588d79679f2258caaca19", text)
        self.assertIn("delivery_contract.CALENDAR_YEARS == strategy._OFFICIAL_EXCHANGE_CALENDAR_YEARS", text)
        self.assertIn("delivery_contract.EXCHANGE_CLOSURES == strategy._EXCHANGE_CLOSURES", text)
        self.assertIn("--network-budget-per-product 180", text)
        self.assertIn("ICIM_LEGULEGU_DAILY_SNAPSHOT: ${{ secrets.ICIM_LEGULEGU_DAILY_SNAPSHOT }}", text)
        self.assertIn("ICIM_CHINABOND_SNAPSHOT_FILE: strategy-artifacts/chinabond.json", text)
        self.assertIn("timeout --foreground 90s python -u strategy/ic_im_chinabond.py", text)
        self.assertIn('--expected-date "${{ steps.calendar.outputs.today }}"', text)
        self.assertIn("use disclosed frozen fallback", text)
        self.assertLess(text.index("python -u strategy/ic_im_chinabond.py"), text.index("python -u strategy/run_ic_im_v1_4_github_digest.py"))
        self.assertNotIn("ref: main", text)
        self.assertIn("strategy-artifacts/strategy-sha.txt", text)
        self.assertIn('strategy.BUILD_ID == "v1.4-20261008-r1-ic-csi500-abs40-fix11"', text)
        self.assertIn('strategy.MOMENTUM_RULES["IC"]["nav_decay_threshold"] == 0.06', text)
        self.assertIn('strategy.MOMENTUM_RULES["IC"]["nav_decay_scale"] == 0.5', text)
        self.assertIn('strategy.MOMENTUM_RULES["IM"][key]', text)
        self.assertIn('policy.RULE_REVISION == "ic_im_v1_4_ic_csi500_ma105_w16_abs40_static_20261008_v1"', text)
        self.assertIn('policy.NO_CALL_EFFECTIVE_SIGNAL_DATE.isoformat() == "2026-09-26"', text)
        self.assertIn('policy.PROFIT_OPEN_EFFECTIVE_SIGNAL_DATE.isoformat() == "2026-09-28"', text)
        self.assertIn('policy.ORDINARY_PUT_OPEN_EFFECTIVE_SIGNAL_DATE.isoformat() == "2026-09-29"', text)
        self.assertIn('policy.PROFIT_MULTIPLE == 3.0', text)
        self.assertIn('runner.DELIVERY_REVISION == "20261008-v14-ic-csi500-abs40-fix11"', text)
        self.assertNotIn("published v1.4 fix2", (ROOT / "scripts" / "build_ic_im_v1_4_digest.py").read_text(encoding="utf-8"))
        self.assertNotIn("published v1.4 fix2", (ROOT / "scripts" / "prepare_ic_im_v1_4_marker.py").read_text(encoding="utf-8"))
        self.assertIn('strategy.GRID_POLICY_REVISION == "ic_im_or_fear25_paired_exit50_20261008_v1"', text)
        self.assertIn('strategy.IM_EXECUTION_FIX_REVISION == "im_put_execution_guards_20260908_v2"', text)
        self.assertIn('strategy.IM_PUT_EXECUTION_REVISION == "im_monthly_reset_20260907_v1"', text)
        self.assertLess(text.index("Install strategy dependencies"), text.index("Verify IC/IM fix11 build and historical identities"))
        self.assertIn('strategy._latest_completed_exchange_day(clock)', text)
        self.assertIn("steps.calendar.outputs.completed_day", text)
        self.assertIn("steps.publication_mode.outputs.mode == 'realtime'", text)
        self.assertIn('ICIM_REQUIRE_MIGRATION: "1"', text)
        self.assertIn("ICIM_STATE_DIR: state", text)
        self.assertLess(text.index("Verify v1.4 ledger chain"), text.index("Run IC/IM signal"))
        self.assertIn("build_ic_im_v1_4_digest.py", text)
        self.assertIn("prepare_ic_im_v1_4_marker.py", text)
        self.assertIn("check_ic_im_v1_4_result_day_delivery.py", text)
        self.assertLess(text.index("Recheck delivery against actual signal day"), text.index("Persist normal-send intent before SMTP"))
        self.assertIn("steps.result_day_gate.outputs.should_send_result_day == 'true'", text)
        self.assertIn("state/migration_record.json", text)
        self.assertIn("name: ic-im-v1-4-r1-ledger", text)
        self.assertIn("steps.send_gmail.outcome == 'success'", text)
        self.assertIn("steps.build_digest.outcome == 'success'", text)
        self.assertIn("name: ic-im-v1-4-r1-post-close-digest", text)


class _WorkflowContext:
    """Missing GitHub context properties evaluate to an empty string."""
    def __init__(self, values=None):
        self.values = values or {}

    def __getattr__(self, name):
        value = self.values.get(name, {})
        return _WorkflowContext(value) if isinstance(value, dict) else value

    def __bool__(self):
        return bool(self.values)

    def __eq__(self, other):
        return (self.values or "") == other


def _workflow_step_enabled(name, context, *, healthy=True):
    """Execute real YAML conditions, including GitHub's implicit success()."""
    steps = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["send"]["steps"]
    expression = next(step for step in steps if step.get("name") == name).get("if", "true")
    expression = expression.replace("&&", " and ").replace("||", " or ")
    tree = ast.parse(expression, mode="eval")
    permitted = (ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.Compare, ast.Eq,
                 ast.NotEq, ast.Name, ast.Attribute, ast.Call, ast.Load, ast.Constant)
    assert all(isinstance(node, permitted) for node in ast.walk(tree))
    explicit_status = any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                          and node.func.id in {"always", "success", "failure", "cancelled"}
                          for node in ast.walk(tree))
    namespace = {"true": True, "false": False, "always": lambda: True,
                 "success": lambda: healthy, "failure": lambda: not healthy}
    namespace.update({key: _WorkflowContext(value) for key, value in context.items()})
    allowed = eval(compile(tree, "<actual ICIM workflow condition>", "eval"),
                   {"__builtins__": {}}, namespace)
    return bool(allowed) and (healthy or explicit_status)


def _healthy_delivery_context(*, ledger_only):
    outputs = classify_attempt("workflow_dispatch", str(ledger_only).lower(),
                               "close_confirmed", "false", "false")
    return {
        "github": {"event_name": "workflow_dispatch"},
        "inputs": {"external_schedule": False},
        "steps": {
            "attempt": {"outcome": "success", "outputs": outputs},
            "delivery_gate": {"outcome": "success", "outputs": {"should_send": "true"}},
            "calendar": {"outcome": "success", "outputs": {"trade_day": "true"}},
            "run_signal": {"outcome": "success", "outputs": {"exit_code": "0"}},
            "build_digest": {"outcome": "success"},
            "prepare_marker": {"outcome": "success"},
            "result_day_gate": {"outcome": "success", "outputs": {"should_send_result_day": "true"}},
            "verify_recovery": {"outcome": "success" if ledger_only else "skipped"},
            "send_intent": {"outcome": "success"},
            "send_gmail": {"outcome": "success"},
        },
    }


_SEND_BOUNDARIES = ("Persist normal-send intent before SMTP", "Send Gmail", "Mark digest delivered")


@pytest.mark.parametrize("ledger_only", [False, True])
def test_actual_yaml_healthy_normal_and_ledger_only_are_distinct(ledger_only):
    context = _healthy_delivery_context(ledger_only=ledger_only)
    assert _workflow_step_enabled("Upload successful ledger", context)
    for name in _SEND_BOUNDARIES:
        assert _workflow_step_enabled(name, context) is (not ledger_only)


@pytest.mark.parametrize("ledger_only", [False, True])
@pytest.mark.parametrize("missing", ["ledger_only", "smtp_enabled", "final_attempt", "all"])
def test_actual_yaml_missing_classifier_outputs_fail_closed(ledger_only, missing):
    context = _healthy_delivery_context(ledger_only=ledger_only)
    if missing == "all":
        context["steps"]["attempt"]["outputs"] = {}
    else:
        del context["steps"]["attempt"]["outputs"][missing]
    context["steps"]["verify_recovery"]["outcome"] = "skipped"
    assert not _workflow_step_enabled("Upload successful ledger", context)
    for name in _SEND_BOUNDARIES:
        assert not _workflow_step_enabled(name, context)


@pytest.mark.parametrize("ledger,smtp", [
    ("true", "true"), ("false", "false"), ("TRUE", "false"), ("false", "yes"),
    (True, "false"), ("false", True), (None, "true"), ("true", None),
])
def test_actual_yaml_contradictory_or_untyped_classifier_outputs_cannot_promote_or_send(ledger, smtp):
    context = _healthy_delivery_context(ledger_only=True)
    context["steps"]["attempt"]["outputs"].update(ledger_only=ledger, smtp_enabled=smtp)
    assert not _workflow_step_enabled("Upload successful ledger", context)
    for name in _SEND_BOUNDARIES:
        assert not _workflow_step_enabled(name, context)


@pytest.mark.parametrize("ledger_only", [False, True])
@pytest.mark.parametrize("outcome", ["failure", "cancelled", "skipped"])
def test_actual_yaml_classifier_not_success_cannot_promote_or_send(ledger_only, outcome):
    context = _healthy_delivery_context(ledger_only=ledger_only)
    context["steps"]["attempt"]["outcome"] = outcome
    # Also evaluate with a healthy job to challenge the explicit step guard.
    for healthy in (True, False):
        assert not _workflow_step_enabled("Upload successful ledger", context, healthy=healthy)
        for name in _SEND_BOUNDARIES:
            assert not _workflow_step_enabled(name, context, healthy=healthy)


@pytest.mark.parametrize("ledger_only", [False, True])
@pytest.mark.parametrize("phase", ["run_signal", "build_digest", "prepare_marker", "result_day_gate"])
def test_actual_yaml_failed_production_boundary_cannot_promote_or_emit_intent(ledger_only, phase):
    context = _healthy_delivery_context(ledger_only=ledger_only)
    context["steps"][phase]["outcome"] = "failure"
    if phase == "run_signal":
        context["steps"][phase]["outputs"]["exit_code"] = "1"
    # Causal later steps cannot succeed after a failure before intent.
    context["steps"]["send_intent"]["outcome"] = "skipped"
    context["steps"]["send_gmail"]["outcome"] = "skipped"
    assert not _workflow_step_enabled("Upload successful ledger", context, healthy=False)
    assert not _workflow_step_enabled("Persist normal-send intent before SMTP", context, healthy=False)
    assert not _workflow_step_enabled("Mark digest delivered", context, healthy=False)
    if ledger_only or phase != "run_signal":
        assert not _workflow_step_enabled("Send Gmail", context, healthy=False)


@pytest.mark.parametrize("outcome", ["failure", "cancelled", "skipped"])
def test_actual_yaml_ledger_only_requires_successful_recovery_verification(outcome):
    context = _healthy_delivery_context(ledger_only=True)
    context["steps"]["verify_recovery"]["outcome"] = outcome
    assert not _workflow_step_enabled("Upload successful ledger", context)
    for name in _SEND_BOUNDARIES:
        assert not _workflow_step_enabled(name, context)


def test_actual_yaml_missing_final_attempt_disables_failed_run_diagnostic_mail():
    context = _healthy_delivery_context(ledger_only=False)
    context["steps"]["run_signal"]["outputs"]["exit_code"] = "1"
    context["steps"]["send_intent"]["outcome"] = "skipped"
    del context["steps"]["attempt"]["outputs"]["final_attempt"]
    assert not _workflow_step_enabled("Send Gmail", context)
    assert not _workflow_step_enabled("Upload successful ledger", context)


if __name__ == "__main__":
    unittest.main()


