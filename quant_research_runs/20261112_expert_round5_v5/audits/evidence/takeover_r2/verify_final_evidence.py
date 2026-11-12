"""Independent read-only acceptance of r2 raw artifacts; no tests rerun."""
import ast
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
RUN = HERE.parents[2]
ROOT = RUN.parents[1]
sys.path.insert(0, str(ROOT))
from icim_delivery_test_evidence import validate_run
from _pytest.junitxml import mangle_test_address

def load(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def drift(mapping):
    return {name: {"expected": expected, "actual": sha(ROOT / name) if (ROOT / name).is_file() else None}
            for name, expected in mapping.items()
            if not (ROOT / name).is_file() or sha(ROOT / name) != expected}

class CollapseChangedNestedCollector(ast.NodeTransformer):
    def __init__(self):
        self.replacements = 0
    def visit_FunctionDef(self, node):
        if node.name == "collect_listing_result":
            self.replacements += 1
            node.body = [ast.Pass()]
            return node
        return self.generic_visit(node)

def main():
    pin_path = RUN / "candidate_source_pin_r2.json"
    assert sha(pin_path) == "05ae4d5a238ae619b90cef7755085a1ec55043da32bba8571ad95601944dc0b6"
    pin, initial = load(pin_path), load(RUN / "initial_source_pin.json")
    full = RUN / "takeover_full_candidate_r2"
    outer, runner, network = (load(full / name) for name in
                              ["candidate_result.json", "test_run.json", "network_guard.json"])
    before, after = (load(full / f"source_{side}.json") for side in ["before", "after"])
    assert before == after == pin["source"] and len(before) == 213
    assert not drift(pin["source"])
    counts = validate_run(full / "regression.xml", process_exit_code=outer["exit_code"],
                          source_before=before, source_after=after)
    assert counts == outer["counts"] == {"tests": 1571, "failures": 0, "errors": 0, "skipped": 0}
    assert runner["exit_code"] == network["worker_exit_code"] == outer["exit_code"] == 0
    assert network["external_connection_attempts"] == [] and network["real_nonlocal_connections"] == 0
    collection = load(full / "collection.json")
    for key in ["collected", "selected", "deselected", "executed"]:
        assert isinstance(collection[key], list) and len(collection[key]) == len(set(collection[key]))
    collected, selected, deselected, executed = (set(collection[key]) for key in
                                               ["collected", "selected", "deselected", "executed"])
    manifest = load(ROOT / "docs/ic_im_fear_source_round2_20261109_manifest.json")
    assert selected.isdisjoint(deselected) and selected | deselected == collected and selected == executed
    assert deselected == set(manifest["daily_gate_deselections"]) and len(deselected) == 5
    actual_xml = [(x.get("classname"), x.get("name")) for x in
                  ET.parse(full / "regression.xml").getroot().findall(".//testcase")]
    expected_xml = set()
    for nodeid in executed:
        names = mangle_test_address(nodeid)
        expected_xml.add((".".join(names[:-1]), names[-1]))
    assert len(actual_xml) == len(set(actual_xml)) == len(expected_xml) == len(executed) == 1571
    assert set(actual_xml) == expected_xml
    root_nodes = sorted(node for node in executed if node.startswith("test_icim_expert_round5_20261112.py::"))
    assert len(root_nodes) == pin["new_durable_cases"] == 39
    args = runner["files"]
    files = [arg for arg in args if arg.endswith(".py")]
    old_files = load(RUN / "full_candidate_r3_system_temp/test_run.json")["files"]
    assert args == old_files and len(files) == len(set(files)) == 56
    requested_exclusions = {args[i+1] for i, arg in enumerate(args) if arg == "--deselect"}
    assert requested_exclusions == deselected
    executable = ast.parse((RUN / "run_checks.py").read_text(encoding="utf-8"))
    timed_waits = [n for n in ast.walk(executable) if isinstance(n, ast.Call)
                   and isinstance(n.func, ast.Attribute) and n.func.attr == "wait"
                   and any(k.arg == "timeout" and isinstance(k.value, ast.Constant) and k.value.value == 60
                           for k in n.keywords)]
    assert len(timed_waits) == 1
    assert not drift(initial["protected"]) and not drift(initial["runtime"])
    latest = load(ROOT / "runtime/ic_im_v1_4_r1/latest.json")
    assert latest["sequence"] == 34 and latest["verified_day"] == "2026-11-11"

    old_adapter_path = RUN / "takeover_backup_r1/ic_im_evidence_adapter_v1.py"
    new_adapter_path = ROOT / "ic_im_evidence_adapter_v1.py"
    old_tree, new_tree = (ast.parse(path.read_text(encoding="utf-8-sig")) for path in
                          [old_adapter_path, new_adapter_path])
    old_collapse, new_collapse = CollapseChangedNestedCollector(), CollapseChangedNestedCollector()
    assert ast.dump(old_collapse.visit(deepcopy(old_tree)), include_attributes=False) == ast.dump(
        new_collapse.visit(deepcopy(new_tree)), include_attributes=False)
    assert old_collapse.replacements == new_collapse.replacements == 1
    keyed = next(x for x in new_tree.body if isinstance(x, ast.FunctionDef) and x.name == "_keyed")
    adapted = next(x for x in keyed.body if isinstance(x, ast.FunctionDef) and x.name == "adapted")
    gated = next(x for x in adapted.body if isinstance(x, ast.If) and "LISTING_EFFECTIVE_SIGNAL_DATE" in ast.unparse(x.test))
    assert ast.unparse(gated.test) == "role == 'ic.months' and day >= LISTING_EFFECTIVE_SIGNAL_DATE and hasattr(strategy, '_IC_CANDIDATE_LISTING_EFFECTIVE_SIGNAL_DATE')"
    source_literal = next(x.value for x in new_tree.body if isinstance(x, ast.Assign)
                          and any(isinstance(t, ast.Name) and t.id == "LISTING_EFFECTIVE_SIGNAL_DATE" for t in x.targets))
    assert ast.unparse(source_literal) == "date(2026, 11, 13)"
    # The changed function lies only in the capture-side else, not replay-side body.
    reader_if = gated.body[0]
    assert isinstance(reader_if, ast.If) and ast.unparse(reader_if.test) == "reader is not None"
    assert not any(isinstance(n, ast.FunctionDef) and n.name == "collect_listing_result"
                   for statement in reader_if.body for n in ast.walk(statement))
    assert any(isinstance(n, ast.FunctionDef) and n.name == "collect_listing_result" for n in reader_if.orelse)
    real = load(RUN / "real_validation/verification.json")
    selection = load(RUN / "real_validation/selection.json")
    assert real["new_market_computation"] is False and real["new_network_requests"] == 0
    assert real["formal_ledger_write"] is False
    assert real["status"] == "passed_explicit_business_comparison_with_execution_clock_changes_retained"
    assert all(sha(ROOT / name) == expected for name, expected in real["raw_artifact_sha256"].items())
    assert sum(v["business_fields_identical"] for v in real["comparisons"].values()) == 431
    assert selection["status"] == "passed" and selection["market_day"] == "2026-10-22"
    assert selection["selected"] == selection["frozen_v4_selected"]
    assert selection["network_requests"] == selection["unrecorded_inputs"] == []
    assert selection["synthetic_market_inputs"] is False and selection["formal_ledger_write"] is False
    assert sha(ROOT / "quant_research_runs/20261112_expert_round4_v4/real_previous/verification.json") == selection["reference_sha256"]
    assert not drift(pin["source"]) and not drift(initial["protected"]) and not drift(initial["runtime"])
    artifacts = ["candidate_result.json", "regression.xml", "regression.txt", "source_before.json",
                 "source_after.json", "test_run.json", "collection.json", "network_guard.json", "process_output.txt"]
    result = {"status": "accepted_final_r2_raw_test_evidence", "created_at": datetime.now().astimezone().isoformat(),
              "execution_actor": "/root/evidence_takeover", "candidate_pin_sha256": sha(pin_path),
              "main_sha256": pin["main_sha256"], "source_files": 213, "inventory_files": 56,
              "counts": counts, "original_deselections": sorted(deselected), "root_durable_tests": root_nodes,
              "root_durable_test_count": 39, "watchdog_seconds": 60,
              "test_execution_repeated_by_auditor": False, "protected_files_unchanged": 2347,
              "runtime_files_unchanged": 38, "formal_latest": initial["formal_latest"],
              "raw_artifact_sha256": {name: sha(full / name) for name in artifacts},
              "real_r1_artifact_reuse": {"new_r2_market_replay": False,
                  "exact_AST_change": "Only nested collect_listing_result inside new-day ic.months capture branch",
                  "effective_signal_date": "2026-11-13", "real_market_day": "2026-10-22",
                  "old_date_and_replay_paths_AST_unchanged": True, "business_fields_equal": 431,
                  "verification_sha256": sha(RUN / "real_validation/verification.json"),
                  "selection_sha256": sha(RUN / "real_validation/selection.json"), "selected": selection["selected"]},
              "remaining": ["Root final report, diff, protection check and final verification",
                            "Registry append must be disclosed separately from protected bytes"],
              "production_release_certificate": False, "verifier_sha256": sha(Path(__file__))}
    with (HERE / "summary.json").open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps({key: result[key] for key in ["status", "counts", "inventory_files", "root_durable_test_count"]}))

if __name__ == "__main__":
    main()
